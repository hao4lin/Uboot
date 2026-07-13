"""Track M2 local evolution from a phase2-ready frozen checkpoint only."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
from uboot.checkpoint import load_checkpoint
from uboot.phase2 import run_tracking

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--tracking-sweeps", type=int, default=1000)
    parser.add_argument("--micro-snapshot-interval-sweeps", type=int, default=1)
    parser.add_argument("--max-open-gap-sweeps", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-version-mismatch", action="store_true")
    args = parser.parse_args()
    engine, metadata = load_checkpoint(args.checkpoint, allow_version_mismatch=args.allow_version_mismatch)
    if not metadata.get("phase2_ready"):
        parser.error("checkpoint is not phase2-ready")
    result = run_tracking(engine, metadata, tracking_sweeps=args.tracking_sweeps,
        interval=args.micro_snapshot_interval_sweeps, output_dir=args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
