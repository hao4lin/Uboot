"""Run M0-M6 across requested sizes and seeds."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[20, 50, 100, 500])
    parser.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4])
    parser.add_argument("--background-sweeps", type=int, default=1_000)
    parser.add_argument("--snapshot-interval-sweeps", type=int, default=100)
    parser.add_argument("--worker-count", type=int, default=1)
    parser.add_argument("--output-dir", default="artifacts/edge_response_batch")
    args = parser.parse_args()
    runner = Path(__file__).with_name("edge_response.py")
    summaries = []
    profiles = [f"M{i}" for i in range(7)]
    total_batches = len(profiles) * len(args.sizes) * len(args.seeds)
    batch_index = 0
    for profile in profiles:
        for size in args.sizes:
            for seed in args.seeds:
                batch_index += 1
                destination = Path(args.output_dir) / profile / f"N{size}_seed{seed}"
                print(
                    "\n" + "=" * 78,
                    f"BATCH {batch_index}/{total_batches} | profile={profile} | "
                    f"N={size} | seed={seed} | "
                    f"background_sweeps={args.background_sweeps}",
                    f"worker_count={args.worker_count} | "
                    f"snapshot_interval_sweeps={args.snapshot_interval_sweeps}",
                    f"output={destination}",
                    "=" * 78,
                    sep="\n",
                    file=sys.stderr,
                    flush=True,
                )
                subprocess.run([sys.executable, str(runner), "--profile", profile,
                    "--N", str(size), "--seed", str(seed), "--background-sweeps",
                    str(args.background_sweeps), "--snapshot-interval-sweeps",
                    str(args.snapshot_interval_sweeps), "--worker-count",
                    str(args.worker_count), "--output-dir", str(destination)], check=True)
                summaries.append(json.loads(
                    (destination / "edge_response_summary.json").read_text(encoding="utf-8")
                ))
    output = Path(args.output_dir)
    with (output / "edge_response_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = sorted({key for row in summaries for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    (output / "edge_response_experiment_report.md").write_text(
        "# Edge-response batch report\n\n"
        "This report assigns no physical interpretation. Compare the generated "
        "summary CSV and per-run periodic snapshots across M0--M6.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
