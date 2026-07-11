"""Purely relation-endogenous rewrites after random initialization."""

from dataclasses import dataclass
from random import Random

from uboot.kernel.raw_network import SLOT_COUNT, RawNetwork
from uboot.observables import mutual_pair_count
from uboot.selection.endogenous import endogenous_candidates


@dataclass(frozen=True, slots=True)
class Rewrite:
    network: RawNetwork
    source: int
    slot: int
    previous_target: int
    target: int
    candidate_count: int


@dataclass(frozen=True, slots=True)
class Sample:
    step: int
    mutual_pairs: int
    mutual_slot_density: float


def rewrite_once(network: RawNetwork, rng: Random) -> Rewrite:
    """Rewrite one random slot using only candidates produced by relations."""

    source = rng.randrange(network.size)
    slot = rng.randrange(SLOT_COUNT)
    candidates = endogenous_candidates(network, source)
    if not candidates:
        raise RuntimeError("endogenous candidate set is empty")
    target = rng.choice(candidates)
    previous_target = network.targets[source][slot]
    return Rewrite(
        network.retarget(source, slot, target),
        source,
        slot,
        previous_target,
        target,
        len(candidates),
    )


def simulate(
    initial: RawNetwork,
    steps: int,
    rng: Random,
    *,
    sample_every: int = 1,
) -> tuple[RawNetwork, tuple[Sample, ...]]:
    """Run endogenous rewrites and sample mutual connections over normalized time."""

    if steps < 0:
        raise ValueError("steps cannot be negative")
    if sample_every < 1:
        raise ValueError("sample_every must be positive")

    engine = _EndogenousEngine(initial, rng)
    samples = [_sample(initial, 0)]
    for step in range(1, steps + 1):
        engine.rewrite()
        if step % sample_every == 0 or step == steps:
            samples.append(_sample(engine.snapshot(), step))
    return engine.snapshot(), tuple(samples)


def _sample(network: RawNetwork, step: int) -> Sample:
    pairs = mutual_pair_count(network)
    return Sample(step, pairs, 2 * pairs / (SLOT_COUNT * network.size))


class _EndogenousEngine:
    """Mutable local executor with a reverse index; snapshots remain immutable."""

    def __init__(self, network: RawNetwork, rng: Random) -> None:
        self.targets = [list(slots) for slots in network.targets]
        self.incoming = [dict[int, int]() for _ in range(network.size)]
        self.rng = rng
        for source, slots in enumerate(self.targets):
            for target in slots:
                counts = self.incoming[target]
                counts[source] = counts.get(source, 0) + 1

    def rewrite(self) -> None:
        source = self.rng.randrange(len(self.targets))
        slot = self.rng.randrange(SLOT_COUNT)
        candidates = set(self.incoming[source])
        candidates.update(
            target
            for neighbor in self.targets[source]
            for target in self.targets[neighbor]
            if target != source
        )
        candidates.discard(source)
        if not candidates:
            raise RuntimeError("endogenous candidate set is empty")

        target = self.rng.choice(sorted(candidates))
        previous = self.targets[source][slot]
        if target == previous:
            return

        previous_counts = self.incoming[previous]
        previous_counts[source] -= 1
        if previous_counts[source] == 0:
            del previous_counts[source]
        new_counts = self.incoming[target]
        new_counts[source] = new_counts.get(source, 0) + 1
        self.targets[source][slot] = target

    def snapshot(self) -> RawNetwork:
        return RawNetwork(tuple(tuple(slots) for slots in self.targets))  # type: ignore[arg-type]
