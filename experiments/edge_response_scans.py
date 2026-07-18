"""Run mode, worker-capacity, and snapshot-interval validation scans."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
from time import monotonic

from uboot.snapshot_lineage import analyze_lineages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--N", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--background-sweeps", type=int, default=5000)
    parser.add_argument("--queue-capacity", type=int, default=1024)
    parser.add_argument("--queue-policy", choices=["reject", "drop_oldest"], default="reject")
    parser.add_argument("--output-dir", default="artifacts/edge_response_scans")
    args = parser.parse_args()
    root = Path(args.output_dir)

    mode_rows = []
    modes = (("M0", 1, False), ("M1", 1, False), ("M1", 1, True))
    for profile, workers, stats in modes:
        name = f"{profile}_workers{workers}_{'stats_on' if stats else 'stats_off'}"
        destination = root / "modes" / name
        runtime = _run(args, destination, profile, workers, 100, stats)
        summary = _json(destination / "edge_response_summary.json")
        mode_rows.append(_summary_row(summary, runtime) | {"stats_enabled": stats})
    _write(root / "mode_experiment.csv", mode_rows)

    worker_rows = []
    for workers in (1, 2, 4, 8):
        destination = root / "workers" / str(workers)
        runtime = _run(args, destination, "M1", workers, 100, True)
        summary = _json(destination / "edge_response_summary.json")
        worker_rows.append(_summary_row(summary, runtime))
    _write(root / "worker_count_experiment.csv", worker_rows)

    interval_rows = []
    for interval in (10, 30, 100, 300, 1000):
        destination = root / "intervals" / str(interval)
        _run(args, destination, "M1", 1, interval, True)
        snapshots = list(
            csv.DictReader((destination / "system_snapshots.csv").open())
        )
        has_pair_or_triple = any(
            int(row["closed_pair_count"]) + int(row["closed_triple_count"]) > 0
            for row in snapshots
        )
        whole_only = all(
            row["whole_network_object_present"] == "True" for row in snapshots
        )
        if whole_only:
            status = "degenerate_whole_network_object"
            analysis = {}
        elif not has_pair_or_triple:
            status = "no_closed_pair_or_triple"
            analysis = {}
        else:
            analysis = analyze_lineages(
                destination / "closed_object_snapshots.csv",
                destination / "lineage",
            )
            status = str(analysis["lineage_validation_status"])
        final = snapshots[-1]
        interval_rows.append(
            {
                "snapshot_interval_sweeps": interval,
                "snapshot_count": len(snapshots),
                "lineage_validation_status": status,
                "final_closed_pair_count": final["closed_pair_count"],
                "final_closed_triple_count": final["closed_triple_count"],
                "final_closed_larger_count": final["closed_larger_count"],
                "final_max_closed_candidate_scc_size": final[
                    "max_closed_candidate_scc_size"
                ],
                "whole_network_object_present": final[
                    "whole_network_object_present"
                ],
                "primary_matches": analysis.get("primary_matches", ""),
                "unmatched_births": analysis.get("unmatched_births", ""),
                "unmatched_deaths": analysis.get("unmatched_deaths", ""),
                "split_candidates": analysis.get("split_candidates", ""),
                "merge_candidates": analysis.get("merge_candidates", ""),
            }
        )
    _write(root / "snapshot_interval_experiment.csv", interval_rows)


def _run(
    args: argparse.Namespace,
    destination: Path,
    profile: str,
    workers: int,
    interval: int,
    stats: bool,
) -> float:
    runner = Path(__file__).with_name("edge_response.py")
    command = [
        sys.executable,
        str(runner),
        "--profile",
        profile,
        "--N",
        str(args.N),
        "--seed",
        str(args.seed),
        "--background-sweeps",
        str(args.background_sweeps),
        "--worker-count",
        str(workers),
        "--response-queue-capacity",
        str(args.queue_capacity),
        "--response-queue-policy",
        args.queue_policy,
        "--snapshot-interval-sweeps",
        str(interval),
        "--output-dir",
        str(destination),
    ]
    if stats:
        command.extend(
            (
                "--enable-snapshots",
                "--enable-worker-stats",
                "--enable-closed-object-snapshots",
                "--enable-response-histograms",
            )
        )
    started = monotonic()
    subprocess.run(command, check=True)
    return monotonic() - started


def _summary_row(summary: dict, runtime: float) -> dict:
    return {
        "profile": summary["profile"],
        "worker_count": summary.get("worker_count", 0),
        "seed": summary["seed"],
        "N": summary["N"],
        "final_tick": summary["tick"],
        "final_state_hash": summary["final_state_hash"],
        "all_mutual_pair_density_per_slot": summary[
            "all_mutual_pair_density_per_slot"
        ],
        "responses_created": summary.get("responses_created_total", 0),
        "responses_completed": summary.get("responses_completed_total", 0),
        "responses_rejected": summary.get("responses_rejected_total", 0),
        "max_concurrent_workers": summary.get("max_concurrent_workers", 0),
        "mean_chain_length": summary["mean_completed_chain_length"],
        "mean_lifetime_ticks": summary["mean_completed_lifetime_ticks"],
        "p50_lifetime_ticks": summary["p50_lifetime_ticks"],
        "p90_lifetime_ticks": summary["p90_lifetime_ticks"],
        "p99_lifetime_ticks": summary["p99_lifetime_ticks"],
        "mean_queue_depth": summary["mean_queue_depth"],
        "max_queue_depth": summary["max_queue_depth"],
        "runtime_seconds": runtime,
    }


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
