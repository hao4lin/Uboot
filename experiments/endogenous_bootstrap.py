"""Run the first-to-second-step relation-endogenous bootstrap experiment."""

from __future__ import annotations

import argparse
from random import Random

from uboot.dynamics.endogenous import simulate
from uboot.kernel import SLOT_COUNT, random_network


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
    final, samples = simulate(network, steps, rng, sample_every=sample_every)

    print("sweep,mutual_pairs,mutual_slot_density")
    for sample in samples:
        print(
            f"{sample.step / slots:.6g},{sample.mutual_pairs},"
            f"{sample.mutual_slot_density:.12g}"
        )
    print(f"# objects={final.size} seed={args.seed} steps={steps}")


if __name__ == "__main__":
    main()
