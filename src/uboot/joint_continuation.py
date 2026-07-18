"""Offline joint continuation tracking for one closed pair and one closed triple."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import json
from itertools import combinations
from pathlib import Path
from random import Random
from statistics import fmean, median
from typing import Any, Iterable


Members = tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GodSlice:
    snapshot_id: int
    tick: int | None
    sweep: float | None
    pairs: tuple[Members, ...]
    triples: tuple[Members, ...]


@dataclass(frozen=True, slots=True)
class TrackedSlice:
    snapshot_id: int
    tick: int | None
    sweep: float | None
    pair_ids: Members
    triangle_ids: Members
    previous_pair_ids: Members | None
    previous_triangle_ids: Members | None
    pair_shared_ids: Members
    triangle_shared_ids: Members
    pair_changed: bool
    triangle_changed: bool
    skipped_god_slices_since_previous: int


@dataclass(frozen=True, slots=True)
class TrackEvent:
    direction: str
    snapshot_id: int | None
    event_type: str
    current_pair: Members
    current_triangle: Members
    candidate_pairs: tuple[Members, ...] = ()
    candidate_triangles: tuple[Members, ...] = ()
    details: str = ""


@dataclass(frozen=True, slots=True)
class DirectionTrackResult:
    direction: str
    start_snapshot_id: int
    tracked_slices: tuple[TrackedSlice, ...]
    events: tuple[TrackEvent, ...]
    stop_reason: str
    stop_snapshot_id: int | None
    scanned_god_slice_count: int
    accepted_slice_count: int
    rejected_slice_count: int
    ambiguous_pair_count: int
    ambiguous_triangle_count: int
    structural_conflict_count: int


@dataclass(frozen=True, slots=True)
class JointTrackResult:
    start_snapshot_id: int
    start_pair_ids: Members
    start_triangle_ids: Members
    ascending: DirectionTrackResult
    descending: DirectionTrackResult


class SnapshotSeries:
    """Closed pair/triple objects indexed by the complete recorded slice order."""

    def __init__(self, slices: Iterable[GodSlice]) -> None:
        ordered = sorted(slices, key=lambda item: item.snapshot_id)
        ids = [item.snapshot_id for item in ordered]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot_id values must be unique within one series")
        self.slices = tuple(ordered)
        self.by_id = {item.snapshot_id: item for item in ordered}
        self._positions = {item.snapshot_id: index for index, item in enumerate(ordered)}
        self._pair_indexes = tuple(_member_index(item.pairs) for item in ordered)
        self._triangle_indexes = tuple(_triangle_index(item.triples) for item in ordered)

    @classmethod
    def from_csv(cls, system_path: Path, object_path: Path) -> "SnapshotSeries":
        system_rows: dict[int, dict[str, str]] = {}
        with system_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                snapshot_id = int(row["snapshot_id"])
                if snapshot_id in system_rows:
                    raise ValueError(f"duplicate system snapshot_id: {snapshot_id}")
                system_rows[snapshot_id] = row
        objects: dict[int, dict[str, list[Members]]] = {
            snapshot_id: {"pair": [], "triple": []} for snapshot_id in system_rows
        }
        with object_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                object_class = row.get("object_class")
                if object_class not in {"pair", "triple"}:
                    continue
                snapshot_id = int(row["snapshot_id"])
                if snapshot_id not in objects:
                    raise ValueError(
                        f"object snapshot_id {snapshot_id} is absent from system snapshots"
                    )
                members = _parse_members(row.get("member_ids", ""))
                expected = 2 if object_class == "pair" else 3
                if len(members) != expected:
                    raise ValueError(
                        f"{object_class} at snapshot {snapshot_id} has {len(members)} members"
                    )
                objects[snapshot_id][object_class].append(members)
        slices = []
        for snapshot_id, row in system_rows.items():
            slices.append(
                GodSlice(
                    snapshot_id=snapshot_id,
                    tick=_optional_int(row.get("tick")),
                    sweep=_optional_float(row.get("sweep")),
                    pairs=tuple(sorted(set(objects[snapshot_id]["pair"]))),
                    triples=tuple(sorted(set(objects[snapshot_id]["triple"]))),
                )
            )
        return cls(slices)

    @classmethod
    def from_csv_pairs(
        cls, paths: Iterable[tuple[Path, Path]]
    ) -> "SnapshotSeries":
        """Combine chunk files, deduplicating their identical boundary slices."""
        combined: dict[int, GodSlice] = {}
        for system_path, object_path in paths:
            for item in cls.from_csv(system_path, object_path).slices:
                previous = combined.get(item.snapshot_id)
                if previous is not None and previous != item:
                    raise ValueError(
                        f"conflicting duplicate snapshot_id across chunks: {item.snapshot_id}"
                    )
                combined[item.snapshot_id] = item
        if not combined:
            raise ValueError("no snapshot chunks were found")
        return cls(combined.values())

    def candidate_pairs(self, position: int, current: Members) -> tuple[Members, ...]:
        index = self._pair_indexes[position]
        return tuple(sorted({item for member in current for item in index.get(member, ())}))

    def candidate_triangles(
        self, position: int, current: Members
    ) -> tuple[Members, ...]:
        index = self._triangle_indexes[position]
        keys = combinations(current, 2)
        return tuple(sorted({item for key in keys for item in index.get(tuple(key), ())}))


def track_joint_continuation(
    series: SnapshotSeries,
    *,
    start_snapshot_id: int,
    pair_ids: Iterable[int],
    triangle_ids: Iterable[int],
    max_god_slices: int | None = None,
) -> JointTrackResult:
    pair = _normalize_members(pair_ids, 2, "pair")
    triangle = _normalize_members(triangle_ids, 3, "triangle")
    if pair and set(pair) & set(triangle):
        raise ValueError("start pair and triple must be disjoint")
    if start_snapshot_id not in series.by_id:
        raise ValueError("start snapshot_id is absent from the input series")
    start = series.by_id[start_snapshot_id]
    if pair not in start.pairs:
        raise ValueError("specified pair is not a closed pair in the start snapshot")
    if triangle not in start.triples:
        raise ValueError("specified triangle is not a closed triple in the start snapshot")
    position = series._positions[start_snapshot_id]
    return JointTrackResult(
        start_snapshot_id=start_snapshot_id,
        start_pair_ids=pair,
        start_triangle_ids=triangle,
        ascending=_track_direction(
            series, position, pair, triangle, 1, "ascending_scan", max_god_slices
        ),
        descending=_track_direction(
            series, position, pair, triangle, -1, "descending_scan", max_god_slices
        ),
    )


def auto_select_and_track(
    series: SnapshotSeries,
    *,
    start_snapshot_id: int,
    seed: int,
    max_attempts: int,
    max_god_slices: int | None = None,
) -> tuple[JointTrackResult, list[dict[str, Any]]]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if start_snapshot_id not in series.by_id:
        raise ValueError("start snapshot_id is absent from the input series")
    start = series.by_id[start_snapshot_id]
    choices = [
        (pair, triangle)
        for pair in start.pairs
        for triangle in start.triples
        if not set(pair) & set(triangle)
    ]
    Random(seed).shuffle(choices)
    attempts: list[dict[str, Any]] = []
    for pair, triangle in choices[:max_attempts]:
        result = track_joint_continuation(
            series,
            start_snapshot_id=start_snapshot_id,
            pair_ids=pair,
            triangle_ids=triangle,
            max_god_slices=max_god_slices,
        )
        useful = (
            result.ascending.accepted_slice_count > 1
            or result.descending.accepted_slice_count > 1
        )
        attempts.append(
            {
                "pair_ids": "|".join(map(str, pair)),
                "triangle_ids": "|".join(map(str, triangle)),
                "accepted": useful,
                "ascending_stop_reason": result.ascending.stop_reason,
                "descending_stop_reason": result.descending.stop_reason,
            }
        )
        if useful:
            return result, attempts
    raise ValueError(
        f"no candidate produced a continuation in {min(max_attempts, len(choices))} attempts"
    )


def write_joint_result(
    result: JointTrackResult,
    output_dir: Path,
    *,
    config: dict[str, Any],
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    directions = (result.ascending, result.descending)
    slice_rows = []
    event_rows = []
    for direction in directions:
        for track_index, item in enumerate(direction.tracked_slices):
            row = asdict(item)
            row.update(
                direction=direction.direction,
                track_index=track_index,
                pair_id_1=item.pair_ids[0],
                pair_id_2=item.pair_ids[1],
                triangle_id_1=item.triangle_ids[0],
                triangle_id_2=item.triangle_ids[1],
                triangle_id_3=item.triangle_ids[2],
                pair_shared_count=len(item.pair_shared_ids),
                triangle_shared_count=len(item.triangle_shared_ids),
                skipped_god_slices=item.skipped_god_slices_since_previous,
            )
            for field in (
                "pair_ids",
                "triangle_ids",
                "previous_pair_ids",
                "previous_triangle_ids",
                "pair_shared_ids",
                "triangle_shared_ids",
            ):
                row[field] = _serialize_members(row[field])
            slice_rows.append(row)
        for event in direction.events:
            row = asdict(event)
            row["current_pair"] = _serialize_members(row["current_pair"])
            row["current_triangle"] = _serialize_members(row["current_triangle"])
            row["candidate_pairs"] = _serialize_candidates(row["candidate_pairs"])
            row["candidate_triangles"] = _serialize_candidates(
                row["candidate_triangles"]
            )
            event_rows.append(row)
    summary = {
        "start_snapshot_id": result.start_snapshot_id,
        "start_pair_ids": list(result.start_pair_ids),
        "start_triangle_ids": list(result.start_triangle_ids),
        "ascending": _direction_summary(result.ascending),
        "descending": _direction_summary(result.descending),
        "auto_selection_attempts": attempts or [],
    }
    _write_csv(output_dir / "joint_fixed_point_track_slices.csv", slice_rows)
    _write_csv(output_dir / "joint_fixed_point_track_events.csv", event_rows)
    if attempts:
        _write_csv(output_dir / "joint_fixed_point_track_attempts.csv", attempts)
    (output_dir / "joint_fixed_point_track_config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "joint_fixed_point_track_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "joint_fixed_point_track_summary.md").write_text(
        _markdown_summary(summary), encoding="utf-8"
    )
    return summary


def _track_direction(
    series: SnapshotSeries,
    start_position: int,
    start_pair: Members,
    start_triangle: Members,
    increment: int,
    direction: str,
    max_god_slices: int | None,
) -> DirectionTrackResult:
    start = series.slices[start_position]
    tracked = [
        TrackedSlice(
            start.snapshot_id,
            start.tick,
            start.sweep,
            start_pair,
            start_triangle,
            None,
            None,
            (),
            (),
            False,
            False,
            0,
        )
    ]
    events: list[TrackEvent] = []
    current_pair, current_triangle = start_pair, start_triangle
    scanned = 1
    rejected = 0
    skipped = 0
    ambiguous_pairs = 0
    ambiguous_triangles = 0
    conflicts = 0
    stop_reason = "end_of_snapshots"
    stop_snapshot_id: int | None = None
    position = start_position + increment
    hit_limit = False
    while 0 <= position < len(series.slices):
        if max_god_slices is not None and scanned >= max_god_slices:
            hit_limit = True
            break
        item = series.slices[position]
        scanned += 1
        pairs = series.candidate_pairs(position, current_pair)
        triangles = series.candidate_triangles(position, current_triangle)
        if len(pairs) > 1 or len(triangles) > 1:
            ambiguous_pairs += len(pairs) > 1
            ambiguous_triangles += len(triangles) > 1
            if len(pairs) > 1 and len(triangles) > 1:
                stop_reason = "ambiguous_both"
            elif len(pairs) > 1:
                stop_reason = "ambiguous_pair_match"
            else:
                stop_reason = "ambiguous_triangle_match"
            stop_snapshot_id = item.snapshot_id
            events.append(
                TrackEvent(
                    direction,
                    item.snapshot_id,
                    stop_reason,
                    current_pair,
                    current_triangle,
                    pairs,
                    triangles,
                )
            )
            break
        if not pairs or not triangles:
            rejected += 1
            skipped += 1
            position += increment
            continue
        matched_pair, matched_triangle = pairs[0], triangles[0]
        if set(matched_pair) & set(matched_triangle):
            conflicts += 1
            stop_reason = "structural_conflict"
            stop_snapshot_id = item.snapshot_id
            events.append(
                TrackEvent(
                    direction,
                    item.snapshot_id,
                    stop_reason,
                    current_pair,
                    current_triangle,
                    pairs,
                    triangles,
                    "matched pair and triple overlap",
                )
            )
            break
        pair_shared = tuple(sorted(set(current_pair) & set(matched_pair)))
        triangle_shared = tuple(
            sorted(set(current_triangle) & set(matched_triangle))
        )
        tracked.append(
            TrackedSlice(
                item.snapshot_id,
                item.tick,
                item.sweep,
                matched_pair,
                matched_triangle,
                current_pair,
                current_triangle,
                pair_shared,
                triangle_shared,
                matched_pair != current_pair,
                matched_triangle != current_triangle,
                skipped,
            )
        )
        current_pair, current_triangle = matched_pair, matched_triangle
        skipped = 0
        position += increment
    else:
        stop_reason = "end_of_snapshots" if len(tracked) > 1 else "no_valid_continuation"
        stop_snapshot_id = series.slices[-1 if increment > 0 else 0].snapshot_id
    if hit_limit:
        stop_reason = "max_scan_limit"
        stop_snapshot_id = series.slices[position].snapshot_id
    if not events or events[-1].event_type != stop_reason:
        events.append(
            TrackEvent(
                direction,
                stop_snapshot_id,
                stop_reason,
                current_pair,
                current_triangle,
                details="direction tracking stopped",
            )
        )
    return DirectionTrackResult(
        direction,
        start.snapshot_id,
        tuple(tracked),
        tuple(events),
        stop_reason,
        stop_snapshot_id,
        scanned,
        len(tracked),
        rejected,
        ambiguous_pairs,
        ambiguous_triangles,
        conflicts,
    )


def _direction_summary(result: DirectionTrackResult) -> dict[str, Any]:
    transitions = result.tracked_slices[1:]
    gaps = [item.skipped_god_slices_since_previous for item in transitions]
    joint = {
        "both_unchanged": 0,
        "pair_changed_triangle_unchanged": 0,
        "pair_unchanged_triangle_changed": 0,
        "both_changed": 0,
    }
    for item in transitions:
        if item.pair_changed and item.triangle_changed:
            joint["both_changed"] += 1
        elif item.pair_changed:
            joint["pair_changed_triangle_unchanged"] += 1
        elif item.triangle_changed:
            joint["pair_unchanged_triangle_changed"] += 1
        else:
            joint["both_unchanged"] += 1
    return {
        "scanned_god_slice_count": result.scanned_god_slice_count,
        "accepted_slice_count": result.accepted_slice_count,
        "rejected_slice_count": result.rejected_slice_count,
        "valid_slice_rate": result.accepted_slice_count
        / result.scanned_god_slice_count,
        "pair_unchanged_count": sum(not item.pair_changed for item in transitions),
        "pair_changed_count": sum(item.pair_changed for item in transitions),
        "triangle_unchanged_count": sum(
            not item.triangle_changed for item in transitions
        ),
        "triangle_changed_count": sum(item.triangle_changed for item in transitions),
        **joint,
        "skipped_god_slice_histogram": {
            str(value): gaps.count(value) for value in sorted(set(gaps))
        },
        "max_skipped_god_slices": max(gaps, default=0),
        "mean_skipped_god_slices": fmean(gaps) if gaps else 0,
        "median_skipped_god_slices": median(gaps) if gaps else 0,
        "trajectory_length": len(result.tracked_slices),
        "stop_reason": result.stop_reason,
        "stop_snapshot_id": result.stop_snapshot_id,
        "ambiguous_pair_count": result.ambiguous_pair_count,
        "ambiguous_triangle_count": result.ambiguous_triangle_count,
        "structural_conflict_count": result.structural_conflict_count,
    }


def _member_index(items: tuple[Members, ...]) -> dict[int, tuple[Members, ...]]:
    mutable: dict[int, list[Members]] = {}
    for item in items:
        for member in item:
            mutable.setdefault(member, []).append(item)
    return {key: tuple(values) for key, values in mutable.items()}


def _triangle_index(items: tuple[Members, ...]) -> dict[Members, tuple[Members, ...]]:
    mutable: dict[Members, list[Members]] = {}
    for item in items:
        for key in combinations(item, 2):
            mutable.setdefault(tuple(key), []).append(item)
    return {key: tuple(values) for key, values in mutable.items()}


def _normalize_members(values: Iterable[int], size: int, label: str) -> Members:
    members = tuple(sorted(set(values)))
    if len(members) != size:
        raise ValueError(f"{label} must contain exactly {size} distinct IDs")
    return members


def _parse_members(value: str) -> Members:
    return tuple(sorted(int(item) for item in value.split("|") if item != ""))


def _optional_int(value: str | None) -> int | None:
    return int(value) if value not in {None, ""} else None


def _optional_float(value: str | None) -> float | None:
    return float(value) if value not in {None, ""} else None


def _serialize_members(values: Iterable[int] | None) -> str:
    return "" if values is None else "|".join(map(str, values))


def _serialize_candidates(values: Iterable[Iterable[int]]) -> str:
    return ";".join(_serialize_members(value) for value in values)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Joint pair-triple continuation summary",
        "",
        f"Start snapshot: {summary['start_snapshot_id']}",
        f"Start pair: {'|'.join(map(str, summary['start_pair_ids']))}",
        f"Start triple: {'|'.join(map(str, summary['start_triangle_ids']))}",
        "",
        "Raw snapshot spacing is an index interval, not physical time.",
    ]
    for name in ("ascending", "descending"):
        item = summary[name]
        lines.extend(
            [
                "",
                f"## {name}",
                "",
                f"- God slices scanned: {item['scanned_god_slice_count']}",
                f"- Effective slices accepted: {item['accepted_slice_count']}",
                f"- Intermediate slices collapsed: {item['rejected_slice_count']}",
                f"- Trajectory length: {item['trajectory_length']}",
                f"- Stop: {item['stop_reason']} at {item['stop_snapshot_id']}",
            ]
        )
    return "\n".join(lines) + "\n"
