"""Analyze adjacent closed-object snapshots without touching simulation state."""

import argparse
from pathlib import Path

from uboot.snapshot_lineage import analyze_lineages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--lineage-min-jaccard", type=float, default=0.25)
    parser.add_argument("--lineage-min-containment", type=float, default=0.5)
    args = parser.parse_args()
    summary = analyze_lineages(
        Path(args.input),
        Path(args.output_dir),
        min_jaccard=args.lineage_min_jaccard,
        min_containment=args.lineage_min_containment,
    )
    print(summary)


if __name__ == "__main__":
    main()
