"""Replay M2 with passive update-touch observation for one pair and triple."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from random import Random

from uboot.checkpoint import load_checkpoint
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.joint_diagnostics import run_touch_replay


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--checkpoint", type=Path)
    source.add_argument("--N", type=int)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--warmup-sweeps", type=int, default=0)
    parser.add_argument("--tracking-sweeps", type=int, required=True)
    parser.add_argument("--snapshot-interval-sweeps", type=int, default=1)
    parser.add_argument("--start-snapshot-id", type=int, required=True)
    parser.add_argument("--pair-ids", type=int, nargs=2, required=True)
    parser.add_argument("--triangle-ids", type=int, nargs=3, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.checkpoint:
        if args.warmup_sweeps:
            parser.error("--warmup-sweeps is only valid with --N")
        engine, _ = load_checkpoint(args.checkpoint)
    else:
        rng = Random(args.seed)
        engine = EdgeResponseEngine(
            distinct_random_network(args.N, rng),
            policy_profile("M2"),
            rng,
            worker_count=1,
            mode="M1",
            candidate_rule="endogenous_in_out2",
        )
        engine.run(args.warmup_sweeps)
    result = run_touch_replay(
        engine,
        pair_ids=tuple(sorted(args.pair_ids)),
        triangle_ids=tuple(sorted(args.triangle_ids)),
        start_snapshot_id=args.start_snapshot_id,
        tracking_sweeps=args.tracking_sweeps,
        snapshot_interval_sweeps=args.snapshot_interval_sweeps,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
