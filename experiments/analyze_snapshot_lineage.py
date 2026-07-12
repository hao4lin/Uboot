"""Analyze adjacent closed-object snapshots without touching simulation state."""

import argparse
from pathlib import Path

from uboot.snapshot_lineage import analyze_lineages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    summary = analyze_lineages(Path(args.input), Path(args.output_dir))
    print(summary)


if __name__ == "__main__":
    main()
