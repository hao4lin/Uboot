"""Immutable raw-object network with exactly three outgoing slots per object."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

SLOT_COUNT = 3


@dataclass(frozen=True, slots=True)
class RawNetwork:
    """A simulation handle for otherwise indistinguishable raw objects."""

    targets: tuple[tuple[int, int, int], ...]

    def __post_init__(self) -> None:
        size = len(self.targets)
        if size < 2:
            raise ValueError("a raw network requires at least two objects")
        for source, slots in enumerate(self.targets):
            if len(slots) != SLOT_COUNT:
                raise ValueError("each raw object must have exactly three slots")
            for target in slots:
                if not 0 <= target < size:
                    raise ValueError("slot target is outside the network")
                if target == source:
                    raise ValueError("raw objects cannot point to themselves")
            if len(set(slots)) != SLOT_COUNT:
                raise ValueError("each raw object must target three distinct objects")

    @property
    def size(self) -> int:
        return len(self.targets)

    def retarget(self, source: int, slot: int, target: int) -> "RawNetwork":
        """Return a network with one changed slot and all invariants rechecked."""

        if not 0 <= source < self.size:
            raise IndexError("source object is outside the network")
        if not 0 <= slot < SLOT_COUNT:
            raise IndexError("slot is outside the three-slot range")
        if not 0 <= target < self.size:
            raise IndexError("target object is outside the network")
        if target == source:
            raise ValueError("raw objects cannot point to themselves")

        rows = list(self.targets)
        updated = list(rows[source])
        updated[slot] = target
        rows[source] = tuple(updated)  # type: ignore[assignment]
        return RawNetwork(tuple(rows))


def random_network(size: int, rng: Random) -> RawNetwork:
    """Create the sole exogenous phase: uniform random initial directions."""

    if size <= SLOT_COUNT:
        raise ValueError("a three-slot raw network requires at least four objects")
    targets = tuple(
        tuple(rng.sample([target for target in range(size) if target != source], SLOT_COUNT))
        for source in range(size)
    )
    return RawNetwork(targets)  # type: ignore[arg-type]
