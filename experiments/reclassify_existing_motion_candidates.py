"""Conservatively reclassify first-round deterministic motion evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from uboot.motion_reclassification import (
    reclassify_existing_motion_candidates,
    write_reclassified_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.input_dir.resolve()
    output = args.output or root / "motion_candidates_reclassified.csv"
    rows = reclassify_existing_motion_candidates(
        root / "motion_candidates.csv",
        root / "localized_commits.csv",
        root / "generated_three_member_slices.csv",
    )
    write_reclassified_csv(output, rows)
    print(f"reclassified={len(rows)} output={output}")


if __name__ == "__main__":
    main()
