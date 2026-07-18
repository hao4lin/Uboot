"""Output serialization for fixed-point exposure relation traces."""

from __future__ import annotations

import csv
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from uboot.exposure import DirectObjectLink, ExposedSlot, ObjectPath2
from uboot.exposure_trace import (
    ExposureChangeEvent,
    ExposureStateRun,
    ExposureTraceResult,
)


def write_exposure_trace(
    result: ExposureTraceResult,
    output_dir: Path,
    *,
    config: dict[str, Any],
    verification: dict[str, Any],
    write_all_exposure_touches: bool = False,
    max_event_rows: int | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.mkdir(parents=True)
    _write_csv(
        output_dir / "fixed_point_objects.csv",
        _object_rows(result),
        [
            "object_id",
            "object_type",
            "member_ids",
            "internal_support_slot_count",
            "exposed_slot_count",
            "in_radius_two_scope",
        ],
    )
    for label, state in (("start", result.start_state), ("end", result.end_state)):
        _write_csv(
            output_dir / f"fixed_point_exposed_slots_{label}.csv",
            [_exposed_row(item) for item in state.exposed_slots],
            list(ExposedSlot.__dataclass_fields__),
        )
        _write_dataclasses(
            output_dir / f"fixed_point_direct_links_{label}.csv",
            state.direct_links,
            list(DirectObjectLink.__dataclass_fields__),
        )
        _write_dataclasses(
            output_dir / f"fixed_point_two_hop_paths_{label}.csv",
            state.two_hop_paths,
            list(ObjectPath2.__dataclass_fields__),
        )
    selected = [
        item
        for item in result.events
        if write_all_exposure_touches
        or item.level1_micro_exposure_changed
        or item.level2_object_link_changed
        or item.level3_two_hop_changed
    ]
    available = len(selected)
    if max_event_rows is not None:
        if max_event_rows < 1:
            raise ValueError("max_event_rows must be positive")
        selected = selected[:max_event_rows]
    event_rows = [_event_row(item) for item in selected]
    event_fields = list(ExposureChangeEvent.__dataclass_fields__)
    _write_csv(
        output_dir / "fixed_point_exposure_events.csv", event_rows, event_fields
    )
    _write_dataclasses(
        output_dir / "fixed_point_exposure_state_runs.csv",
        result.state_runs,
        list(ExposureStateRun.__dataclass_fields__),
    )
    summary = {
        **result.summary,
        "event_rows_available": available,
        "event_rows_written": len(selected),
        "event_rows_truncated": len(selected) < available,
        "state_run_count": len(result.state_runs),
    }
    (output_dir / "fixed_point_exposure_config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "fixed_point_exposure_verification.json").write_text(
        json.dumps(verification, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "fixed_point_exposure_summary.md").write_text(
        _summary_markdown(summary, verification), encoding="utf-8"
    )
    return summary


def _object_rows(result: ExposureTraceResult) -> list[dict[str, Any]]:
    supports = dict(result.start_state.internal_support_slots)
    exposed = {
        object_id: sum(
            item.object_id == object_id for item in result.start_state.exposed_slots
        )
        for object_id in result.index.by_id
    }
    scope = set(result.start_state.scope_object_ids)
    return [
        {
            "object_id": item.object_id,
            "object_type": item.object_type,
            "member_ids": "|".join(map(str, item.members)),
            "internal_support_slot_count": len(supports[item.object_id]),
            "exposed_slot_count": exposed[item.object_id],
            "in_radius_two_scope": item.object_id in scope,
        }
        for item in result.index.objects
    ]


def _exposed_row(item) -> dict[str, Any]:
    row = asdict(item)
    row["target_object_ids"] = "|".join(item.target_object_ids)
    row["target_object_member_ids"] = "|".join(
        map(str, item.target_object_member_ids)
    )
    row["target_member_roles"] = "|".join(item.target_member_roles)
    return row


def _event_row(item: ExposureChangeEvent) -> dict[str, Any]:
    row = asdict(item)
    for field in ("old_target_object_ids", "new_target_object_ids", "change_classes", "affected_object_ids"):
        row[field] = "|".join(row[field])
    for field in (
        "affected_direct_links_before",
        "affected_direct_links_after",
        "affected_two_hop_paths_before",
        "affected_two_hop_paths_after",
        "retarget_events",
    ):
        row[field] = json.dumps(row[field], separators=(",", ":"))
    return row


def _write_dataclasses(path: Path, values, fields: list[str] | None = None) -> None:
    rows = [asdict(item) for item in values]
    _write_csv(path, rows, fields or (list(rows[0]) if rows else []))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _summary_markdown(
    summary: dict[str, Any], verification: dict[str, Any]
) -> str:
    keys = (
        "atomic_update_count",
        "level0_internal_change_count",
        "total_internal_support_slot_count",
        "start_exposed_slot_count",
        "end_exposed_slot_count",
        "start_direct_object_link_count",
        "end_direct_object_link_count",
        "start_two_hop_path_count",
        "end_two_hop_path_count",
        "exposed_slot_touch_count",
        "raw_target_change_count",
        "same_target_object_micro_rewire_count",
        "source_slot_reassignment_count",
        "direct_object_link_create_count",
        "direct_object_link_break_count",
        "direct_object_neighbor_change_count",
        "direct_object_multiplicity_change_count",
        "two_hop_path_create_count",
        "two_hop_path_break_count",
        "two_hop_path_change_count",
        "gap_transfer_candidate_count",
        "external_rewire_chain_count",
        "objects_with_micro_exposure_change",
        "objects_with_direct_neighbor_change",
        "objects_with_two_hop_change",
        "classification",
    )
    lines = [
        "# Fixed-point exposure relation summary",
        "",
        f"- Internal fixed-point invariant preserved: {summary['level0_internal_change_count'] == 0}",
        f"- Direct/micro/RNG verification passed: {verification['all_checks_pass']}",
        "",
        "| statistic | value |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {summary[key]} |" for key in keys)
    lines.extend(
        [
            "",
            "## Empirical ratios",
            "",
            "| ratio | value |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| {key} | {value} |"
        for key, value in summary.items()
        if key.startswith("P(")
    )
    return "\n".join(lines) + "\n"
