"""Create a bounded diagnostic sample from relation-transformation outputs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from uboot.relation_transform_diagnostic import (
    DIAGNOSTIC_FIELDNAMES,
    build_diagnostic_sample,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260718)
    parser.add_argument("--per-group-limit", type=int, default=30)
    parser.add_argument(
        "--output",
        type=Path,
        help="default: INPUT_DIR/slice_transformations_diagnostic_sample.csv",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output = (
        args.output.resolve()
        if args.output is not None
        else input_dir / "slice_transformations_diagnostic_sample.csv"
    )
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    slices = _read_csv(input_dir / "relation_slices.csv")
    transformations = _read_csv(input_dir / "slice_transformations.csv")
    verification = json.loads(
        (input_dir / "observer_verification.json").read_text(encoding="utf-8")
    )
    rows = build_diagnostic_sample(
        slices,
        transformations,
        verification["tracked_anchor_ids"],
        seed=args.seed,
        per_group_limit=args.per_group_limit,
    )
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DIAGNOSTIC_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        group = row["sample_group"]
        counts[group] = counts.get(group, 0) + 1
    print(json.dumps({"output": str(output), "group_counts": counts}, indent=2))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    main()
