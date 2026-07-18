"""Continue frozen M2 history in short resumable checkpointed chunks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import monotonic

from uboot.checkpoint import load_checkpoint
from uboot.incremental import (
    acquire_run_lock,
    choose_root_checkpoint,
    finalize_run_index,
    history_tail,
    reserve_chunk_paths,
    run_incremental_chunk,
)
from uboot.checkpoint import dynamics_state_hash
from uboot.kernel import SLOT_COUNT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--source-checkpoint", type=Path)
    parser.add_argument("--chunk-sweeps", type=int, default=1000)
    parser.add_argument("--snapshot-interval-sweeps", type=int, default=10)
    parser.add_argument("--allow-version-mismatch", action="store_true")
    args = parser.parse_args()
    lock = acquire_run_lock(args.run_dir)
    try:
        source = choose_root_checkpoint(args.run_dir, args.source_checkpoint)
        engine, metadata = load_checkpoint(
            source, allow_version_mismatch=args.allow_version_mismatch
        )
        ticks_per_sweep = SLOT_COUNT * len(engine.targets)
        root_sweep = engine.tick // ticks_per_sweep
        tail = history_tail(args.run_dir)
        replay_started = monotonic()
        replay_sweeps = 0
        if tail is not None:
            start_sweep = int(tail["end_sweep"])
            replay_sweeps = start_sweep - root_sweep
            if replay_sweeps < 0:
                raise ValueError("history ends before its fixed root checkpoint")
            engine.run(start_sweep)
            replay_hash = dynamics_state_hash(engine)
            if replay_hash != tail["final_dynamics_state_hash"]:
                raise RuntimeError(
                    "root replay does not match the previous history boundary; "
                    "refusing to fork the recorded trajectory"
                )
        else:
            start_sweep = root_sweep
        replay_runtime = monotonic() - replay_started
        end_sweep = start_sweep + args.chunk_sweeps
        chunk_dir, checkpoint_dir = reserve_chunk_paths(
            args.run_dir, start_sweep, end_sweep
        )
        last_report = monotonic()

        def progress(completed: int, total: int) -> None:
            nonlocal last_report
            now = monotonic()
            if now - last_report >= 60 or completed == total:
                print(
                    f"incremental sweeps {completed}/{total} "
                    f"({100 * completed / total:.1f}%)",
                    file=sys.stderr,
                    flush=True,
                )
                last_report = now

        manifest = run_incremental_chunk(
            engine,
            metadata,
            chunk_sweeps=args.chunk_sweeps,
            snapshot_interval_sweeps=args.snapshot_interval_sweeps,
            chunk_dir=chunk_dir,
            checkpoint_dir=checkpoint_dir,
            seed=int(metadata["seed"]),
            progress=progress,
        )
        manifest["replay_sweeps"] = replay_sweeps
        manifest["replay_runtime_seconds"] = replay_runtime
        (chunk_dir / "incremental_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        finalize_run_index(args.run_dir, manifest)
        print(json.dumps(manifest, indent=2, sort_keys=True))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
