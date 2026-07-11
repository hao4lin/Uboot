"""Run the first-to-second-step relation-endogenous bootstrap experiment."""

from __future__ import annotations

import argparse
from random import Random
import sys
from time import monotonic

from uboot.dynamics.endogenous import Sample, simulate
from uboot.kernel import SLOT_COUNT, random_network
from uboot.observables import mutual_pair_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objects", type=int, default=1_000)
    parser.add_argument("--sweeps", type=float, default=100.0)
    parser.add_argument("--sample-sweeps", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    if args.sweeps < 0:
        parser.error("--sweeps cannot be negative")
    if args.sample_sweeps <= 0:
        parser.error("--sample-sweeps must be positive")

    rng = Random(args.seed)
    network = random_network(args.objects, rng)
    slots = SLOT_COUNT * args.objects
    steps = round(args.sweeps * slots)
    sample_every = max(1, round(args.sample_sweeps * slots))
    started = monotonic()
    last_report = started

    def report_progress(completed: int, total: int, sample: Sample) -> None:
        nonlocal last_report
        now = monotonic()
        if now - last_report < 60 and completed < total:
            return
        percent = 100.0 if total == 0 else 100 * completed / total
        completed_sweeps = completed / slots
        print(
            f"progress: {percent:6.2f}% "
            f"({completed_sweeps:.6g}/{args.sweeps:.6g} sweeps) "
            f"mutual_pairs={sample.mutual_pairs} "
            f"density={sample.mutual_slot_density:.6g} "
            f"sample_sweep={sample.step / slots:.6g}",
            file=sys.stderr,
            flush=True,
        )
        last_report = now

    if steps == 0:
        initial_pairs = mutual_pair_count(network)
        report_progress(
            0,
            0,
            Sample(0, initial_pairs, 2 * initial_pairs / slots),
        )
    final, samples = simulate(
        network,
        steps,
        rng,
        sample_every=sample_every,
        progress=report_progress,
    )

    print("sweep,mutual_pairs,mutual_slot_density")
    for sample in samples:
        print(
            f"{sample.step / slots:.6g},{sample.mutual_pairs},"
            f"{sample.mutual_slot_density:.12g}"
        )
    print(f"# objects={final.size} seed={args.seed} steps={steps}")


if __name__ == "__main__":
    main()
