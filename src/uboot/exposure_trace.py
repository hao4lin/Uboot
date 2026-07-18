"""Non-invasive atomic tracing of fixed-point external relations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Iterable

from uboot.candidate_graph import candidate_objects
from uboot.edge_response import EdgeResponseEngine, EngineUpdateEvent
from uboot.exposure import (
    DirectObjectLink,
    ExposureStateBuilder,
    FixedPointIndex,
    ObjectExposureState,
    ObjectPath2,
    affected_objects_for_candidate_nodes,
    compare_exposure_states,
)
from uboot.microtrace import RetargetObservation


class FixedPointInvariantViolation(RuntimeError):
    """Raised as soon as the start-snapshot fixed-point partition changes."""


@dataclass(frozen=True, slots=True)
class ExposureChangeEvent:
    atomic_index: int
    step_before: int
    step_after: int
    active_node_id: int | None
    slot_id: int | None
    slot_semantics: int | None
    old_target_raw_id: int | None
    new_target_raw_id: int | None
    source_object_id: str | None
    source_object_type: str | None
    source_member_id: int | None
    old_target_object_ids: tuple[str, ...]
    new_target_object_ids: tuple[str, ...]
    level0_internal_changed: bool
    level1_micro_exposure_changed: bool
    level2_object_link_changed: bool
    level3_two_hop_changed: bool
    exposed_slot_touch_count: int
    change_classes: tuple[str, ...]
    affected_object_ids: tuple[str, ...]
    affected_direct_links_before: tuple[DirectObjectLink, ...]
    affected_direct_links_after: tuple[DirectObjectLink, ...]
    affected_two_hop_paths_before: tuple[ObjectPath2, ...]
    affected_two_hop_paths_after: tuple[ObjectPath2, ...]
    gap_transfer_candidate: bool
    retarget_events: tuple[RetargetObservation, ...]


@dataclass(frozen=True, slots=True)
class ExposureStateRun:
    run_index: int
    start_atomic_index: int
    end_atomic_index: int
    start_step: int
    end_step: int
    atomic_update_count: int
    level1_hash: str
    level2_hash: str
    level3_hash: str
    micro_change_event_count: int
    direct_link_change_event_count: int
    two_hop_change_event_count: int


@dataclass(frozen=True, slots=True)
class ExposureTraceResult:
    start_snapshot_id: int
    end_snapshot_id: int
    start_step: int
    end_step: int
    index: FixedPointIndex
    root_object_ids: tuple[str, ...]
    start_state: ObjectExposureState
    end_state: ObjectExposureState
    events: tuple[ExposureChangeEvent, ...]
    state_runs: tuple[ExposureStateRun, ...]
    summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _TickObservation:
    active: EngineUpdateEvent
    retargets: tuple[RetargetObservation, ...]
    affected_candidate_nodes: tuple[int, ...]


class _ExposureTickCollector:
    def __init__(self, targets: list[list[int]]) -> None:
        self.targets = [list(row) for row in targets]
        self.begin_tick()

    def begin_tick(self) -> None:
        self.active: EngineUpdateEvent | None = None
        self.retargets: list[RetargetObservation] = []
        self.affected: set[int] = set()

    def __call__(self, event: EngineUpdateEvent) -> None:
        if event.event_kind == "active_attempt":
            self.active = event
            return
        assert event.target is not None
        self.affected.update((event.source, event.previous_target, event.target))
        self.affected.update(
            source
            for source, row in enumerate(self.targets)
            if event.source in row
        )
        self.targets[event.source][event.slot] = event.target
        self.affected.update(
            source
            for source, row in enumerate(self.targets)
            if event.source in row
        )
        self.retargets.append(
            RetargetObservation(
                event.event_kind,
                event.source,
                event.slot,
                event.slot + 1,
                event.previous_target,
                event.target,
            )
        )

    def finish_tick(self) -> _TickObservation:
        if self.active is None:
            raise RuntimeError("engine tick did not emit an active attempt")
        return _TickObservation(
            self.active, tuple(self.retargets), tuple(self.affected)
        )


def run_exposure_trace(
    engine: EdgeResponseEngine,
    *,
    root_pair: Iterable[int],
    root_triangle: Iterable[int],
    start_snapshot_id: int,
    end_snapshot_id: int,
    end_step: int,
    object_radius: int = 2,
    verification_full_recompute: bool = False,
) -> ExposureTraceResult:
    if end_snapshot_id <= start_snapshot_id or end_step <= engine.tick:
        raise ValueError("exposure trace requires a forward non-empty interval")
    if engine.update_observer is not None:
        raise ValueError("exposure trace requires an unused update observer")
    start_step = engine.tick
    graph = engine.candidate_graph()
    index = FixedPointIndex.from_candidate_graph(graph)
    pair = tuple(sorted(root_pair))
    triangle = tuple(sorted(root_triangle))
    pair_object = index.by_members.get(pair)
    triangle_object = index.by_members.get(triangle)
    if pair_object is None or pair_object.object_type != "pair":
        raise ValueError("root pair is not a start-snapshot fixed-point pair")
    if triangle_object is None or triangle_object.object_type != "triangle":
        raise ValueError("root triangle is not a start-snapshot fixed-point triangle")
    roots = (pair_object.object_id, triangle_object.object_id)
    builder = ExposureStateBuilder(
        engine.targets, index, roots, object_radius=object_radius
    )
    state = builder.state()
    start_state = state
    collector = _ExposureTickCollector(engine.targets)
    engine.update_observer = collector
    events: list[ExposureChangeEvent] = []
    runs: list[ExposureStateRun] = []
    counts: Counter[str] = Counter()
    changed_objects: dict[str, set[str]] = {
        "micro": set(),
        "direct": set(),
        "two_hop": set(),
    }
    previous_rewire_objects: set[str] | None = None
    current_run: ExposureStateRun | None = None
    try:
        while engine.tick < end_step:
            atomic_index = engine.tick - start_step + 1
            step_before = engine.tick
            collector.begin_tick()
            engine.step_tick()
            observed = collector.finish_tick()
            for node in observed.affected_candidate_nodes:
                graph[node] = engine.direct_candidates(node)
            validate_fixed_point_invariant(index, graph, step=engine.tick)
            affected_objects = affected_objects_for_candidate_nodes(
                index, observed.affected_candidate_nodes
            )
            next_state = builder.update(engine.targets, affected_objects)
            if verification_full_recompute:
                full = builder.full_recompute(engine.targets)
                if full != next_state:
                    raise RuntimeError(
                        f"incremental exposure state diverged at step {engine.tick}"
                    )
                counts["full_recompute_verification_count"] += 1
            delta = compare_exposure_states(state, next_state)
            exposed_touch_count = _exposed_touch_count(
                observed.retargets, state, next_state
            )
            classes = delta.change_classes
            affected = set(delta.affected_object_ids) | set(affected_objects)
            if delta.level2_object_link_changed:
                if previous_rewire_objects is not None and (
                    previous_rewire_objects & affected
                ):
                    classes = (*classes, "external_rewire_chain")
                    counts["external_rewire_chain_count"] += 1
                previous_rewire_objects = affected
            event = _change_event(
                index,
                observed,
                delta,
                classes,
                atomic_index,
                step_before,
                engine.tick,
                exposed_touch_count,
                affected,
            )
            if exposed_touch_count or delta.level1_micro_exposure_changed or delta.level2_object_link_changed or delta.level3_two_hop_changed:
                events.append(event)
            _accumulate_counts(counts, delta, exposed_touch_count)
            if delta.level1_micro_exposure_changed:
                changed_objects["micro"].update(affected)
            if delta.level2_object_link_changed:
                changed_objects["direct"].update(affected)
            if delta.level3_two_hop_changed:
                changed_objects["two_hop"].update(affected)
            current_run = _extend_state_run(
                runs,
                current_run,
                next_state,
                event,
                atomic_index,
                step_before,
                engine.tick,
            )
            state = next_state
    finally:
        engine.update_observer = None
    if current_run is not None:
        runs.append(current_run)
    if collector.targets != engine.targets:
        raise RuntimeError("exposure raw-slot mirror diverged from the engine")
    if graph != engine.candidate_graph():
        raise RuntimeError("exposure candidate-graph cache diverged from the engine")
    summary = _summary(
        counts,
        changed_objects,
        atomic_update_count=engine.tick - start_step,
        internal_change_count=0,
    )
    summary.update(
        start_snapshot_id=start_snapshot_id,
        end_snapshot_id=end_snapshot_id,
        start_step=start_step,
        end_step=engine.tick,
        fixed_point_object_count=len(index.objects),
        scoped_object_count=len(start_state.scope_object_ids),
        total_internal_support_slot_count=sum(
            len(slots) for _, slots in start_state.internal_support_slots
        ),
        start_exposed_slot_count=len(start_state.exposed_slots),
        end_exposed_slot_count=len(state.exposed_slots),
        start_direct_object_link_count=len(start_state.direct_links),
        end_direct_object_link_count=len(state.direct_links),
        start_two_hop_path_count=len(start_state.two_hop_paths),
        end_two_hop_path_count=len(state.two_hop_paths),
    )
    if not start_state.exposed_slots and not state.exposed_slots:
        summary["classification"] = (
            "no_exposed_slots_under_strict_support_definition"
        )
    return ExposureTraceResult(
        start_snapshot_id,
        end_snapshot_id,
        start_step,
        engine.tick,
        index,
        roots,
        start_state,
        state,
        tuple(events),
        tuple(runs),
        summary,
    )


def validate_fixed_point_invariant(
    index: FixedPointIndex,
    graph: list[tuple[int, ...]],
    *,
    step: int,
) -> None:
    current = {
        item.members
        for item in candidate_objects(graph, include_singletons=False)
        if len(item.members) in {2, 3}
    }
    expected = set(index.by_members)
    if current != expected:
        missing = sorted(expected - current)
        added = sorted(current - expected)
        raise FixedPointInvariantViolation(
            f"invariant_violation at step {step}: missing={missing}, added={added}"
        )


def _change_event(
    index: FixedPointIndex,
    observed: _TickObservation,
    delta,
    classes: tuple[str, ...],
    atomic_index: int,
    step_before: int,
    step_after: int,
    exposed_touch_count: int,
    affected: set[str],
) -> ExposureChangeEvent:
    active = observed.active
    active_retarget = next(
        (
            item
            for item in observed.retargets
            if item.event_kind == "active_retarget"
            and item.source == active.source
            and item.slot == active.slot
        ),
        None,
    )
    source_ids = index.raw_to_object_ids.get(active.source, ())
    source_id = source_ids[0] if len(source_ids) == 1 else None
    return ExposureChangeEvent(
        atomic_index,
        step_before,
        step_after,
        active.source,
        active.slot,
        active.slot + 1,
        active.previous_target,
        None if active_retarget is None else active_retarget.new_target,
        source_id,
        None if source_id is None else index.by_id[source_id].object_type,
        active.source if source_id is not None else None,
        index.raw_to_object_ids.get(active.previous_target, ()),
        ()
        if active_retarget is None
        else index.raw_to_object_ids.get(active_retarget.new_target, ()),
        False,
        delta.level1_micro_exposure_changed,
        delta.level2_object_link_changed,
        delta.level3_two_hop_changed,
        exposed_touch_count,
        classes,
        tuple(sorted(affected)),
        delta.affected_direct_links_before,
        delta.affected_direct_links_after,
        delta.affected_two_hop_paths_before,
        delta.affected_two_hop_paths_after,
        delta.gap_transfer_candidate,
        observed.retargets,
    )


def _exposed_touch_count(
    retargets: tuple[RetargetObservation, ...],
    before: ObjectExposureState,
    after: ObjectExposureState,
) -> int:
    before_slots = {
        (item.source_member_id, item.source_slot_id) for item in before.exposed_slots
    }
    after_slots = {
        (item.source_member_id, item.source_slot_id) for item in after.exposed_slots
    }
    return len(
        {
            (item.source, item.slot)
            for item in retargets
            if (item.source, item.slot) in before_slots | after_slots
        }
    )


def _accumulate_counts(counts: Counter[str], delta, exposed_touches: int) -> None:
    counts["exposed_slot_touch_count"] += exposed_touches
    counts["exposed_slot_touch_update_count"] += bool(exposed_touches)
    counts["raw_target_change_count"] += delta.raw_target_change_count
    counts["same_target_object_micro_rewire_count"] += (
        delta.same_target_object_micro_rewire_count
    )
    counts["source_slot_reassignment_count"] += delta.source_slot_reassignment_count
    counts["direct_object_link_create_count"] += delta.direct_link_create_count
    counts["direct_object_link_break_count"] += delta.direct_link_break_count
    counts["direct_object_neighbor_change_count"] += delta.direct_neighbor_change_count
    counts["direct_object_multiplicity_change_count"] += delta.multiplicity_change_count
    counts["two_hop_path_create_count"] += delta.two_hop_path_create_count
    counts["two_hop_path_break_count"] += delta.two_hop_path_break_count
    counts["gap_transfer_candidate_count"] += delta.gap_transfer_candidate
    counts["level1_change_event_count"] += delta.level1_micro_exposure_changed
    counts["level2_change_event_count"] += delta.level2_object_link_changed
    counts["level3_change_event_count"] += delta.level3_two_hop_changed
    counts["two_hop_without_neighbor_change_count"] += (
        delta.level3_two_hop_changed and not delta.level2_object_link_changed
    )
    counts["micro_without_neighbor_change_count"] += (
        delta.level1_micro_exposure_changed and not delta.level2_object_link_changed
    )


def _extend_state_run(
    completed: list[ExposureStateRun],
    current: ExposureStateRun | None,
    state: ObjectExposureState,
    event: ExposureChangeEvent,
    atomic_index: int,
    step_before: int,
    step_after: int,
) -> ExposureStateRun:
    hashes = (
        state.level1_exposure_hash,
        state.level2_direct_link_hash,
        state.level3_two_hop_hash,
    )
    changes = (
        int(event.level1_micro_exposure_changed),
        int(event.level2_object_link_changed),
        int(event.level3_two_hop_changed),
    )
    if current is None or (
        current.level1_hash,
        current.level2_hash,
        current.level3_hash,
    ) != hashes:
        if current is not None:
            completed.append(current)
        return ExposureStateRun(
            len(completed) + 1,
            atomic_index,
            atomic_index,
            step_before,
            step_after,
            1,
            *hashes,
            *changes,
        )
    return replace(
        current,
        end_atomic_index=atomic_index,
        end_step=step_after,
        atomic_update_count=current.atomic_update_count + 1,
        micro_change_event_count=current.micro_change_event_count + changes[0],
        direct_link_change_event_count=(
            current.direct_link_change_event_count + changes[1]
        ),
        two_hop_change_event_count=current.two_hop_change_event_count + changes[2],
    )


def _summary(
    counts: Counter[str],
    changed_objects: dict[str, set[str]],
    *,
    atomic_update_count: int,
    internal_change_count: int,
) -> dict[str, Any]:
    micro = counts["level1_change_event_count"]
    direct = counts["level2_change_event_count"]
    two_hop = counts["level3_change_event_count"]
    classification = "first_and_second_layers_fully_static"
    if direct:
        classification = "fixed_points_static_object_neighbors_dynamic"
    elif two_hop:
        classification = "fixed_points_and_direct_links_static_two_hop_dynamic"
    elif micro:
        classification = "fixed_points_static_micro_exposure_dynamic"
    return {
        "atomic_update_count": atomic_update_count,
        "level0_internal_change_count": internal_change_count,
        "exposed_slot_touch_count": counts["exposed_slot_touch_count"],
        "exposed_slot_touch_update_count": counts[
            "exposed_slot_touch_update_count"
        ],
        "raw_target_change_count": counts["raw_target_change_count"],
        "same_target_object_micro_rewire_count": counts[
            "same_target_object_micro_rewire_count"
        ],
        "source_slot_reassignment_count": counts[
            "source_slot_reassignment_count"
        ],
        "direct_object_link_create_count": counts[
            "direct_object_link_create_count"
        ],
        "direct_object_link_break_count": counts[
            "direct_object_link_break_count"
        ],
        "direct_object_neighbor_change_count": counts[
            "direct_object_neighbor_change_count"
        ],
        "direct_object_multiplicity_change_count": counts[
            "direct_object_multiplicity_change_count"
        ],
        "two_hop_path_create_count": counts["two_hop_path_create_count"],
        "two_hop_path_break_count": counts["two_hop_path_break_count"],
        "two_hop_path_change_count": two_hop,
        "gap_transfer_candidate_count": counts["gap_transfer_candidate_count"],
        "external_rewire_chain_count": counts["external_rewire_chain_count"],
        "objects_with_micro_exposure_change": len(changed_objects["micro"]),
        "objects_with_direct_neighbor_change": len(changed_objects["direct"]),
        "objects_with_two_hop_change": len(changed_objects["two_hop"]),
        "P(micro exposure changes | exposed slot touched)": _probability(
            micro, counts["exposed_slot_touch_update_count"]
        ),
        "P(object neighbor changes | micro exposure changes)": _probability(
            direct, micro
        ),
        "P(two-hop changes | object neighbor unchanged)": _probability(
            counts["two_hop_without_neighbor_change_count"],
            counts["micro_without_neighbor_change_count"],
        ),
        "P(gap transfer candidate | object-level rewiring)": _probability(
            counts["gap_transfer_candidate_count"], direct
        ),
        "full_recompute_verification_count": counts[
            "full_recompute_verification_count"
        ],
        "classification": classification,
    }


def _probability(numerator: int, denominator: int) -> float | str:
    return numerator / denominator if denominator else "N/A"
