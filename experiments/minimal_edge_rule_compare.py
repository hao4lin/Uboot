"""Compare three stateless minimal reciprocal edge rules at N=100."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from uboot.minimal_edge_experiment import (
    MinimalRunResult,
    network_hash,
    run_baseline_reference,
    run_minimal_comparison,
)
from uboot.minimal_edge_rules import POLICY_NAMES


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--N", type=int, default=100, dest="size")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--sweeps", type=int, required=True)
    parser.add_argument("--snapshot-sweeps", default="0,100,300,1000,3000,10000")
    parser.add_argument("--policies", default=",".join(POLICY_NAMES))
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.size != 100:
        raise SystemExit("the first minimal-rule experiment is restricted to N=100")
    if args.sweeps < 0:
        raise SystemExit("--sweeps must be non-negative")
    policies = _csv_values(args.policies)
    unknown = set(policies) - set(POLICY_NAMES)
    if unknown:
        raise SystemExit(f"unknown policies: {', '.join(sorted(unknown))}")
    snapshots = tuple(int(value) for value in _csv_values(args.snapshot_sweeps))
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    reporter = ProgressReporter()
    try:
        results = run_minimal_comparison(
            size=args.size,
            seed=args.seed,
            sweeps=args.sweeps,
            snapshot_sweeps=snapshots,
            policy_names=policies,
            progress=reporter,
        )
        baseline_validation = _validate_baseline(args, results)
        _write_outputs(output, args, results, baseline_validation)
    except BaseException:
        print(f"incomplete output retained at {output}", file=sys.stderr)
        raise
    print(f"complete: {output}", file=sys.stderr)


class ProgressReporter:
    def __init__(self) -> None:
        self.last_policy = ""
        self.last_time = 0.0

    def __call__(self, policy: str, tick: int, total: int) -> None:
        now = time.monotonic()
        if policy != self.last_policy or tick >= total or now - self.last_time >= 60:
            print(
                f"policy={policy} tick={tick}/{total}",
                file=sys.stderr,
                flush=True,
            )
            self.last_policy = policy
            self.last_time = now


def _validate_baseline(
    args: argparse.Namespace, results: tuple[MinimalRunResult, ...]
) -> dict[str, Any]:
    baseline = next(
        (result for result in results if result.policy == "baseline_random_replace"),
        None,
    )
    if baseline is None:
        return {"performed": False, "reason": "baseline policy not selected"}
    reference_network, reference_rng_state = run_baseline_reference(
        size=args.size, seed=args.seed, sweeps=args.sweeps
    )
    hash_match = baseline.final_hash == network_hash(reference_network)
    rng_match = baseline.rng_state == reference_rng_state
    if not hash_match or not rng_match:
        raise RuntimeError("baseline wrapper diverged from the frozen reference loop")
    return {
        "performed": True,
        "final_hash_match": hash_match,
        "rng_state_match": rng_match,
    }


def _write_outputs(
    output: Path,
    args: argparse.Namespace,
    results: tuple[MinimalRunResult, ...],
    baseline_validation: dict[str, Any],
) -> None:
    shutil.copyfile(
        ROOT / "docs" / "minimal_rule_diff.md", output / "minimal_rule_diff.md"
    )
    config = {
        "N": args.size,
        "seed": args.seed,
        "sweeps": args.sweeps,
        "snapshot_sweeps": list(
            sorted(set(int(value) for value in _csv_values(args.snapshot_sweeps)))
        ),
        "policies": list(_csv_values(args.policies)),
        "baseline_validation": baseline_validation,
    }
    (output / "minimal_edge_rule_configs.json").write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )
    _write_csv(
        output / "minimal_edge_rule_summary.csv",
        (result.summary for result in results),
    )
    _write_csv(
        output / "minimal_edge_rule_time_series.csv",
        (row for result in results for row in result.time_series),
    )
    _write_csv(
        output / "minimal_edge_structure_lifetimes.csv",
        (row for result in results for row in result.structure_lifetimes),
    )
    rng_states = {
        result.policy: {
            "seed": result.seed,
            "final_network_hash": result.final_hash,
            "final_rng_state_hash": result.rng_state_hash,
            "final_rng_state": result.rng_state,
        }
        for result in results
    }
    (output / "minimal_edge_rng_states.json").write_text(
        json.dumps(rng_states, indent=2), encoding="utf-8"
    )
    (output / "minimal_edge_rule_comparison.md").write_text(
        comparison_report(results), encoding="utf-8"
    )


def comparison_report(results: tuple[MinimalRunResult, ...]) -> str:
    by_name = {result.policy: result for result in results}
    columns = (
        "policy",
        "Q_edge_count",
        "Q_component_count",
        "Q_max_component",
        "reciprocal_keep_rate",
        "nonreciprocal_to_reciprocal_rate",
        "mean_current_relation_age",
    )
    lines = [
        "# Minimal reciprocal edge-rule comparison",
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for result in results:
        row = result.summary
        lines.append("| " + " | ".join(str(row[column]) for column in columns) + " |")
    baseline = by_name.get("baseline_random_replace")
    policy_b = by_name.get("direct_reciprocal_candidate_first")
    policy_c = by_name.get("old_new_reciprocal_compare")
    lines.extend(
        [
            "",
            "## First-round questions",
            "",
            "1. Rule fidelity: all selected policies completed through the requested "
            "sweep; rule differences are limited to `minimal_rule_diff.md`.",
            "2. Baseline reproduction: validated by final network hash and complete "
            "RNG-state equality against the frozen direct reference loop.",
            "3. Policy B reciprocal trend: " + _trend_answer(policy_b),
            "4. Policy C reciprocal retention versus baseline: "
            + _retention_answer(policy_c, baseline),
            "5. Long-lived small Q components: "
            + _structure_answer(results, {"pair", "triangle"}),
            "6. Q maximum-component global merge: " + _merge_answer(results),
            "7. Reciprocal formation and continued loss: "
            + _conversion_answer(results),
            "8. Qualitative classification: " + _classification_answer(results),
            "9. Continue unchanged only when a directional time-series trend remains "
            "without global adhesion; small endpoint counts alone are not failure.",
            "10. A rule that returns to baseline behavior or approaches a global Q "
            "component should be abandoned, not repaired with parameters.",
            "",
            "R and Q statistics, transitions, and lifetimes are passive observations. "
            "They never feed target selection.",
        ]
    )
    return "\n".join(lines) + "\n"


def _trend_answer(result: MinimalRunResult | None) -> str:
    if result is None:
        return "not run."
    values = [row["Q_edge_count"] for row in result.time_series]
    return f"Q edges by snapshot={values}; interpret direction and plateau from this series."


def _retention_answer(
    policy: MinimalRunResult | None, baseline: MinimalRunResult | None
) -> str:
    if policy is None or baseline is None:
        return "required comparison not run."
    return (
        f"C={policy.summary['reciprocal_keep_rate']}, "
        f"baseline={baseline.summary['reciprocal_keep_rate']}."
    )


def _structure_answer(results: tuple[MinimalRunResult, ...], kinds: set[str]) -> str:
    rows = [
        row
        for result in results
        for row in result.structure_lifetimes
        if row["structure_type"] in kinds and row["observed_lifetime"] > 0
    ]
    if not rows:
        return "none persisted across more than one sampled interval."
    longest = max(rows, key=lambda row: row["observed_lifetime"])
    return (
        f"longest={longest['policy']} {longest['structure_type']} "
        f"{longest['members']} for {longest['observed_lifetime']} sampled sweeps."
    )


def _merge_answer(results: tuple[MinimalRunResult, ...]) -> str:
    merged = [
        result.policy
        for result in results
        if result.summary["Q_max_component"] == result.size
    ]
    return ", ".join(merged) if merged else "no policy ended in a global Q component."


def _conversion_answer(results: tuple[MinimalRunResult, ...]) -> str:
    both = [
        result.policy
        for result in results
        if result.summary["transition_nr"] > 0 and result.summary["transition_rn"] > 0
    ]
    return ", ".join(both) if both else "no selected policy showed both directions."


def _classification_answer(results: tuple[MinimalRunResult, ...]) -> str:
    labels = []
    for result in results:
        row = result.summary
        if row["Q_max_component"] == result.size:
            label = "rapid global adhesion"
        elif row["Q_edge_count"] == 0:
            label = "too loose or no endpoint distinction"
        else:
            label = "trend requires time-series reading"
        labels.append(f"{result.policy}={label}")
    return "; ".join(labels) + "."


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    materialized = list(rows)
    fieldnames = list(dict.fromkeys(key for row in materialized for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(materialized)


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


if __name__ == "__main__":
    main()
