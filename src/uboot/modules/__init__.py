"""Small, weakly coupled simulation modules."""

from typing import Protocol

from uboot.kernel import Direction, State


class Module(Protocol):
    """Minimum interface implemented by a simulation module."""

    name: str
    directions: frozenset[str]

    def apply(self, state: State, direction: Direction) -> State:
        """Return a new state without mutating the input state."""
