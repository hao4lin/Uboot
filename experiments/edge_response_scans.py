"""Run bounded worker-count and snapshot-interval validation scans."""

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
    parser.add_argument("--background-sweeps", type=int, default=100)
    parser.add_argument("--output-dir", default="artifacts/edge_response_scans")
    args = parser.parse_args()
    root = Path(args.output_dir)
    worker_rows = []
    for workers in (0, 1, 2, 4, 8):
        destination = root / "workers" / str(workers)
        elapsed = _run(args, destination, workers, 30)
        summary = _json(destination / "edge_response_summary.json")
        final = list(csv.DictReader((destination / "system_snapshots.csv").open()))[-1]
        worker_rows.append({"worker_count": workers, "seed": args.seed, "N": args.N,
            "final_sweep": final["sweep"], "active_slot_count": final["active_slot_count"],
            "mutual_density": final["mutual_density"], "closed_pair_count": final["closed_pair_count"],
            "closed_triple_count": final["closed_triple_count"],
            "candidate_size_1_count": final["candidate_size_1_count"],
            "candidate_size_2_count": final["candidate_size_2_count"],
            "responses_started": summary.get("thread_count", 0),
            "responses_completed": summary.get("responses_completed_aggregate", 0),
            "mean_response_chain_length": summary.get("mean_thread_length", 0),
            "mean_response_lifetime": summary.get("mean_response_lifetime", 0),
            "runtime_seconds": elapsed})
    _write(root / "worker_count_experiment.csv", worker_rows)

    interval_rows = []
    for interval in (10, 30, 100, 300, 1000):
        destination = root / "intervals" / str(interval)
        _run(args, destination, 1, interval)
        analysis = analyze_lineages(
            destination / "closed_object_snapshots.csv", destination / "lineage"
        )
        pair_summary = list(csv.DictReader(
            (destination / "lineage" / "snapshot_matching_summary.csv").open()
        ))
        transition_rows = list(csv.DictReader(
            (destination / "lineage" / "object_transition_matrix.csv").open()
        ))
        transitions = {(row["from_type"], row["to_type"]): int(row["count"]) for row in transition_rows}
        pairs = max(1, len(pair_summary))
        source_total = max(1, sum(int(row["from_object_count"]) for row in pair_summary))
        target_total = max(1, sum(int(row["to_object_count"]) for row in pair_summary))
        interval_rows.append({"snapshot_interval_sweeps": interval,
            "snapshot_count": analysis["snapshot_count"],
            "mean_objects_per_snapshot": analysis["mean_objects_per_snapshot"],
            "primary_match_rate": analysis["primary_matches"] / source_total,
            "unmatched_birth_rate": analysis["unmatched_births"] / target_total,
            "unmatched_death_rate": analysis["unmatched_deaths"] / source_total,
            "ambiguous_source_rate": sum(int(r["ambiguous_source_count"]) for r in pair_summary) / source_total,
            "ambiguous_target_rate": sum(int(r["ambiguous_target_count"]) for r in pair_summary) / target_total,
            "split_candidate_rate": analysis["split_candidates"] / source_total,
            "merge_candidate_rate": analysis["merge_candidates"] / target_total,
            "pair_to_pair_count": transitions.get(("pair", "pair"), 0),
            "pair_to_triple_count": transitions.get(("pair", "triple"), 0),
            "triple_to_pair_count": transitions.get(("triple", "pair"), 0),
            "triple_to_triple_count": transitions.get(("triple", "triple"), 0),
            "matching_snapshot_pairs": pairs})
    _write(root / "snapshot_interval_experiment.csv", interval_rows)


def _run(args: argparse.Namespace, destination: Path, workers: int, interval: int) -> float:
    runner = Path(__file__).with_name("edge_response.py")
    started = monotonic()
    subprocess.run([sys.executable, str(runner), "--profile", "M1", "--N", str(args.N),
        "--seed", str(args.seed), "--background-sweeps", str(args.background_sweeps),
        "--worker-count", str(workers), "--snapshot-interval-sweeps", str(interval),
        "--enable-snapshots", "--enable-worker-stats", "--enable-closed-object-snapshots",
        "--enable-response-histograms", "--output-dir", str(destination)], check=True)
    return monotonic() - started


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
