"""Run one configurable fair-slot edge-response experiment."""

from __future__ import annotations

import argparse
import csv
import json
from hashlib import sha256
from pathlib import Path
from random import Random
import sys
from time import monotonic
from typing import Any

from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.snapshot_lineage import closed_object_rows, graph_diagnostics, system_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["M0", "M1", "M2", "M3", "M4", "M5", "M6", "LEGACY"], default="M0")
    parser.add_argument("--N", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--background-sweeps", type=int, default=1_000)
    parser.add_argument("--snapshot-interval-sweeps", type=int, default=100)
    parser.add_argument("--worker-count", type=int, default=1)
    parser.add_argument("--response-queue-capacity", type=int, default=1024)
    parser.add_argument(
        "--response-queue-policy", choices=["reject", "drop_oldest"], default="reject"
    )
    parser.add_argument("--object-member-inline-limit", type=int, default=32)
    parser.add_argument("--enable-snapshots", action="store_true")
    parser.add_argument("--enable-worker-stats", action="store_true")
    parser.add_argument("--enable-closed-object-snapshots", action="store_true")
    parser.add_argument("--enable-response-histograms", action="store_true")
    parser.add_argument("--enable-graph-diagnostics", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--p-incoming", type=float)
    parser.add_argument("--p-same-slot-given-incoming", type=float)
    parser.add_argument("--p-respond-no-return", type=float)
    parser.add_argument("--p-respond-same-return", type=float)
    parser.add_argument("--p-respond-other-return", type=float)
    parser.add_argument("--p-respond-multi-return", type=float)
    parser.add_argument("--response-slot-mode-no-return")
    parser.add_argument("--response-slot-mode-same-return")
    parser.add_argument("--response-slot-mode-other-return")
    parser.add_argument("--response-slot-mode-multi-return")
    parser.add_argument("--response-target-mode")
    parser.add_argument("--max-chain-depth", type=int)
    parser.add_argument("--scheduler-mode", choices=["tick"])
    parser.add_argument("--p-process-response", type=float)
    parser.add_argument("--output-dir", default="artifacts/edge_response")
    args = parser.parse_args()
    if args.snapshot_interval_sweeps < 1:
        parser.error("--snapshot-interval-sweeps must be positive")
    if args.worker_count < 0:
        parser.error("--worker-count cannot be negative")
    if args.profile != "M0" and args.worker_count < 1:
        parser.error("M1-family modes require --worker-count >= 1")
    overrides: dict[str, Any] = {}
    if args.config:
        overrides = json.loads(Path(args.config).read_text(encoding="utf-8"))
    selection = overrides.setdefault("selection", {})
    response = overrides.setdefault("response", {})
    _set(selection, "p_incoming", args.p_incoming)
    if args.p_incoming is not None:
        selection["p_global_explore"] = 1 - args.p_incoming
    _set(
        selection,
        "p_same_slot_given_incoming",
        args.p_same_slot_given_incoming,
    )
    for key in (
        "p_respond_no_return",
        "p_respond_same_return",
        "p_respond_other_return",
        "p_respond_multi_return",
    ):
        _set(
            response,
            f"p_{key.removeprefix('p_respond_')}",
            getattr(args, key),
        )
    for suffix in ("no_return", "same_return", "other_return", "multi_return"):
        _set(response, f"slot_{suffix}", getattr(args, f"response_slot_mode_{suffix}"))
    _set(response, "target_mode", args.response_target_mode)
    _set(response, "max_chain_depth", args.max_chain_depth)
    _set(overrides, "scheduler_mode", args.scheduler_mode)
    _set(overrides, "p_process_response", args.p_process_response)
    rng = Random(args.seed)
    engine = EdgeResponseEngine(
        distinct_random_network(args.N, rng),
        policy_profile(args.profile, **overrides),
        rng,
        worker_count=args.worker_count,
        response_queue_capacity=args.response_queue_capacity,
        response_queue_policy=args.response_queue_policy,
        enable_worker_stats=args.enable_worker_stats,
        enable_response_histograms=args.enable_response_histograms,
        mode="M0" if args.profile == "M0" else "M1",
    )
    last_progress = monotonic()
    snapshot_interval = args.snapshot_interval_sweeps * 3 * args.N
    next_snapshot = snapshot_interval
    snapshots: list[dict[str, Any]] = []
    object_snapshots: list[dict[str, Any]] = []
    diagnostic_snapshots: list[dict[str, Any]] = []
    snapshot_id = 0
    last_snapshot_active = -1

    def capture_snapshot(completed: int) -> None:
        nonlocal snapshot_id
        nonlocal last_snapshot_active
        if completed == last_snapshot_active:
            return
        network = engine.snapshot()
        summary = engine.summary()
        candidate_graph = engine.candidate_graph()
        objects = closed_object_rows(
            network,
            candidate_graph,
            snapshot_id,
            completed,
            member_inline_limit=args.object_member_inline_limit,
        )
        if args.enable_closed_object_snapshots:
            object_snapshots.extend(objects)
        if args.enable_snapshots:
            snapshots.append(
                system_snapshot(
                    network,
                    candidate_graph,
                    snapshot_id,
                    completed,
                    summary,
                    objects,
                )
            )
        if args.enable_graph_diagnostics:
            diagnostic_snapshots.append(
                {
                    "snapshot_id": snapshot_id,
                    "tick": completed,
                    **graph_diagnostics(network, candidate_graph),
                }
            )
        snapshot_id += 1
        last_snapshot_active = completed

    def report_progress(
        completed: int, total: int, responses: int, queue_length: int
    ) -> None:
        nonlocal last_progress
        now = monotonic()
        finished = completed == total and queue_length == 0
        if now - last_progress < 60 and not finished:
            return
        percent = 100.0 if total == 0 else 100 * completed / total
        print(
            f"progress: {percent:6.2f}% "
            f"({completed}/{total} active slots) "
            f"responses={responses} queue={queue_length}",
            file=sys.stderr,
            flush=True,
        )
        last_progress = now

    if (
        args.enable_snapshots
        or args.enable_closed_object_snapshots
        or args.enable_graph_diagnostics
    ):
        capture_snapshot(0)
    def on_tick(tick: int) -> None:
        nonlocal next_snapshot
        if tick == next_snapshot:
            capture_snapshot(tick)
            next_snapshot += snapshot_interval

    snapshot_enabled = (
        args.enable_snapshots
        or args.enable_closed_object_snapshots
        or args.enable_graph_diagnostics
    )
    engine.run(
        args.background_sweeps,
        report_progress,
        tick_callback=on_tick if snapshot_enabled else None,
    )
    if snapshot_enabled:
        capture_snapshot(engine.tick)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = {
        "profile": args.profile,
        "N": args.N,
        "seed": args.seed,
        "background_sweeps": args.background_sweeps,
        "final_state_hash": sha256(repr(engine.dynamics_state()).encode()).hexdigest(),
        **engine.summary(),
    }
    (output / "edge_response_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    if args.enable_snapshots:
        _write_rows(output / "system_snapshots.csv", snapshots)
        _write_rows(output / "edge_response_snapshots.csv", snapshots)
    if args.enable_closed_object_snapshots:
        _write_rows(output / "closed_object_snapshots.csv", object_snapshots)
    if args.enable_worker_stats or args.enable_response_histograms:
        _write_rows(
            output / "response_by_tentacle_summary.csv",
            engine.response_histogram_rows(),
        )
    if args.enable_graph_diagnostics:
        _write_rows(output / "graph_diagnostics.csv", diagnostic_snapshots)
    with (output / "edge_response_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)
    (output / "edge_response_experiment_report.md").write_text(
        "# Edge-response experiment report\n\n"
        f"Profile: {args.profile}; N={args.N}; seed={args.seed}.\n\n"
        "No physical interpretation is assigned. See edge_response_summary.json "
        "and edge_response_snapshots.csv. Continuous event recording is disabled.\n",
        encoding="utf-8")
    audit = Path(__file__).parents[1] / "docs" / "edge_response_logic_audit.md"
    (output / "edge_response_logic_audit.md").write_text(
        audit.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _set(mapping: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        mapping[key] = value


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        fields = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
