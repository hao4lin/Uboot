"""Run one configurable fair-slot edge-response experiment."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path
from random import Random
from typing import Any

from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["M0", "M1", "M2", "M3", "M4", "M5", "M6", "LEGACY"], default="M0")
    parser.add_argument("--N", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--background-sweeps", type=int, default=1_000)
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
    parser.add_argument(
        "--scheduler-mode", choices=["response_priority", "mixed"]
    )
    parser.add_argument("--p-process-response", type=float)
    parser.add_argument("--output-dir", default="artifacts/edge_response")
    args = parser.parse_args()
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
    engine = EdgeResponseEngine(distinct_random_network(args.N, rng),
                                policy_profile(args.profile, **overrides), rng)
    engine.run(args.background_sweeps)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = {"profile": args.profile, "N": args.N, "seed": args.seed,
               "background_sweeps": args.background_sweeps, **engine.summary()}
    (output / "edge_response_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    with (output / "edge_response_events.csv").open("w", newline="", encoding="utf-8") as handle:
        rows = [asdict(event) for event in engine.events]
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
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
        "and edge_response_events.csv.\n", encoding="utf-8")
    audit = Path(__file__).parents[1] / "docs" / "edge_response_logic_audit.md"
    (output / "edge_response_logic_audit.md").write_text(
        audit.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _set(mapping: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        mapping[key] = value


if __name__ == "__main__":
    main()
