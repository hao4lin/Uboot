"""Touch replay and slice diagnostics for joint pair-triple continuation."""

from __future__ import annotations

from collections import Counter
import csv
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from uboot.checkpoint import dynamics_state_hash
from uboot.edge_response import EdgeResponseEngine, EngineUpdateEvent
from uboot.joint_continuation import (
    DirectionTrackResult,
    JointTrackResult,
    Members,
    SnapshotSeries,
)
from uboot.kernel import SLOT_COUNT
from uboot.phase2 import snapshot_state


RawSlot = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class TouchObservation:
    direction: str
    snapshot_id: int
    previous_snapshot_id: int
    tracked_member_active_update_count: int
    pair_support_touch_count: int
    triangle_support_touch_count: int
    incoming_relation_touch_count: int
    any_structure_touch_count: int


@dataclass(frozen=True, slots=True)
class SliceDiagnostic:
    direction: str
    snapshot_id: int
    step: int | None
    tick: int | None
    sweep: float | None
    previous_accepted_snapshot_id: int
    current_pair: Members
    current_triangle: Members
    pair_candidate_count: int
    triangle_candidate_count: int
    matched_pair: Members | None
    matched_triangle: Members | None
    jointly_present: bool
    pair_changed: bool
    triangle_changed: bool
    movement_event: bool
    active_node_id: str
    active_node_is_tracked_member: bool | None
    pair_support_touched: bool | None
    triangle_support_touched: bool | None
    incoming_relation_touched: bool | None
    any_structure_touch: bool | None
    diagnostic_class: str
    collapsed_by_joint_requirement: bool
    god_slices_since_previous_accepted: int
    structure_touch_count_since_previous_accepted: int | None
    tracked_member_active_update_count: int | None
    pair_support_touch_count: int | None
    triangle_support_touch_count: int | None
    incoming_relation_touch_count: int | None
    any_structure_touch_count: int | None


class TrackedTouchObserver:
    """Observe relevant retargets without feeding measurements into dynamics."""

    def __init__(
        self,
        targets: list[list[int]],
        pair: Iterable[int],
        triangle: Iterable[int],
    ) -> None:
        self.targets = [list(row) for row in targets]
        self.pair = frozenset(pair)
        self.triangle = frozenset(triangle)
        self.counts: Counter[str] = Counter()

    def set_tracked(self, pair: Iterable[int], triangle: Iterable[int]) -> None:
        self.pair = frozenset(pair)
        self.triangle = frozenset(triangle)

    def __call__(self, event: EngineUpdateEvent) -> None:
        tracked = self.pair | self.triangle
        if event.event_kind == "active_attempt":
            if event.source in tracked:
                self.counts["tracked_member_active_update_count"] += 1
            return
        assert event.target is not None
        pair_sources_before = self.pair | {
            target for member in self.pair for target in self.targets[member]
        }
        triangle_sources_before = self.triangle | {
            target for member in self.triangle for target in self.targets[member]
        }
        pair_relevant = (
            event.source in pair_sources_before
            or event.previous_target in self.pair
            or event.target in self.pair
        )
        triangle_relevant = (
            event.source in triangle_sources_before
            or event.previous_target in self.triangle
            or event.target in self.triangle
        )
        pair_before = (
            candidate_support_raw_slots(self.targets, self.pair)
            if pair_relevant
            else frozenset()
        )
        triangle_before = (
            candidate_support_raw_slots(self.targets, self.triangle)
            if triangle_relevant
            else frozenset()
        )
        old_slot = (event.source, event.slot, event.previous_target)
        self.targets[event.source][event.slot] = event.target
        new_slot = (event.source, event.slot, event.target)
        pair_sources_after = self.pair | {
            target for member in self.pair for target in self.targets[member]
        }
        triangle_sources_after = self.triangle | {
            target for member in self.triangle for target in self.targets[member]
        }
        pair_relevant = pair_relevant or event.source in pair_sources_after
        triangle_relevant = triangle_relevant or event.source in triangle_sources_after
        pair_after = (
            candidate_support_raw_slots(self.targets, self.pair)
            if pair_relevant
            else frozenset()
        )
        triangle_after = (
            candidate_support_raw_slots(self.targets, self.triangle)
            if triangle_relevant
            else frozenset()
        )
        pair_touch = (
            old_slot in pair_before
            or new_slot in pair_after
            or pair_before != pair_after
        )
        triangle_touch = (
            old_slot in triangle_before
            or new_slot in triangle_after
            or triangle_before != triangle_after
        )
        incoming_touch = (
            event.source in tracked
            or event.previous_target in tracked
            or event.target in tracked
        )
        dependency_touch = (
            event.source in pair_sources_before
            or event.source in pair_sources_after
            or event.source in triangle_sources_before
            or event.source in triangle_sources_after
        )
        self.counts["pair_support_touch_count"] += pair_touch
        self.counts["triangle_support_touch_count"] += triangle_touch
        self.counts["incoming_relation_touch_count"] += incoming_touch
        self.counts["any_structure_touch_count"] += (
            pair_touch or triangle_touch or incoming_touch or dependency_touch
        )

    def flush(
        self, *, direction: str, snapshot_id: int, previous_snapshot_id: int
    ) -> TouchObservation:
        result = TouchObservation(
            direction=direction,
            snapshot_id=snapshot_id,
            previous_snapshot_id=previous_snapshot_id,
            tracked_member_active_update_count=self.counts[
                "tracked_member_active_update_count"
            ],
            pair_support_touch_count=self.counts["pair_support_touch_count"],
            triangle_support_touch_count=self.counts[
                "triangle_support_touch_count"
            ],
            incoming_relation_touch_count=self.counts[
                "incoming_relation_touch_count"
            ],
            any_structure_touch_count=self.counts["any_structure_touch_count"],
        )
        self.counts.clear()
        return result


def candidate_support_raw_slots(
    targets: list[list[int]], members: Iterable[int]
) -> frozenset[RawSlot]:
    """Return positive raw-slot witnesses for internal M2 candidate edges."""
    group = frozenset(members)
    support: set[RawSlot] = set()
    for source in group:
        for target in group - {source}:
            for slot, value in enumerate(targets[target]):
                if value == source:
                    support.add((target, slot, value))
            for first_slot, middle in enumerate(targets[source]):
                for second_slot, value in enumerate(targets[middle]):
                    if value == target:
                        support.add((source, first_slot, middle))
                        support.add((middle, second_slot, value))
    return frozenset(support)


def candidate_relation_dependency_slots(
    targets: list[list[int]], members: Iterable[int]
) -> frozenset[RawSlot]:
    """Raw slots whose retargeting can change an M2 candidate set of a member."""
    group = frozenset(members)
    dependencies: set[RawSlot] = set()
    for source, row in enumerate(targets):
        for slot, target in enumerate(row):
            if source in group or target in group:
                dependencies.add((source, slot, target))
    for source in group:
        for middle in targets[source]:
            dependencies.update(
                (middle, slot, target)
                for slot, target in enumerate(targets[middle])
            )
    return frozenset(dependencies)


def run_touch_replay(
    engine: EdgeResponseEngine,
    *,
    pair_ids: Members,
    triangle_ids: Members,
    start_snapshot_id: int,
    tracking_sweeps: int,
    snapshot_interval_sweeps: int,
    output_dir: Path,
) -> dict[str, Any]:
    if tracking_sweeps < 1 or snapshot_interval_sweeps < 1:
        raise ValueError("tracking and snapshot intervals must be positive")
    first_snapshot = snapshot_state(engine, start_snapshot_id)
    pairs, triangles = _closed_pairs_and_triples_from_snapshot(first_snapshot)
    if pair_ids not in pairs or triangle_ids not in triangles:
        raise ValueError("tracked pair/triple is not closed at replay start")
    observer = TrackedTouchObserver(engine.targets, pair_ids, triangle_ids)
    engine.update_observer = observer
    ticks_per_sweep = SLOT_COUNT * len(engine.targets)
    source_sweep = engine.tick // ticks_per_sweep
    start_hash = dynamics_state_hash(engine)
    rows: list[TouchObservation] = []
    system_rows = [_system_snapshot_row(first_snapshot, ticks_per_sweep)]
    object_rows = list(first_snapshot["objects"])
    current_pair, current_triangle = pair_ids, triangle_ids
    previous_snapshot_id = start_snapshot_id
    offsets = list(
        range(snapshot_interval_sweeps, tracking_sweeps + 1, snapshot_interval_sweeps)
    )
    if not offsets or offsets[-1] != tracking_sweeps:
        offsets.append(tracking_sweeps)
    for index, offset in enumerate(offsets, start=1):
        engine.run(source_sweep + offset)
        snapshot_id = start_snapshot_id + index
        observation = observer.flush(
            direction="ascending_scan",
            snapshot_id=snapshot_id,
            previous_snapshot_id=previous_snapshot_id,
        )
        rows.append(observation)
        recorded = snapshot_state(engine, snapshot_id)
        system_rows.append(_system_snapshot_row(recorded, ticks_per_sweep))
        object_rows.extend(recorded["objects"])
        pairs, triangles = _closed_pairs_and_triples_from_snapshot(recorded)
        pair_candidates = tuple(
            item for item in pairs if set(item) & set(current_pair)
        )
        triangle_candidates = tuple(
            item for item in triangles if len(set(item) & set(current_triangle)) >= 2
        )
        if len(pair_candidates) == len(triangle_candidates) == 1:
            matched_pair, matched_triangle = pair_candidates[0], triangle_candidates[0]
            if not set(matched_pair) & set(matched_triangle):
                current_pair, current_triangle = matched_pair, matched_triangle
                observer.set_tracked(current_pair, current_triangle)
        previous_snapshot_id = snapshot_id
    engine.update_observer = None
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(
        output_dir / "joint_fixed_point_update_touches.csv",
        [asdict(row) for row in rows],
        fieldnames=[field for field in TouchObservation.__dataclass_fields__],
    )
    _write_csv(
        output_dir / "phase2_system_snapshots.csv",
        system_rows,
        fieldnames=["snapshot_id", "tick", "sweep"],
    )
    _write_csv(
        output_dir / "phase2_object_snapshots.csv",
        object_rows,
        fieldnames=list(object_rows[0]) if object_rows else [],
    )
    manifest = {
        "N": len(engine.targets),
        "source_sweep": source_sweep,
        "tracking_sweeps": tracking_sweeps,
        "snapshot_interval_sweeps": snapshot_interval_sweeps,
        "start_snapshot_id": start_snapshot_id,
        "pair_ids": list(pair_ids),
        "triangle_ids": list(triangle_ids),
        "start_dynamics_state_hash": start_hash,
        "final_dynamics_state_hash": dynamics_state_hash(engine),
        "touch_interval_count": len(rows),
    }
    (output_dir / "joint_touch_replay_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def load_touch_observations(path: Path | None) -> dict[tuple[str, int], TouchObservation]:
    if path is None:
        return {}
    observations = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            item = TouchObservation(
                direction=row["direction"],
                snapshot_id=int(row["snapshot_id"]),
                previous_snapshot_id=int(row["previous_snapshot_id"]),
                tracked_member_active_update_count=int(
                    row["tracked_member_active_update_count"]
                ),
                pair_support_touch_count=int(row["pair_support_touch_count"]),
                triangle_support_touch_count=int(
                    row["triangle_support_touch_count"]
                ),
                incoming_relation_touch_count=int(
                    row["incoming_relation_touch_count"]
                ),
                any_structure_touch_count=int(row["any_structure_touch_count"]),
            )
            observations[(item.direction, item.snapshot_id)] = item
    return observations


def build_diagnostics(
    series: SnapshotSeries,
    result: JointTrackResult,
    touches: dict[tuple[str, int], TouchObservation],
) -> dict[str, tuple[SliceDiagnostic, ...]]:
    return {
        "ascending": _diagnose_direction(series, result.ascending, result, touches, 1),
        "descending": _diagnose_direction(series, result.descending, result, touches, -1),
    }


def write_diagnostics(
    diagnostics: dict[str, tuple[SliceDiagnostic, ...]], output_dir: Path
) -> dict[str, Any]:
    rows = [asdict(row) for values in diagnostics.values() for row in values]
    for row in rows:
        for field in ("current_pair", "current_triangle", "matched_pair", "matched_triangle"):
            row[field] = _serialize(row[field])
    movement_rows = []
    for values in diagnostics.values():
        movement_index = 0
        god_since = 0
        accepted_since = 0
        touches_since = 0
        for row in values:
            god_since += 1
            if row.jointly_present:
                accepted_since += 1
            touches_since += row.any_structure_touch_count or 0
            if not row.movement_event:
                continue
            movement_index += 1
            movement_rows.append(
                {
                    "direction": row.direction,
                    "movement_index": movement_index,
                    "snapshot_id": row.snapshot_id,
                    "step": row.step,
                    "tick": row.tick,
                    "previous_pair": _serialize(row.current_pair),
                    "next_pair": _serialize(row.matched_pair),
                    "pair_shared_ids": _serialize(
                        set(row.current_pair) & set(row.matched_pair or ())
                    ),
                    "pair_changed": row.pair_changed,
                    "previous_triangle": _serialize(row.current_triangle),
                    "next_triangle": _serialize(row.matched_triangle),
                    "triangle_shared_ids": _serialize(
                        set(row.current_triangle) & set(row.matched_triangle or ())
                    ),
                    "triangle_changed": row.triangle_changed,
                    "movement_code": f"{int(row.pair_changed)}{int(row.triangle_changed)}",
                    "god_slices_since_previous_movement": god_since,
                    "accepted_slices_since_previous_movement": accepted_since,
                    "structure_touches_since_previous_movement": touches_since,
                }
            )
            god_since = 0
            accepted_since = 0
            touches_since = 0
    _write_csv(
        output_dir / "joint_fixed_point_track_diagnostics.csv",
        rows,
        fieldnames=[field for field in SliceDiagnostic.__dataclass_fields__],
    )
    movement_fields = [
        "direction", "movement_index", "snapshot_id", "step", "tick", "previous_pair",
        "next_pair", "pair_shared_ids", "pair_changed", "previous_triangle",
        "next_triangle", "triangle_shared_ids", "triangle_changed", "movement_code",
        "god_slices_since_previous_movement", "accepted_slices_since_previous_movement",
        "structure_touches_since_previous_movement",
    ]
    _write_csv(
        output_dir / "joint_fixed_point_movement_events.csv",
        movement_rows,
        fieldnames=movement_fields,
    )
    summary = {
        name: _diagnostic_summary(values) for name, values in diagnostics.items()
    }
    summary["total"] = _diagnostic_summary(
        tuple(row for values in diagnostics.values() for row in values)
    )
    (output_dir / "joint_fixed_point_touch_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "joint_fixed_point_touch_summary.md").write_text(
        _touch_markdown(summary), encoding="utf-8"
    )
    return summary


def _diagnose_direction(
    series: SnapshotSeries,
    track: DirectionTrackResult,
    result: JointTrackResult,
    touches: dict[tuple[str, int], TouchObservation],
    increment: int,
) -> tuple[SliceDiagnostic, ...]:
    position = series._positions[result.start_snapshot_id] + increment
    current_pair, current_triangle = result.start_pair_ids, result.start_triangle_ids
    previous_accepted = result.start_snapshot_id
    gap = 0
    rows = []
    while 0 <= position < len(series.slices):
        if len(rows) >= max(0, track.scanned_god_slice_count - 1):
            break
        item = series.slices[position]
        pairs = series.candidate_pairs(position, current_pair)
        triangles = series.candidate_triangles(position, current_triangle)
        touch = touches.get((track.direction, item.snapshot_id))
        matched_pair = pairs[0] if len(pairs) == 1 else None
        matched_triangle = triangles[0] if len(triangles) == 1 else None
        jointly_present = matched_pair is not None and matched_triangle is not None
        conflict = jointly_present and bool(set(matched_pair) & set(matched_triangle))
        pair_changed = matched_pair is not None and matched_pair != current_pair
        triangle_changed = (
            matched_triangle is not None and matched_triangle != current_triangle
        )
        movement = jointly_present and not conflict and (pair_changed or triangle_changed)
        diagnostic_class = _diagnostic_class(
            pairs, triangles, pair_changed, triangle_changed, conflict, touch
        )
        gap += 1
        rows.append(
            SliceDiagnostic(
                track.direction,
                item.snapshot_id,
                item.tick,
                item.tick,
                item.sweep,
                previous_accepted,
                current_pair,
                current_triangle,
                len(pairs),
                len(triangles),
                matched_pair,
                matched_triangle,
                jointly_present and not conflict,
                pair_changed,
                triangle_changed,
                movement,
                "",
                None if touch is None else bool(touch.tracked_member_active_update_count),
                None if touch is None else bool(touch.pair_support_touch_count),
                None if touch is None else bool(touch.triangle_support_touch_count),
                None if touch is None else bool(touch.incoming_relation_touch_count),
                None if touch is None else bool(touch.any_structure_touch_count),
                diagnostic_class,
                not (jointly_present and not conflict),
                gap,
                None if touch is None else touch.any_structure_touch_count,
                None if touch is None else touch.tracked_member_active_update_count,
                None if touch is None else touch.pair_support_touch_count,
                None if touch is None else touch.triangle_support_touch_count,
                None if touch is None else touch.incoming_relation_touch_count,
                None if touch is None else touch.any_structure_touch_count,
            )
        )
        if len(pairs) > 1 or len(triangles) > 1 or conflict:
            break
        if jointly_present:
            current_pair, current_triangle = matched_pair, matched_triangle
            previous_accepted = item.snapshot_id
            gap = 0
        position += increment
    return tuple(rows)


def _diagnostic_class(
    pairs: tuple[Members, ...],
    triangles: tuple[Members, ...],
    pair_changed: bool,
    triangle_changed: bool,
    conflict: bool,
    touch: TouchObservation | None,
) -> str:
    if len(pairs) > 1 and len(triangles) > 1:
        return "ambiguous_both"
    if len(pairs) > 1:
        return "ambiguous_pair"
    if len(triangles) > 1:
        return "ambiguous_triangle"
    if conflict:
        return "structural_conflict"
    if len(pairs) == len(triangles) == 1:
        if pair_changed and triangle_changed:
            return "both_moved"
        if pair_changed:
            return "pair_moved_triangle_static"
        if triangle_changed:
            return "pair_static_triangle_moved"
        if touch is None:
            return "joint_state_unchanged_touch_unknown"
        return (
            "touched_but_joint_state_unchanged"
            if touch.any_structure_touch_count
            else "untouched"
        )
    if len(pairs) == 1 and not triangles:
        return "pair_only_continuation"
    if len(triangles) == 1 and not pairs:
        return "triangle_only_continuation"
    if touch is None:
        return "no_valid_joint_successor_touch_unknown"
    if touch.pair_support_touch_count and touch.triangle_support_touch_count:
        return "both_changed_no_valid_joint_successor"
    if touch.pair_support_touch_count:
        return "pair_changed_no_valid_pair"
    if touch.triangle_support_touch_count:
        return "triangle_changed_no_valid_triangle"
    return "untouched"


def _diagnostic_summary(rows: tuple[SliceDiagnostic, ...]) -> dict[str, Any]:
    classes = Counter(row.diagnostic_class for row in rows)
    touch_known = all(row.any_structure_touch_count is not None for row in rows)
    any_touches = sum(row.any_structure_touch_count or 0 for row in rows)
    pair_touches = sum(row.pair_support_touch_count or 0 for row in rows)
    triangle_touches = sum(row.triangle_support_touch_count or 0 for row in rows)
    touched_slices = sum(bool(row.any_structure_touch_count) for row in rows)
    pair_touched_slices = sum(bool(row.pair_support_touch_count) for row in rows)
    triangle_touched_slices = sum(
        bool(row.triangle_support_touch_count) for row in rows
    )
    movements = sum(row.movement_event for row in rows)
    result = {
        "scanned_god_slices": len(rows),
        "jointly_valid_slices": sum(row.jointly_present for row in rows),
        "movement_event_slices": movements,
        "static_static_count": sum(
            row.jointly_present and not row.pair_changed and not row.triangle_changed
            for row in rows
        ),
        "moved_static_count": classes["pair_moved_triangle_static"],
        "static_moved_count": classes["pair_static_triangle_moved"],
        "moved_moved_count": classes["both_moved"],
        "untouched_count": classes["untouched"],
        "touched_but_joint_state_unchanged_count": classes[
            "touched_but_joint_state_unchanged"
        ],
        "pair_only_continuation_count": classes["pair_only_continuation"],
        "triangle_only_continuation_count": classes["triangle_only_continuation"],
        "pair_only_moved_count": sum(
            row.diagnostic_class == "pair_only_continuation" and row.pair_changed
            for row in rows
        ),
        "triangle_only_moved_count": sum(
            row.diagnostic_class == "triangle_only_continuation"
            and row.triangle_changed
            for row in rows
        ),
        "pair_changed_no_valid_pair_count": classes["pair_changed_no_valid_pair"],
        "triangle_changed_no_valid_triangle_count": classes[
            "triangle_changed_no_valid_triangle"
        ],
        "both_changed_no_valid_joint_successor_count": classes[
            "both_changed_no_valid_joint_successor"
        ],
        "ambiguous_count": sum(value for key, value in classes.items() if key.startswith("ambiguous")),
        "structural_conflict_count": classes["structural_conflict"],
        "tracked_member_active_update_count": _known_sum(
            rows, "tracked_member_active_update_count", touch_known
        ),
        "pair_support_touch_count": pair_touches if touch_known else "N/A",
        "triangle_support_touch_count": triangle_touches if touch_known else "N/A",
        "any_structure_touch_count": any_touches if touch_known else "N/A",
        "P(any_structure_touch per scanned slice)": _probability(
            touched_slices, len(rows)
        ) if touch_known else "N/A",
        "P(movement_event | any_structure_touch)": _probability(
            sum(row.movement_event and bool(row.any_structure_touch_count) for row in rows),
            touched_slices,
        ) if touch_known else "N/A",
        "P(pair moves | pair support touched)": _probability(
            sum(
                row.jointly_present
                and row.pair_changed
                and bool(row.pair_support_touch_count)
                for row in rows
            ),
            pair_touched_slices,
        ) if touch_known else "N/A",
        "P(triangle moves | triangle support touched)": _probability(
            sum(
                row.jointly_present
                and row.triangle_changed
                and bool(row.triangle_support_touch_count)
                for row in rows
            ),
            triangle_touched_slices,
        ) if touch_known else "N/A",
        "P(pair-only continuation | any structure touch)": _probability(
            sum(
                row.diagnostic_class == "pair_only_continuation"
                and bool(row.any_structure_touch_count)
                for row in rows
            ),
            touched_slices,
        ) if touch_known else "N/A",
        "P(triangle-only continuation | any structure touch)": _probability(
            sum(
                row.diagnostic_class == "triangle_only_continuation"
                and bool(row.any_structure_touch_count)
                for row in rows
            ),
            touched_slices,
        ) if touch_known else "N/A",
        "P(joint slice collapsed | any structure touch)": _probability(
            sum(
                row.collapsed_by_joint_requirement
                and bool(row.any_structure_touch_count)
                for row in rows
            ),
            touched_slices,
        ) if touch_known else "N/A",
    }
    return result


def _closed_pairs_and_triples_from_snapshot(
    snapshot: dict[str, Any],
) -> tuple[tuple[Members, ...], tuple[Members, ...]]:
    members = tuple(tuple(sorted(group)) for group in snapshot["members"])
    return (
        tuple(item for item in members if len(item) == 2),
        tuple(item for item in members if len(item) == 3),
    )


def _system_snapshot_row(
    snapshot: dict[str, Any], ticks_per_sweep: int
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "tick": snapshot["tick"],
        "sweep": snapshot["tick"] / ticks_per_sweep,
    }


def _known_sum(rows: tuple[SliceDiagnostic, ...], field: str, known: bool) -> int | str:
    return sum(getattr(row, field) or 0 for row in rows) if known else "N/A"


def _probability(numerator: int, denominator: int) -> float | str:
    return numerator / denominator if denominator else "N/A"


def _serialize(values: Iterable[int] | None) -> str:
    return "" if values is None else "|".join(map(str, sorted(values)))


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fieldnames: list[str],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _touch_markdown(summary: dict[str, Any]) -> str:
    lines = ["# Joint continuation touch summary", ""]
    for name in ("ascending", "descending", "total"):
        item = summary[name]
        lines.extend([f"## {name}", "", "| statistic | value |", "|---|---:|"])
        lines.extend(f"| {key} | {value} |" for key, value in item.items())
        lines.append("")
    return "\n".join(lines)
