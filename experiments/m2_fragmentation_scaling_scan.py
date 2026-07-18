"""Checkpoint-only finite-size scan of M2 candidate fragmentation."""

from __future__ import annotations

import argparse
import csv
import json
from math import exp, log
from pathlib import Path
from random import Random
from statistics import fmean
from time import monotonic
from typing import Any

from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.fragmentation import first_sustained_fragmented_index, fragmentation_metrics
from uboot.snapshot_lineage import closed_object_rows


DEFAULT_CHECKPOINTS = (0, 10, 30, 100, 300, 1000, 3000, 10000, 30000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="100,200,500,1000")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--worker-count", type=int, default=1)
    parser.add_argument("--max-sweeps", type=int, default=30000)
    parser.add_argument(
        "--snapshot-sweeps", default=",".join(map(str, DEFAULT_CHECKPOINTS))
    )
    parser.add_argument("--output-dir", default="artifacts/m2_1_fragmentation_scaling")
    args = parser.parse_args()
    checkpoints = sorted(
        {int(value) for value in args.snapshot_sweeps.split(",") if int(value) <= args.max_sweeps}
        | {0, args.max_sweeps}
    )
    output = Path(args.output_dir)
    snapshots: list[dict[str, Any]] = []
    top_sizes: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    summaries = []
    first_passages = []
    for size in map(int, args.sizes.split(",")):
        result = _run_size(
            size,
            args.seed,
            args.worker_count,
            checkpoints,
            snapshots,
            top_sizes,
            objects,
        )
        summaries.append(result[0])
        first_passages.extend(result[1])
    _write(output / "fragmentation_scaling_summary.csv", summaries)
    _write(output / "fragmentation_snapshots.csv", snapshots)
    _write(output / "fragmentation_scc_top_sizes.csv", top_sizes)
    _write(output / "fragmentation_first_passage.csv", first_passages)
    _write(output / "fragmentation_objects.csv", objects)
    fits = _fits(first_passages)
    _write(output / "fragmentation_scaling_fits.csv", fits)
    correlations = _correlations(snapshots)
    (output / "fragmentation_correlations.json").write_text(
        json.dumps(correlations, indent=2), encoding="utf-8"
    )


def _run_size(
    size: int,
    seed: int,
    workers: int,
    checkpoints: list[int],
    all_snapshots: list[dict[str, Any]],
    all_top_sizes: list[dict[str, Any]],
    all_objects: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rng = Random(seed)
    engine = EdgeResponseEngine(
        distinct_random_network(size, rng),
        policy_profile("M2"),
        rng,
        worker_count=workers,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )
    started = monotonic()
    rows = []
    previous_giant: frozenset[int] = frozenset()
    seen_objects: set[tuple[int, ...]] = set()
    sustained_confirmed_at: int | None = None
    stopped_reason = "max_sweeps"
    for checkpoint_index, sweep in enumerate(checkpoints):
        engine.run(sweep)
        network = engine.snapshot()
        graph = engine.candidate_graph()
        metrics, top, giant = fragmentation_metrics(network, graph)
        row = {
            "N": size,
            "seed": seed,
            "worker_count": workers,
            "tick": engine.tick,
            "sweep": sweep,
            **metrics,
        }
        rows.append(row)
        all_snapshots.append(row)
        all_top_sizes.append(
            {"N": size, "tick": engine.tick, "sweep": sweep, **{f"rank_{i + 1}": value for i, value in enumerate(top)}}
        )
        if metrics["largest_candidate_scc_fraction"] <= 0.5:
            object_rows = closed_object_rows(
                network,
                graph,
                checkpoint_index,
                engine.tick,
                member_inline_limit=32,
            )
            for object_row in object_rows:
                members = tuple(map(int, object_row["member_ids"].split("|"))) if object_row["member_ids"] else tuple()
                key: tuple[Any, ...] = members or (object_row["member_hash"],)
                object_row.update(
                    N=size,
                    born_from_previous_giant=(
                        bool(members)
                        and key not in seen_objects
                        and set(members) <= previous_giant
                    ),
                )
                seen_objects.add(key)
                all_objects.append(object_row)
        fractions = [float(item["largest_candidate_scc_fraction"]) for item in rows]
        sustained_index = first_sustained_fragmented_index(fractions)
        if sustained_index is not None and sustained_confirmed_at is None:
            sustained_confirmed_at = checkpoint_index
        elif sustained_confirmed_at is not None and checkpoint_index > sustained_confirmed_at:
            stopped_reason = "sustained_fragmented_plus_checkpoint"
            previous_giant = giant
            break
        previous_giant = giant
        print(
            f"N={size} checkpoint={sweep} g={metrics['largest_candidate_scc_fraction']:.6f} "
            f"edges/N={metrics['candidate_edges_per_node']:.6f}",
            flush=True,
        )
    runtime = monotonic() - started
    passages = _first_passages(size, rows)
    summary = {
        "N": size,
        "seed": seed,
        "worker_count": workers,
        "completed_sweeps": rows[-1]["sweep"],
        "initial_giant_fraction": rows[0]["largest_candidate_scc_fraction"],
        "final_giant_fraction": rows[-1]["largest_candidate_scc_fraction"],
        "first_fragmented_sweep": _first_value(rows, "largest_candidate_scc_fraction", 0.2),
        "first_sustained_fragmented_sweep": (
            rows[first_sustained_fragmented_index([float(r["largest_candidate_scc_fraction"]) for r in rows])]["sweep"]
            if first_sustained_fragmented_index([float(r["largest_candidate_scc_fraction"]) for r in rows]) is not None
            else ""
        ),
        "first_closed_pair_sweep": _first_positive(rows, "closed_pair_count"),
        "first_closed_triple_sweep": _first_positive(rows, "closed_triple_count"),
        "first_closed_larger_sweep": _first_larger(rows),
        "initial_candidate_edges_per_node": rows[0]["candidate_edges_per_node"],
        "final_candidate_edges_per_node": rows[-1]["candidate_edges_per_node"],
        "final_closed_pair_count": rows[-1]["closed_pair_count"],
        "final_closed_triple_count": rows[-1]["closed_triple_count"],
        "final_closed_larger_count": (
            rows[-1]["closed_size_4_to_10_count"]
            + rows[-1]["closed_size_11_to_100_count"]
            + rows[-1]["closed_size_gt_100_count"]
        ),
        "stopped_reason": stopped_reason,
        "runtime_seconds": runtime,
    }
    return summary, passages


def _first_passages(size: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = [
        ("giant_fraction", 0.8, "largest_candidate_scc_fraction", "le"),
        ("giant_fraction", 0.5, "largest_candidate_scc_fraction", "le"),
        ("giant_fraction", 0.2, "largest_candidate_scc_fraction", "le"),
        *[("candidate_edges_per_node", value, "candidate_edges_per_node", "le") for value in (8, 6, 4, 3)],
        ("closed_pair", 1, "closed_pair_count", "positive"),
        ("closed_triple", 1, "closed_triple_count", "positive"),
    ]
    output = []
    for threshold_type, value, field, comparison in definitions:
        row = next(
            (
                item
                for item in rows
                if (float(item[field]) <= value if comparison == "le" else int(item[field]) > 0)
            ),
            None,
        )
        output.append(
            {
                "N": size,
                "threshold_type": threshold_type,
                "threshold_value": value,
                "first_tick": row["tick"] if row else "",
                "first_sweep": row["sweep"] if row else "",
                "sustained": False,
            }
        )
    index = first_sustained_fragmented_index(
        [float(row["largest_candidate_scc_fraction"]) for row in rows]
    )
    output.append(
        {
            "N": size,
            "threshold_type": "giant_fraction",
            "threshold_value": 0.2,
            "first_tick": rows[index]["tick"] if index is not None else "",
            "first_sweep": rows[index]["sweep"] if index is not None else "",
            "sustained": True,
        }
    )
    return output


def _fits(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fits = []
    keys = {(row["threshold_type"], row["threshold_value"], row["sustained"]) for row in passages}
    for key in sorted(keys, key=str):
        samples = [(int(row["N"]), float(row["first_sweep"])) for row in passages if (row["threshold_type"], row["threshold_value"], row["sustained"]) == key and row["first_sweep"] != ""]
        if len(samples) < 3:
            continue
        xs, ys = zip(*((log(n), log(value)) for n, value in samples if value > 0))
        x_mean, y_mean = fmean(xs), fmean(ys)
        denominator = sum((x - x_mean) ** 2 for x in xs)
        alpha = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
        intercept = y_mean - alpha * x_mean
        predicted = [intercept + alpha * x for x in xs]
        total = sum((y - y_mean) ** 2 for y in ys)
        residual = sum((y - estimate) ** 2 for y, estimate in zip(ys, predicted))
        fits.append({"threshold_type": key[0], "threshold_value": key[1], "sustained": key[2], "alpha": alpha, "a": exp(intercept), "R2": 1 - residual / total if total else 1, "sample_count": len(samples), "label": "exploratory finite-size fit"})
    return fits


def _correlations(rows: list[dict[str, Any]]) -> dict[str, float]:
    giant = [float(row["largest_candidate_scc_fraction"]) for row in rows]
    density = [float(row["candidate_edges_per_node"]) for row in rows]
    mutual = [float(row["raw_mutual_pairs_per_node"]) for row in rows]
    return {"giant_vs_candidate_edges_per_node": _correlation(giant, density), "giant_vs_raw_mutual_pairs_per_node": _correlation(giant, mutual)}


def _correlation(left: list[float], right: list[float]) -> float:
    lm, rm = fmean(left), fmean(right)
    numerator = sum((a - lm) * (b - rm) for a, b in zip(left, right))
    denominator = (sum((a - lm) ** 2 for a in left) * sum((b - rm) ** 2 for b in right)) ** 0.5
    return numerator / denominator if denominator else 0


def _first_value(rows: list[dict[str, Any]], field: str, threshold: float) -> int | str:
    return next((int(row["sweep"]) for row in rows if float(row[field]) <= threshold), "")


def _first_positive(rows: list[dict[str, Any]], field: str) -> int | str:
    return next((int(row["sweep"]) for row in rows if int(row[field]) > 0), "")


def _first_larger(rows: list[dict[str, Any]]) -> int | str:
    return next((int(row["sweep"]) for row in rows if int(row["closed_size_4_to_10_count"]) + int(row["closed_size_11_to_100_count"]) + int(row["closed_size_gt_100_count"]) > 0), "")


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    fields.extend(
        key for row in rows for key in row if key not in fields
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
