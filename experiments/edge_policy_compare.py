"""Run the independent edge-change policy comparison experiment."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from uboot.edge_change_policies import POLICY_NAMES
from uboot.edge_policy_experiment import (
    PolicyRunResult,
    policy_config_from_mapping,
    policy_configs_json,
    run_policy_comparison,
)


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--N", type=int, default=100, dest="size")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--sweeps", type=int, required=True)
    parser.add_argument(
        "--snapshot-sweeps",
        default="0,1000,3000,10000,30000,100000,300000",
    )
    parser.add_argument("--policies", default=",".join(POLICY_NAMES))
    parser.add_argument("--policy-config", type=Path)
    parser.add_argument("--write-relation-events", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.size != 100:
        raise SystemExit("this experiment is deliberately restricted to N=100")
    if args.sweeps < 0:
        raise SystemExit("--sweeps must be non-negative")
    policies = _csv_values(args.policies)
    unknown = set(policies) - set(POLICY_NAMES)
    if unknown:
        raise SystemExit(f"unknown policies: {', '.join(sorted(unknown))}")
    snapshots = tuple(int(value) for value in _csv_values(args.snapshot_sweeps))
    config_mapping = _read_config(args.policy_config)
    parameters = policy_config_from_mapping(config_mapping)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    reporter = ProgressReporter()
    try:
        results = run_policy_comparison(
            size=args.size,
            seed=args.seed,
            sweeps=args.sweeps,
            snapshot_sweeps=snapshots,
            policy_names=policies,
            policy_parameters=parameters,
            write_events=args.write_relation_events,
            progress=reporter,
        )
        _write_outputs(output, args, results)
    except BaseException:
        print(f"incomplete output retained at {output}", file=sys.stderr)
        raise
    print(f"complete: {output}", file=sys.stderr)


class ProgressReporter:
    def __init__(self) -> None:
        self.last_policy = ""
        self.last_time = 0.0

    def __call__(
        self, policy: str, tick: int, total: int, responses: int, queued: int
    ) -> None:
        now = time.monotonic()
        if policy != self.last_policy or tick >= total or now - self.last_time >= 60:
            print(
                f"policy={policy} tick={tick}/{total} "
                f"responses={responses} queued={queued}",
                file=sys.stderr,
                flush=True,
            )
            self.last_policy = policy
            self.last_time = now


def _read_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("policy config must be a JSON object keyed by policy name")
    policy_values = value.get("policies", value)
    if not isinstance(policy_values, dict):
        raise ValueError("the policies config entry must be a JSON object")
    normalized = {}
    for name, item in policy_values.items():
        if not isinstance(item, dict):
            raise ValueError(f"policy config for {name} must be a JSON object")
        if name == "baseline_current":
            continue
        normalized[name] = item.get("parameters", item)
    return normalized


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _write_outputs(
    output: Path, args: argparse.Namespace, results: tuple[PolicyRunResult, ...]
) -> None:
    shutil.copyfile(
        ROOT / "docs" / "edge_policy_baseline.md",
        output / "edge_policy_baseline.md",
    )
    shutil.copyfile(
        ROOT / "docs" / "edge_policy_code_diff_report.md",
        output / "edge_policy_code_diff_report.md",
    )
    configs = {
        "run": {
            "N": args.size,
            "seed": args.seed,
            "sweeps": args.sweeps,
            "snapshot_sweeps": list(
                sorted(set(int(value) for value in _csv_values(args.snapshot_sweeps)))
            ),
            "policies": list(_csv_values(args.policies)),
            "write_relation_events": args.write_relation_events,
        },
        "policies": policy_configs_json(results),
    }
    (output / "edge_policy_configs.json").write_text(
        json.dumps(configs, indent=2, sort_keys=True), encoding="utf-8"
    )
    _write_csv(output / "edge_policy_summary.csv", (r.summary for r in results))
    _write_csv(
        output / "edge_policy_time_series.csv",
        (row for result in results for row in result.time_series),
    )
    _write_csv(
        output / "edge_policy_relation_strength_histogram.csv",
        (row for result in results for row in result.strength_histogram),
    )
    _write_csv(
        output / "edge_policy_transition_matrix.csv",
        (row for result in results for row in result.transitions),
    )
    _write_csv(
        output / "edge_policy_component_stats.csv",
        (row for result in results for row in result.component_stats),
    )
    _write_csv(
        output / "edge_policy_internal_external_stats.csv",
        (row for result in results for row in result.internal_external_stats),
    )
    if args.write_relation_events:
        _write_csv(
            output / "edge_policy_relation_events.csv",
            (row for result in results for row in result.events),
        )
    (output / "edge_policy_comparison.md").write_text(
        _comparison_report(results), encoding="utf-8"
    )


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    materialized = list(rows)
    fieldnames = list(dict.fromkeys(key for row in materialized for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(materialized)


def _comparison_report(results: tuple[PolicyRunResult, ...]) -> str:
    columns = (
        "policy",
        "internal_relation_count",
        "external_slot_change_rate",
        "same_node_external_change_rate_after_internal",
        "L_max_component",
        "strength_midrange_ratio",
        "total_score",
    )
    lines = [
        "# Edge-policy comparison",
        "",
        "This report is descriptive. The auxiliary score does not select a model.",
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for result in results:
        row = result.summary
        lines.append("| " + " | ".join(str(row[column]) for column in columns) + " |")
    by_name = {result.policy: result.summary for result in results}
    baseline = by_name.get("baseline_current")
    hard = by_name.get("hard_reciprocal_lock")
    soft = by_name.get("soft_reciprocal_inertia")
    closure = by_name.get("soft_internal_harder_than_external")
    qualifying = [
        row["policy"]
        for row in by_name.values()
        if _is_positive(row.get("mean_internal_relation_lifetime"))
        and _is_positive(row.get("mean_external_relation_lifetime"))
        and float(row["mean_internal_relation_lifetime"])
        > float(row["mean_external_relation_lifetime"])
        and _is_positive(row.get("external_slot_change_rate"))
        and row["L_max_component"] < row["N"]
        and _is_positive(row.get("strength_midrange_ratio"))
        and row["promote_count"] > 0
        and row["demote_count"] > 0
    ]
    hard_free = [
        row["policy"]
        for row in by_name.values()
        if row["internal_relation_count"] > 0
        and _is_positive(row.get("same_node_external_change_rate_after_internal"))
        and row.get("strength_midrange_ratio") == "N/A"
    ]
    soft_stable = [
        row["policy"]
        for row in by_name.values()
        if row["internal_relation_count"] > 0
        and isinstance(row.get("internal_slot_change_rate"), float)
        and isinstance(row.get("external_slot_change_rate"), float)
        and row["internal_slot_change_rate"] < row["external_slot_change_rate"]
        and row.get("strength_midrange_ratio") != "N/A"
    ]
    lines.extend(
        [
            "",
            "## Data answers",
            "",
            "1. Baseline local fixed points: " + _baseline_answer(baseline),
            "2. Hard-lock over-hardness: " + _hard_answer(hard),
            "3. Soft reciprocal inertia: " + _soft_answer(soft),
            "4. Closure-supported relative stability: " + _closure_answer(closure),
            "5. Sibling-slot drift after internal formation is shown directly by "
            "`same_node_external_change_rate_after_internal`; nonzero values are "
            "evidence of slot isolation.",
            "6. External drift breaking internal relations is assessed by comparing "
            "internal and external slot change rates and their completed lifetimes; "
            "`N/A` means this run did not observe enough completed relations.",
            "7. Policies satisfying every requested criterion in this run: "
            + (", ".join(qualifying) if qualifying else "none"),
            "8. Hard-internal plus drifting-external evidence: "
            + (", ".join(hard_free) if hard_free else "none"),
            "9. Softer statistically stable internal evidence: "
            + (", ".join(soft_stable) if soft_stable else "none"),
            "10. Overall mechanism evidence: "
            + (
                "continuous-strength candidates meet all current criteria"
                if qualifying
                else "insufficient for a preferred mechanism; retain all controls"
            ),
            "",
            "## Reading the result",
            "",
            "Q is the strict same-slot reciprocal graph, S is the configured "
            "high-strength graph, and L is the promoted internal graph. C retains "
            "the current candidate-graph statistic for continuity. Baseline and "
            "hard-lock strength fields are not evidence for continuous transitions.",
            "",
            "Interpret over-softness, over-hardness, internal lifetime, and external "
            "mobility together. A nonzero total score alone is not a recommendation.",
        ]
    )
    return "\n".join(lines) + "\n"


def _is_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _baseline_answer(row: dict[str, Any] | None) -> str:
    if row is None:
        return "not run."
    return (
        f"C-g={row['current_candidate_g']}, Q pairs={row['Q_pair_count']}, "
        f"Q triangles={row['Q_triangle_count']}; the historical candidate graph "
        "remains the continuity statistic and has no retention state."
    )


def _hard_answer(row: dict[str, Any] | None) -> str:
    if row is None:
        return "not run."
    return (
        f"internal relations={row['internal_relation_count']}, "
        f"L max component={row['L_max_component']}/{row['N']}, "
        f"overhard penalty={row['overhard_penalty']}."
    )


def _soft_answer(row: dict[str, Any] | None) -> str:
    if row is None:
        return "not run."
    return (
        f"high-strength relations={row['high_strength_relation_count']}, "
        f"S pairs={row['S_pair_count']}, S triangles={row['S_triangle_count']}; "
        "interpret lifetime separately from structure formation."
    )


def _closure_answer(row: dict[str, Any] | None) -> str:
    if row is None:
        return "not run."
    return (
        f"mean internal lifetime={row.get('mean_internal_relation_lifetime')}, "
        f"mean external lifetime={row.get('mean_external_relation_lifetime')}, "
        f"internal/external change rates={row['internal_slot_change_rate']}/"
        f"{row['external_slot_change_rate']}."
    )


if __name__ == "__main__":
    main()
