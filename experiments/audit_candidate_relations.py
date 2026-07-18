"""Run one short M1 state and audit mutual pairs against candidate relations."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from random import Random

from uboot.candidate_audit import diagnose_mutual_candidates
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--N", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--background-sweeps", type=int, default=100)
    parser.add_argument("--worker-count", type=int, default=1)
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--output-dir", default="artifacts/candidate_relation_audit")
    args = parser.parse_args()
    rng = Random(args.seed)
    engine = EdgeResponseEngine(
        distinct_random_network(args.N, rng),
        policy_profile("M1"),
        rng,
        worker_count=args.worker_count,
        mode="M1",
    )
    engine.run(args.background_sweeps)
    rows, summary = diagnose_mutual_candidates(
        engine.snapshot(), sample_limit=args.sample_limit
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write(output / "mutual_candidate_examples.csv", rows)
    (output / "mutual_candidate_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary))


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
