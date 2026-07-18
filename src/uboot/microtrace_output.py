"""CSV, JSON, and Markdown serialization for two-snapshot microtraces."""

from __future__ import annotations

import csv
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Iterable

from uboot.microtrace import MicroTraceEvent, MicroTraceResult


def write_microtrace(
    result: MicroTraceResult,
    output_dir: Path,
    *,
    config: dict[str, Any],
    verification: dict[str, Any],
    write_all_atomic_events: bool = False,
    max_event_rows: int | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.mkdir(parents=True)
    selected = []
    previous_state = "exact_static"
    for event in result.events:
        noteworthy = (
            event.any_structure_touch
            or event.micro_state != previous_state
            or event.pair_recognition_changed
            or event.triangle_recognition_changed
            or event.pair_restored_to_initial
            or event.triangle_restored_to_initial
        )
        if write_all_atomic_events or noteworthy:
            selected.append(event)
        previous_state = event.micro_state
    available = len(selected)
    if max_event_rows is not None:
        if max_event_rows < 1:
            raise ValueError("max_event_rows must be positive")
        selected = selected[:max_event_rows]
    event_rows = [_event_row(item) for item in selected]
    event_fields = list(_event_row(result.events[0])) if result.events else []
    _write_csv(
        output_dir / "pair_triangle_microtrace_events.csv",
        event_rows,
        event_fields,
    )
    runs = _state_runs(result.events)
    _write_csv(
        output_dir / "pair_triangle_microtrace_state_runs.csv",
        runs,
        list(runs[0]) if runs else [],
    )
    summary = {
        **result.summary,
        "event_rows_available": available,
        "event_rows_written": len(selected),
        "event_rows_truncated": len(selected) < available,
        "state_run_count": len(runs),
    }
    (output_dir / "pair_triangle_microtrace_config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "pair_triangle_microtrace_verification.json").write_text(
        json.dumps(verification, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output_dir / "pair_triangle_microtrace_summary.md").write_text(
        _summary_markdown(summary, verification), encoding="utf-8"
    )
    return summary


def _event_row(event: MicroTraceEvent) -> dict[str, Any]:
    row = asdict(event)
    for field in (
        "current_pair_before",
        "current_pair_after",
        "current_triangle_before",
        "current_triangle_after",
    ):
        row[field] = _serialize_members(row[field])
    row["pair_candidates"] = ";".join(
        _serialize_members(item) for item in event.pair_candidates
    )
    row["triangle_candidates"] = ";".join(
        _serialize_members(item) for item in event.triangle_candidates
    )
    row["retarget_events"] = json.dumps(
        [asdict(item) for item in event.retarget_events], separators=(",", ":")
    )
    return row


def _state_runs(events: tuple[MicroTraceEvent, ...]) -> list[dict[str, Any]]:
    if not events:
        return []
    runs = []
    start = 0
    for index in range(1, len(events) + 1):
        if index < len(events) and events[index].micro_state == events[start].micro_state:
            continue
        group = events[start:index]
        runs.append(
            {
                "run_index": len(runs) + 1,
                "start_atomic_index": group[0].atomic_index,
                "end_atomic_index": group[-1].atomic_index,
                "start_step": group[0].step_before,
                "end_step": group[-1].step_after,
                "micro_state": group[0].micro_state,
                "atomic_update_count": len(group),
                "structure_touch_count": sum(item.any_structure_touch for item in group),
                "pair_at_run_start": _serialize_members(group[0].current_pair_before),
                "pair_at_run_end": _serialize_members(group[-1].current_pair_after),
                "triangle_at_run_start": _serialize_members(
                    group[0].current_triangle_before
                ),
                "triangle_at_run_end": _serialize_members(
                    group[-1].current_triangle_after
                ),
            }
        )
        start = index
    return runs


def _write_csv(
    path: Path, rows: list[dict[str, Any]], fields: list[str]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _summary_markdown(
    summary: dict[str, Any], verification: dict[str, Any]
) -> str:
    lines = [
        "# Pair-triangle microtrace summary",
        "",
        f"- Atomic updates: {summary['atomic_update_count']}",
        f"- Structure-touch updates: {summary['structure_touch_update_count']}",
        f"- Initial pair ever absent: {summary['initial_pair_ever_absent']}",
        f"- Initial triangle ever absent: {summary['initial_triangle_ever_absent']}",
        f"- Unique movement observed: {summary['ever_moved']}",
        f"- Ambiguity observed: {summary['ever_ambiguous']}",
        f"- Pair break and restore: {summary['pair_break_and_restore']}",
        f"- Triangle break and restore: {summary['triangle_break_and_restore']}",
        f"- Transient pair move: {summary['transient_pair_move']}",
        f"- Transient triangle move: {summary['transient_triangle_move']}",
        f"- End returned to initial members: {summary['end_returned_to_initial_members']}",
        f"- Interval classification: `{summary['classification']}`",
        f"- Deterministic verification passed: {verification['all_checks_pass']}",
        "",
        "## Micro-state counts",
        "",
        "| state | updates |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {state} | {count} |"
        for state, count in summary["micro_state_counts"].items()
    )
    return "\n".join(lines) + "\n"


def _serialize_members(values: Iterable[int]) -> str:
    return "|".join(map(str, values))
