"""Non-invasive pair/triple tracking between two observation snapshots."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from uboot.candidate_graph import candidate_objects
from uboot.checkpoint import dynamics_state_hash, phase2_readiness
from uboot.edge_response import EdgeResponseEngine, EngineUpdateEvent
from uboot.joint_continuation import Members
from uboot.joint_diagnostics import candidate_support_raw_slots


@dataclass(frozen=True, slots=True)
class RetargetObservation:
    event_kind: str
    source: int
    slot: int
    slot_meaning: int
    old_target: int
    new_target: int


@dataclass(frozen=True, slots=True)
class AtomicTouch:
    active_node_id: int | None = None
    slot_id: int | None = None
    old_target_id: int | None = None
    new_target_id: int | None = None
    active_is_tracked_member: bool = False
    pair_support_touched: bool = False
    triangle_support_touched: bool = False
    incoming_relation_touched: bool = False
    any_structure_touch: bool = False
    affected_candidate_nodes: tuple[int, ...] = ()
    retarget_events: tuple[RetargetObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class MicroTraceEvent:
    atomic_index: int
    step_before: int
    step_after: int
    active_node_id: int | None
    slot_id: int | None
    slot_meaning: int | None
    old_target_id: int | None
    new_target_id: int | None
    active_is_tracked_member: bool
    pair_support_touched: bool
    triangle_support_touched: bool
    incoming_relation_touched: bool
    any_structure_touch: bool
    initial_pair_exists: bool
    initial_triangle_exists: bool
    current_pair_before: Members
    current_pair_after: Members
    pair_candidate_count: int
    pair_candidates: tuple[Members, ...]
    current_triangle_before: Members
    current_triangle_after: Members
    triangle_candidate_count: int
    triangle_candidates: tuple[Members, ...]
    micro_state: str
    pair_changed_this_update: bool
    triangle_changed_this_update: bool
    pair_recognition_changed: bool
    triangle_recognition_changed: bool
    pair_restored_to_initial: bool
    triangle_restored_to_initial: bool
    retarget_events: tuple[RetargetObservation, ...]


@dataclass(frozen=True, slots=True)
class MicroTraceResult:
    start_snapshot_id: int
    end_snapshot_id: int
    start_step: int
    end_step: int
    initial_pair: Members
    initial_triangle: Members
    events: tuple[MicroTraceEvent, ...]
    summary: dict[str, Any]


class MicroContinuationTracker:
    """Pure state machine for micro-level continuation and restoration."""

    def __init__(
        self, initial_pair: Iterable[int], initial_triangle: Iterable[int]
    ) -> None:
        self.initial_pair = _members(initial_pair, 2, "pair")
        self.initial_triangle = _members(initial_triangle, 3, "triangle")
        if set(self.initial_pair) & set(self.initial_triangle):
            raise ValueError("initial pair and triangle must be disjoint")
        self.current_pair = self.initial_pair
        self.current_triangle = self.initial_triangle
        self.previous_pair_candidates = (self.initial_pair,)
        self.previous_triangle_candidates = (self.initial_triangle,)
        self.previous_initial_pair_exists = True
        self.previous_initial_triangle_exists = True
        self.events: list[MicroTraceEvent] = []
        self.counts: Counter[str] = Counter()
        self.pair_sequence = [self.initial_pair]
        self.triangle_sequence = [self.initial_triangle]
        self.pair_moved_away = False
        self.triangle_moved_away = False

    def observe(
        self,
        pairs: Iterable[Members],
        triangles: Iterable[Members],
        *,
        step_before: int,
        step_after: int,
        touch: AtomicTouch,
    ) -> MicroTraceEvent:
        all_pairs = tuple(pairs)
        all_triangles = tuple(triangles)
        pair_before, triangle_before = self.current_pair, self.current_triangle
        pair_candidates = tuple(
            item for item in all_pairs if set(item) & set(pair_before)
        )
        triangle_candidates = tuple(
            item
            for item in all_triangles
            if len(set(item) & set(triangle_before)) >= 2
        )
        initial_pair_exists = self.initial_pair in all_pairs
        initial_triangle_exists = self.initial_triangle in all_triangles
        pair_match = pair_candidates[0] if len(pair_candidates) == 1 else None
        triangle_match = (
            triangle_candidates[0] if len(triangle_candidates) == 1 else None
        )
        conflict = bool(
            pair_match is not None
            and triangle_match is not None
            and set(pair_match) & set(triangle_match)
        )
        pair_changed = pair_match is not None and pair_match != pair_before
        triangle_changed = (
            triangle_match is not None and triangle_match != triangle_before
        )
        state = _micro_state(
            pair_candidates,
            triangle_candidates,
            pair_changed,
            triangle_changed,
            conflict,
        )
        pair_restored = initial_pair_exists and (
            not self.previous_initial_pair_exists
            or (pair_match == self.initial_pair and pair_before != self.initial_pair)
        )
        triangle_restored = initial_triangle_exists and (
            not self.previous_initial_triangle_exists
            or (
                triangle_match == self.initial_triangle
                and triangle_before != self.initial_triangle
            )
        )
        if not conflict and pair_match is not None:
            self.current_pair = pair_match
        if not conflict and triangle_match is not None:
            self.current_triangle = triangle_match
        self._record_confirmed_sequence(
            self.pair_sequence, pair_before, self.current_pair, "pair_oscillation_count"
        )
        self._record_confirmed_sequence(
            self.triangle_sequence,
            triangle_before,
            self.current_triangle,
            "triangle_oscillation_count",
        )
        if pair_changed:
            self.counts["pair_move_count"] += 1
            if self.current_pair != self.initial_pair:
                self.pair_moved_away = True
        if triangle_changed:
            self.counts["triangle_move_count"] += 1
            if self.current_triangle != self.initial_triangle:
                self.triangle_moved_away = True
        if not initial_pair_exists:
            self.counts["initial_pair_absent_updates"] += 1
        if not initial_triangle_exists:
            self.counts["initial_triangle_absent_updates"] += 1
        if pair_restored and not self.previous_initial_pair_exists:
            self.counts["pair_break_and_restore_count"] += 1
        if triangle_restored and not self.previous_initial_triangle_exists:
            self.counts["triangle_break_and_restore_count"] += 1
        if pair_restored and self.pair_moved_away:
            self.counts["transient_pair_move_count"] += 1
            self.pair_moved_away = False
        if triangle_restored and self.triangle_moved_away:
            self.counts["transient_triangle_move_count"] += 1
            self.triangle_moved_away = False
        if len(pair_candidates) > 1:
            self.counts["pair_ambiguous_updates"] += 1
        if len(triangle_candidates) > 1:
            self.counts["triangle_ambiguous_updates"] += 1
        if not pair_candidates:
            self.counts["pair_broken_updates"] += 1
        if not triangle_candidates:
            self.counts["triangle_broken_updates"] += 1
        self.counts[f"micro_state:{state}"] += 1
        self.counts["structure_touch_updates"] += touch.any_structure_touch
        self.counts["pair_support_touch_updates"] += touch.pair_support_touched
        self.counts["triangle_support_touch_updates"] += (
            touch.triangle_support_touched
        )
        event = MicroTraceEvent(
            atomic_index=len(self.events) + 1,
            step_before=step_before,
            step_after=step_after,
            active_node_id=touch.active_node_id,
            slot_id=touch.slot_id,
            slot_meaning=None if touch.slot_id is None else touch.slot_id + 1,
            old_target_id=touch.old_target_id,
            new_target_id=touch.new_target_id,
            active_is_tracked_member=touch.active_is_tracked_member,
            pair_support_touched=touch.pair_support_touched,
            triangle_support_touched=touch.triangle_support_touched,
            incoming_relation_touched=touch.incoming_relation_touched,
            any_structure_touch=touch.any_structure_touch,
            initial_pair_exists=initial_pair_exists,
            initial_triangle_exists=initial_triangle_exists,
            current_pair_before=pair_before,
            current_pair_after=self.current_pair,
            pair_candidate_count=len(pair_candidates),
            pair_candidates=pair_candidates,
            current_triangle_before=triangle_before,
            current_triangle_after=self.current_triangle,
            triangle_candidate_count=len(triangle_candidates),
            triangle_candidates=triangle_candidates,
            micro_state=state,
            pair_changed_this_update=pair_changed,
            triangle_changed_this_update=triangle_changed,
            pair_recognition_changed=(
                pair_candidates != self.previous_pair_candidates
            ),
            triangle_recognition_changed=(
                triangle_candidates != self.previous_triangle_candidates
            ),
            pair_restored_to_initial=pair_restored,
            triangle_restored_to_initial=triangle_restored,
            retarget_events=touch.retarget_events,
        )
        self.events.append(event)
        self.previous_pair_candidates = pair_candidates
        self.previous_triangle_candidates = triangle_candidates
        self.previous_initial_pair_exists = initial_pair_exists
        self.previous_initial_triangle_exists = initial_triangle_exists
        return event

    def summary(self) -> dict[str, Any]:
        atomic_count = len(self.events)
        pair_transient = bool(self.counts["transient_pair_move_count"])
        triangle_transient = bool(self.counts["transient_triangle_move_count"])
        pair_break_restore = bool(self.counts["pair_break_and_restore_count"])
        triangle_break_restore = bool(
            self.counts["triangle_break_and_restore_count"]
        )
        ever_moved = bool(
            self.counts["pair_move_count"] or self.counts["triangle_move_count"]
        )
        ever_broken = bool(
            self.counts["pair_broken_updates"]
            or self.counts["triangle_broken_updates"]
        )
        ever_ambiguous = bool(
            self.counts["pair_ambiguous_updates"]
            or self.counts["triangle_ambiguous_updates"]
        )
        moved_new = bool(
            (
                self.current_pair != self.initial_pair
                and self.previous_pair_candidates == (self.current_pair,)
            )
            or (
                self.current_triangle != self.initial_triangle
                and self.previous_triangle_candidates == (self.current_triangle,)
            )
        )
        atomically_static = (
            atomic_count > 0
            and not ever_moved
            and not ever_broken
            and not ever_ambiguous
            and self.counts["micro_state:exact_static"] == atomic_count
        )
        return {
            "atomic_update_count": atomic_count,
            "structure_touch_update_count": self.counts[
                "structure_touch_updates"
            ],
            "pair_support_touch_update_count": self.counts[
                "pair_support_touch_updates"
            ],
            "triangle_support_touch_update_count": self.counts[
                "triangle_support_touch_updates"
            ],
            "initial_pair_ever_absent": bool(
                self.counts["initial_pair_absent_updates"]
            ),
            "initial_triangle_ever_absent": bool(
                self.counts["initial_triangle_absent_updates"]
            ),
            "pair_ever_broken": bool(self.counts["pair_broken_updates"]),
            "triangle_ever_broken": bool(
                self.counts["triangle_broken_updates"]
            ),
            "ever_moved": ever_moved,
            "ever_broken": ever_broken,
            "ever_ambiguous": ever_ambiguous,
            "transient_pair_move": pair_transient,
            "transient_triangle_move": triangle_transient,
            "transient_both_move": pair_transient and triangle_transient,
            "pair_break_and_restore": pair_break_restore,
            "triangle_break_and_restore": triangle_break_restore,
            "moved_then_stabilized_new": moved_new,
            "pair_oscillation_count": self.counts["pair_oscillation_count"],
            "triangle_oscillation_count": self.counts[
                "triangle_oscillation_count"
            ],
            "pair_state_sequence": [_serialize_members(item) for item in self.pair_sequence],
            "triangle_state_sequence": [
                _serialize_members(item) for item in self.triangle_sequence
            ],
            "final_pair": _serialize_members(self.current_pair),
            "final_triangle": _serialize_members(self.current_triangle),
            "end_returned_to_initial_members": (
                self.current_pair == self.initial_pair
                and self.current_triangle == self.initial_triangle
            ),
            "restored": pair_transient
            or triangle_transient
            or pair_break_restore
            or triangle_break_restore,
            "classification": _interval_classification(
                atomically_static=atomically_static,
                moved_new=moved_new,
                pair_transient=pair_transient,
                triangle_transient=triangle_transient,
                pair_break_restore=pair_break_restore,
                triangle_break_restore=triangle_break_restore,
                ever_broken=ever_broken,
                ever_ambiguous=ever_ambiguous,
            ),
            "micro_state_counts": {
                key.removeprefix("micro_state:"): value
                for key, value in self.counts.items()
                if key.startswith("micro_state:")
            },
        }

    def _record_confirmed_sequence(
        self,
        sequence: list[Members],
        before: Members,
        after: Members,
        counter: str,
    ) -> None:
        if after == before:
            return
        if after in sequence[:-1]:
            self.counts[counter] += 1
        sequence.append(after)


class _TickCollector:
    """Collect committed retarget details while mirroring raw slots read-only."""

    def __init__(self, targets: list[list[int]]) -> None:
        self.targets = [list(row) for row in targets]
        self.begin_tick((), ())

    def begin_tick(self, pair: Iterable[int], triangle: Iterable[int]) -> None:
        self.pair = frozenset(pair)
        self.triangle = frozenset(triangle)
        self.active: EngineUpdateEvent | None = None
        self.retargets: list[RetargetObservation] = []
        self.pair_touch = False
        self.triangle_touch = False
        self.incoming_touch = False
        self.dependency_touch = False
        self.affected: set[int] = set()

    def __call__(self, event: EngineUpdateEvent) -> None:
        if event.event_kind == "active_attempt":
            self.active = event
            return
        assert event.target is not None
        pair_before = candidate_support_raw_slots(self.targets, self.pair)
        triangle_before = candidate_support_raw_slots(self.targets, self.triangle)
        tracked = self.pair | self.triangle
        pair_dependencies_before = self.pair | {
            target for member in self.pair for target in self.targets[member]
        }
        triangle_dependencies_before = self.triangle | {
            target for member in self.triangle for target in self.targets[member]
        }
        old_slot = (event.source, event.slot, event.previous_target)
        self.affected.update(
            (event.source, event.previous_target, event.target)
        )
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
        pair_after = candidate_support_raw_slots(self.targets, self.pair)
        triangle_after = candidate_support_raw_slots(self.targets, self.triangle)
        new_slot = (event.source, event.slot, event.target)
        self.pair_touch |= (
            old_slot in pair_before
            or new_slot in pair_after
            or pair_before != pair_after
        )
        self.triangle_touch |= (
            old_slot in triangle_before
            or new_slot in triangle_after
            or triangle_before != triangle_after
        )
        self.incoming_touch |= (
            event.source in tracked
            or event.previous_target in tracked
            or event.target in tracked
        )
        self.dependency_touch |= (
            event.source in pair_dependencies_before
            or event.source in triangle_dependencies_before
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

    def finish_tick(self) -> AtomicTouch:
        if self.active is None:
            raise RuntimeError("engine tick did not emit an active attempt")
        active_retarget = next(
            (
                event
                for event in self.retargets
                if event.event_kind == "active_retarget"
                and event.source == self.active.source
                and event.slot == self.active.slot
            ),
            None,
        )
        active_tracked = self.active.source in self.pair | self.triangle
        return AtomicTouch(
            active_node_id=self.active.source,
            slot_id=self.active.slot,
            old_target_id=self.active.previous_target,
            new_target_id=(
                None if active_retarget is None else active_retarget.new_target
            ),
            active_is_tracked_member=active_tracked,
            pair_support_touched=self.pair_touch,
            triangle_support_touched=self.triangle_touch,
            incoming_relation_touched=self.incoming_touch,
            any_structure_touch=(
                active_tracked
                or self.pair_touch
                or self.triangle_touch
                or self.incoming_touch
                or self.dependency_touch
            ),
            affected_candidate_nodes=tuple(self.affected),
            retarget_events=tuple(self.retargets),
        )


def replay_with_microtrace(
    engine: EdgeResponseEngine,
    *,
    initial_pair: Iterable[int],
    initial_triangle: Iterable[int],
    start_snapshot_id: int,
    end_snapshot_id: int,
    end_step: int,
) -> MicroTraceResult:
    """Run an engine from its current S0 state to S1, observing every tick."""
    if end_snapshot_id <= start_snapshot_id:
        raise ValueError("end snapshot must follow start snapshot")
    if end_step <= engine.tick:
        raise ValueError("end_step must exceed the engine start step")
    if engine.update_observer is not None:
        raise ValueError("microtrace requires an unused update observer")
    start_step = engine.tick
    graph = engine.candidate_graph()
    pairs, triangles = _pair_triangle_members(graph)
    pair = _members(initial_pair, 2, "pair")
    triangle = _members(initial_triangle, 3, "triangle")
    if pair not in pairs:
        raise ValueError("initial pair is not a real closed pair at S0")
    if triangle not in triangles:
        raise ValueError("initial triangle is not a real closed triangle at S0")
    if set(pair) & set(triangle):
        raise ValueError("initial pair and triangle have a structural conflict")
    tracker = MicroContinuationTracker(pair, triangle)
    collector = _TickCollector(engine.targets)
    engine.update_observer = collector
    try:
        while engine.tick < end_step:
            before = engine.tick
            collector.begin_tick(tracker.current_pair, tracker.current_triangle)
            engine.step_tick()
            touch = collector.finish_tick()
            for node in touch.affected_candidate_nodes:
                graph[node] = engine.direct_candidates(node)
            pairs, triangles = _pair_triangle_members(graph)
            tracker.observe(
                pairs,
                triangles,
                step_before=before,
                step_after=engine.tick,
                touch=touch,
            )
    finally:
        engine.update_observer = None
    if collector.targets != engine.targets:
        raise RuntimeError("microtrace raw-slot mirror diverged from the engine")
    if graph != engine.candidate_graph():
        raise RuntimeError("microtrace candidate-graph cache diverged from the engine")
    summary = tracker.summary()
    summary.update(
        start_snapshot_id=start_snapshot_id,
        end_snapshot_id=end_snapshot_id,
        start_step=start_step,
        end_step=engine.tick,
    )
    return MicroTraceResult(
        start_snapshot_id,
        end_snapshot_id,
        start_step,
        engine.tick,
        pair,
        triangle,
        tuple(tracker.events),
        summary,
    )


def compare_replays(
    direct: EdgeResponseEngine,
    traced: EdgeResponseEngine,
    *,
    start_hash_direct: str,
    start_hash_traced: str,
) -> dict[str, Any]:
    direct_objects = _selected_object_signatures(direct)
    traced_objects = _selected_object_signatures(traced)
    return {
        "start_dynamics_hash_direct": start_hash_direct,
        "start_dynamics_hash_microtrace": start_hash_traced,
        "start_dynamics_hash_match": start_hash_direct == start_hash_traced,
        "final_dynamics_hash_direct": dynamics_state_hash(direct),
        "final_dynamics_hash_microtrace": dynamics_state_hash(traced),
        "final_dynamics_hash_match": dynamics_state_hash(direct)
        == dynamics_state_hash(traced),
        "final_step_direct": direct.tick,
        "final_step_microtrace": traced.tick,
        "final_step_match": direct.tick == traced.tick,
        "rng_state_match": direct.rng.getstate() == traced.rng.getstate(),
        "counters_match": direct.counters == traced.counters,
        "active_attempts_match": direct.active_attempts == traced.active_attempts,
        "response_aggregates_match": direct.response_aggregates
        == traced.response_aggregates,
        "raw_targets_match": direct.targets == traced.targets,
        "pair_triangle_static_statistics_match": direct_objects == traced_objects,
        "phase2_readiness_match": phase2_readiness(direct)
        == phase2_readiness(traced),
        "all_checks_pass": all(
            (
                start_hash_direct == start_hash_traced,
                dynamics_state_hash(direct) == dynamics_state_hash(traced),
                direct.tick == traced.tick,
                direct.rng.getstate() == traced.rng.getstate(),
                direct.counters == traced.counters,
                direct.active_attempts == traced.active_attempts,
                direct.response_aggregates == traced.response_aggregates,
                direct.targets == traced.targets,
                direct_objects == traced_objects,
                phase2_readiness(direct) == phase2_readiness(traced),
            )
        ),
    }


def _micro_state(
    pairs: tuple[Members, ...],
    triangles: tuple[Members, ...],
    pair_changed: bool,
    triangle_changed: bool,
    conflict: bool,
) -> str:
    if len(pairs) > 1 and len(triangles) > 1:
        return "both_ambiguous"
    if len(pairs) > 1:
        return "pair_ambiguous"
    if len(triangles) > 1:
        return "triangle_ambiguous"
    if conflict:
        return "structural_conflict"
    if len(pairs) == len(triangles) == 1:
        if pair_changed and triangle_changed:
            return "both_moved"
        if pair_changed:
            return "pair_moved_triangle_static"
        if triangle_changed:
            return "pair_static_triangle_moved"
        return "exact_static"
    if not pairs and not triangles:
        return "both_broken"
    if not pairs:
        return "pair_broken_triangle_valid"
    return "triangle_broken_pair_valid"


def _interval_classification(
    *,
    atomically_static: bool,
    moved_new: bool,
    pair_transient: bool,
    triangle_transient: bool,
    pair_break_restore: bool,
    triangle_break_restore: bool,
    ever_broken: bool,
    ever_ambiguous: bool,
) -> str:
    if atomically_static:
        return "atomically_static"
    if moved_new:
        return "moved_then_stabilized_new"
    if pair_transient or triangle_transient:
        return "micro_move_then_return"
    if pair_break_restore or triangle_break_restore:
        return "micro_break_then_restore"
    if ever_broken:
        return "micro_break_without_restore"
    if ever_ambiguous:
        return "micro_ambiguity_observed"
    return "micro_change_observed"


def _pair_triangle_members(
    graph: list[tuple[int, ...]],
) -> tuple[tuple[Members, ...], tuple[Members, ...]]:
    objects = candidate_objects(graph, include_singletons=False)
    return (
        tuple(item.members for item in objects if len(item.members) == 2),
        tuple(item.members for item in objects if len(item.members) == 3),
    )


def _selected_object_signatures(engine: EdgeResponseEngine) -> dict[str, Any]:
    objects = candidate_objects(engine.candidate_graph(), include_singletons=False)
    return {
        "pairs": [asdict(item) for item in objects if len(item.members) == 2],
        "triangles": [asdict(item) for item in objects if len(item.members) == 3],
    }


def _members(values: Iterable[int], size: int, label: str) -> Members:
    result = tuple(sorted(values))
    if len(result) != size or len(set(result)) != size:
        raise ValueError(f"{label} must contain {size} distinct members")
    return result


def _serialize_members(values: Iterable[int]) -> str:
    return "|".join(map(str, values))
