"""Review bounded v1/v2 strong rows using radius-one relation motifs."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time

from uboot.deterministic_replay import build_replay_oracle, checkpoint_manifest_rows
from uboot.kernel import SLOT_COUNT
from uboot.minimal_edge_experiment import network_hash, run_baseline_reference
from uboot.support_graph_review_runner import (
    load_review_inputs,
    run_support_graph_review,
    write_review_outputs,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-dir", type=Path, required=True)
    parser.add_argument("--v2-dir", type=Path, required=True)
    parser.add_argument("--N", type=int, default=100, dest="size")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--sweeps", type=int, required=True)
    parser.add_argument("--checkpoint-sweeps", default="0,100,300,1000,3000,10000")
    parser.add_argument("--replay-missing-context", action="store_true")
    parser.add_argument("--max-local-radius", type=int, default=1)
    parser.add_argument("--require-primary-role-participation", action="store_true")
    parser.add_argument("--control-seed", type=int, default=20260718)
    parser.add_argument("--controls-per-case", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.replay_missing_context:
        raise SystemExit("v1 review requires --replay-missing-context")
    if args.max_local_radius != 1:
        raise SystemExit("the first v3 review is fixed at radius one")
    if args.controls_per_case < 0 or args.controls_per_case > 10:
        raise SystemExit("controls-per-case must be between zero and ten")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        inputs = load_review_inputs(args.v1_dir.resolve(), args.v2_dir.resolve())
        if len(inputs) > 161:
            raise RuntimeError("v3 first review exceeds the bounded 161-row scope")
        max_locator = args.sweeps * SLOT_COUNT * args.size
        checkpoints = tuple(
            sorted(
                {
                    0,
                    max_locator,
                    *(
                        int(value.strip()) * SLOT_COUNT * args.size
                        for value in args.checkpoint_sweeps.split(",")
                        if value.strip()
                    ),
                }
            )
        )
        print("progress=0% phase=building deterministic replay", file=sys.stderr, flush=True)
        oracle = build_replay_oracle(
            size=args.size,
            seed=args.seed,
            max_locator=max_locator,
            checkpoint_locators=checkpoints,
            impact_index_mode="all-commits",
            git_commit_hash=_git_version(),
            checkpoint_dir=output / "checkpoints",
            progress=_build_progress,
        )
        cases = run_support_graph_review(
            oracle,
            inputs,
            control_seed=args.control_seed,
            controls_per_case=args.controls_per_case,
            max_local_radius=args.max_local_radius,
            require_primary_role_participation=args.require_primary_role_participation,
            progress=_review_progress,
        )
        print("progress=92% phase=verifying pure baseline", file=sys.stderr, flush=True)
        direct_network, direct_rng = run_baseline_reference(
            size=args.size, seed=args.seed, sweeps=args.sweeps
        )
        final = oracle.checkpoints[max_locator]
        verification = {
            "baseline_final_network_hash_match": final.network_hash == network_hash(direct_network),
            "baseline_final_rng_state_match": final.rng_state == direct_rng,
            "fingerprint_hash": oracle.fingerprint.fingerprint_hash,
        }
        if not all(verification[key] for key in verification if key.endswith("match")):
            raise RuntimeError("v3 observer diverged from pure Baseline")
        (output / "run_fingerprint.json").write_text(
            json.dumps(asdict(oracle.fingerprint) | {"fingerprint_hash": oracle.fingerprint.fingerprint_hash}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (output / "checkpoint_manifest.json").write_text(
            json.dumps({"fingerprint_hash": oracle.fingerprint.fingerprint_hash, "checkpoints": checkpoint_manifest_rows(oracle)}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        import csv

        with (args.v2_dir.resolve() / "motion_candidates_reclassified.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            old_rows = list(csv.DictReader(handle))
        write_review_outputs(
            output,
            cases,
            old_reclassification_rows=old_rows,
            verification=verification,
            runtime_seconds=time.monotonic() - started,
        )
        print("progress=100% phase=complete", file=sys.stderr, flush=True)
    except BaseException:
        print(f"incomplete output retained at {output}", file=sys.stderr)
        raise
    print(f"complete: {output}", file=sys.stderr)


def _build_progress(completed: int, total: int) -> None:
    if completed == total or completed % 300_000 < 16_384:
        print(
            f"progress={int(50 * completed / max(1, total))}% phase=building deterministic replay",
            file=sys.stderr,
            flush=True,
        )


def _review_progress(completed: int, total: int) -> None:
    if completed == total or completed % 16 == 0:
        print(
            f"progress={50 + int(40 * completed / max(1, total))}% phase=reviewing support graphs",
            file=sys.stderr,
            flush=True,
        )


def _git_version() -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip() or "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return commit + ("+dirty" if dirty else "")


if __name__ == "__main__":
    main()
