"""Backward checks for task-local invariants."""

from collections.abc import Callable, Mapping

from uboot.kernel import State

Invariant = Callable[[State], bool]


def validate(state: State, invariants: Mapping[str, Invariant]) -> tuple[str, ...]:
    """Return the names of failed invariants without hiding their source."""

    return tuple(name for name, check in invariants.items() if not check(state))
