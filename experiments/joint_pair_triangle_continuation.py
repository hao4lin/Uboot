"""Track one closed pair and one closed triple through recorded God slices."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from uboot.joint_continuation import (
    SnapshotSeries,
    auto_select_and_track,
    track_joint_continuation,
    write_joint_result,
)
from uboot.joint_diagnostics import (
    build_diagnostics,
    load_touch_observations,
    write_diagnostics,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--start-snapshot-id", type=int, required=True)
    parser.add_argument("--pair-ids", type=int, nargs=2)
    parser.add_argument("--triangle-ids", type=int, nargs=3)
    parser.add_argument("--auto-select-candidate", action="store_true")
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--max-auto-attempts", type=int, default=100)
    parser.add_argument("--max-god-slices", type=int)
    parser.add_argument("--touch-observations", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.auto_select_candidate == bool(args.pair_ids or args.triangle_ids):
        parser.error(
            "choose either --auto-select-candidate or both --pair-ids/--triangle-ids"
        )
    if not args.auto_select_candidate and not (args.pair_ids and args.triangle_ids):
        parser.error("explicit mode requires both --pair-ids and --triangle-ids")
    input_paths = _resolve_inputs(args.input_dir)
    series = SnapshotSeries.from_csv_pairs(input_paths)
    attempts = None
    if args.auto_select_candidate:
        result, attempts = auto_select_and_track(
            series,
            start_snapshot_id=args.start_snapshot_id,
            seed=args.seed,
            max_attempts=args.max_auto_attempts,
            max_god_slices=args.max_god_slices,
        )
    else:
        result = track_joint_continuation(
            series,
            start_snapshot_id=args.start_snapshot_id,
            pair_ids=args.pair_ids,
            triangle_ids=args.triangle_ids,
            max_god_slices=args.max_god_slices,
        )
    config = {
        "input_dir": str(args.input_dir.resolve()),
        "snapshot_files": [
            {
                "system": str(system_path.resolve()),
                "objects": str(object_path.resolve()),
            }
            for system_path, object_path in input_paths
        ],
        "start_snapshot_id": args.start_snapshot_id,
        "pair_ids": args.pair_ids,
        "triangle_ids": args.triangle_ids,
        "auto_select_candidate": args.auto_select_candidate,
        "seed": args.seed,
        "max_auto_attempts": args.max_auto_attempts,
        "max_god_slices": args.max_god_slices,
        "touch_observations": str(args.touch_observations.resolve())
        if args.touch_observations
        else None,
    }
    summary = write_joint_result(
        result, args.output_dir, config=config, attempts=attempts
    )
    diagnostics = build_diagnostics(
        series, result, load_touch_observations(args.touch_observations)
    )
    summary["touch_diagnostics"] = write_diagnostics(
        diagnostics, args.output_dir
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def _resolve_inputs(input_dir: Path) -> list[tuple[Path, Path]]:
    options = (
        ("system_snapshots.csv", "closed_object_snapshots.csv"),
        ("phase2_system_snapshots.csv", "phase2_object_snapshots.csv"),
    )
    for system_name, object_name in options:
        system_path, object_path = input_dir / system_name, input_dir / object_name
        if system_path.is_file() and object_path.is_file():
            return [(system_path, object_path)]
    chunks = []
    for chunk_dir in sorted((input_dir / "chunks").glob("s*_to_s*")):
        system_path = chunk_dir / "system_snapshots.csv"
        object_path = chunk_dir / "closed_object_snapshots.csv"
        if system_path.is_file() and object_path.is_file():
            chunks.append((system_path, object_path))
    if chunks:
        return chunks
    raise FileNotFoundError(
        "input directory must contain system/object snapshots in a supported format"
    )


if __name__ == "__main__":
    main()
