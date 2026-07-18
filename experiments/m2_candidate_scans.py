"""Run bounded M1-exclusion versus M2-endogenous validation scans."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import subprocess
import sys

from uboot.snapshot_lineage import analyze_lineages, exact_object_lifetime_summary


RULES = ("incoming_excluding_out", "endogenous_in_out2")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--output-dir", default="artifacts/m2_candidate_validation")
    parser.add_argument("--n100-sweeps", default="100,1000,10000")
    parser.add_argument("--run-n1000", action="store_true")
    args = parser.parse_args()
    root = Path(args.output_dir)
    rows = []
    for sweeps in map(int, args.n100_sweeps.split(",")):
        for rule in RULES:
            destination = root / "N100" / f"s{sweeps}" / rule
            _run(destination, 100, args.seed, sweeps, 1, rule, 10)
            rows.append(_summarize(destination, 100, args.seed, sweeps, 1, rule))
    _write(root / "n100_comparison.csv", rows)

    endogenous_final = next(
        row
        for row in reversed(rows)
        if row["candidate_rule"] == "endogenous_in_out2"
    )
    safe = (
        not endogenous_final["whole_network_object_present"]
        and endogenous_final["max_candidate_scc_size"] < 100
        and endogenous_final["candidate_edges_per_node"] < 100
    )
    (root / "n1000_gate.json").write_text(
        json.dumps({"safe": safe, "basis": endogenous_final}, indent=2),
        encoding="utf-8",
    )
    if args.run_n1000 and safe:
        n1000_rows = []
        for workers in (1, 2):
            destination = root / "N1000" / f"workers{workers}"
            _run(destination, 1000, args.seed, 1000, workers, "endogenous_in_out2", 30)
            n1000_rows.append(
                _summarize(
                    destination, 1000, args.seed, 1000, workers, "endogenous_in_out2"
                )
            )
        _write(root / "n1000_worker_comparison.csv", n1000_rows)


def _run(
    output: Path,
    size: int,
    seed: int,
    sweeps: int,
    workers: int,
    rule: str,
    interval: int,
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("edge_response.py")),
            "--profile",
            "M2",
            "--candidate-rule",
            rule,
            "--N",
            str(size),
            "--seed",
            str(seed),
            "--background-sweeps",
            str(sweeps),
            "--worker-count",
            str(workers),
            "--snapshot-interval-sweeps",
            str(interval),
            "--enable-snapshots",
            "--enable-closed-object-snapshots",
            "--enable-worker-stats",
            "--enable-response-histograms",
            "--output-dir",
            str(output),
        ],
        check=True,
    )


def _summarize(
    output: Path, size: int, seed: int, sweeps: int, workers: int, rule: str
) -> dict[str, object]:
    snapshots = list(csv.DictReader((output / "system_snapshots.csv").open()))
    final = snapshots[-1]
    object_path = output / "closed_object_snapshots.csv"
    lineage_counts: Counter[str] = Counter()
    if object_path.exists() and object_path.stat().st_size:
        analysis_dir = output / "lineage"
        analyze_lineages(object_path, analysis_dir)
        lineage_path = analysis_dir / "object_lineages.csv"
        if lineage_path.stat().st_size:
            lineage_counts.update(
                row["match_relation"] for row in csv.DictReader(lineage_path.open())
            )
    first_pair = _first_tick(snapshots, "closed_pair_count")
    first_triple = _first_tick(snapshots, "closed_triple_count")
    lifetimes = exact_object_lifetime_summary(object_path)
    return {
        "N": size,
        "seed": seed,
        "sweeps": sweeps,
        "workers": workers,
        "candidate_rule": rule,
        "all_mutual_pair_count": int(final["all_mutual_pair_count"]),
        "same_meaning_mutual_pair_count": int(final["same_meaning_mutual_pair_count"]),
        "different_only_mutual_pair_count": int(final["different_only_mutual_pair_count"]),
        "raw_mutual_triangle_count": int(final["raw_mutual_triangle_count"]),
        "candidate_directed_edge_count": int(final["candidate_directed_edge_count"]),
        "candidate_mutual_pair_count": int(final["candidate_mutual_pair_count"]),
        "raw_mutual_and_candidate_pair_count": int(final["raw_mutual_and_candidate_pair_count"]),
        "candidate_size_0": int(final["candidate_size_0"]),
        "candidate_size_1": int(final["candidate_size_1"]),
        "candidate_size_2": int(final["candidate_size_2"]),
        "candidate_size_ge3": int(final["candidate_size_ge3"]),
        "closed_pair_count": int(final["closed_pair_count"]),
        "closed_triple_count": int(final["closed_triple_count"]),
        "closed_larger_count": int(final["closed_larger_count"]),
        "first_pair_tick": first_pair,
        "first_triple_tick": first_triple,
        "max_candidate_scc_size": int(final["max_candidate_scc_size"]),
        "max_closed_candidate_scc_size": int(final["max_closed_candidate_scc_size"]),
        "whole_network_object_present": final["whole_network_object_present"] == "True",
        "candidate_edges_per_node": float(final["candidate_edges_per_node"]),
        "mean_response_lifetime_ticks": float(final["mean_response_lifetime_ticks"]),
        "max_concurrent_workers": int(
            json.loads((output / "edge_response_summary.json").read_text())[
                "max_concurrent_workers"
            ]
        ),
        "lineage_persist": lineage_counts["persist"],
        "lineage_member_replacement": lineage_counts["member_replacement"],
        "lineage_growth": lineage_counts["growth"],
        "lineage_shrink": lineage_counts["shrink"],
        "lineage_birth": lineage_counts["birth"],
        "lineage_death": lineage_counts["death"],
        **lifetimes,
    }


def _first_tick(rows: list[dict[str, str]], field: str) -> int | str:
    return next((int(row["tick"]) for row in rows if int(row[field]) > 0), "")


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
