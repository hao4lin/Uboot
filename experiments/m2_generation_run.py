"""Locked stage-one M2 generation and warmup-checkpoint entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from random import Random
import sys
from time import monotonic

from uboot.checkpoint import dependency_snapshot, dynamics_state_hash, save_checkpoint
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--N", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--worker-count", type=int, default=1)
    parser.add_argument("--sweeps", type=int, default=60000)
    parser.add_argument("--queue-capacity", type=int, default=1024)
    parser.add_argument("--queue-policy", choices=("reject", "drop_oldest"), default="reject")
    parser.add_argument("--progress-seconds", type=float, default=60.0)
    parser.add_argument("--save-checkpoint", action="store_true")
    parser.add_argument("--checkpoint-dir", type=Path)
    args = parser.parse_args()
    rng = Random(args.seed)
    engine = EdgeResponseEngine(
        distinct_random_network(args.N, rng),
        policy_profile("M2"),
        rng,
        worker_count=args.worker_count,
        response_queue_capacity=args.queue_capacity,
        response_queue_policy=args.queue_policy,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )
    last_progress = monotonic()

    def report_progress(
        completed: int, total: int, responses: int, queue_length: int
    ) -> None:
        nonlocal last_progress
        now = monotonic()
        if now - last_progress < args.progress_seconds and completed != total:
            return
        print(
            f"progress: {100 * completed / max(1, total):6.2f}% "
            f"({completed}/{total} ticks) responses={responses} queue={queue_length}",
            file=sys.stderr,
            flush=True,
        )
        last_progress = now

    engine.run(args.sweeps, report_progress)
    uniqueness_violations = sum(len(set(row)) != len(row) for row in engine.targets)
    if uniqueness_violations:
        raise RuntimeError("distinct-target invariant violated during generation")
    result = {
        "tick": engine.tick,
        "dynamics_state_hash": dynamics_state_hash(engine),
        "raw_target_uniqueness_violation_count": uniqueness_violations,
    }
    if args.save_checkpoint:
        if args.checkpoint_dir is None:
            parser.error("--checkpoint-dir is required with --save-checkpoint")
        result["checkpoint"] = save_checkpoint(
            engine, args.checkpoint_dir, seed=args.seed
        )
        (args.checkpoint_dir / "dependencies.json").write_text(
            json.dumps(dependency_snapshot(), indent=2, sort_keys=True), encoding="utf-8"
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
