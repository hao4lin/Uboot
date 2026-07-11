"""Minimal shared types for Uboot modules."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from uboot.kernel.raw_network import SLOT_COUNT, RawNetwork, random_network


@dataclass(frozen=True, slots=True)
class Direction:
    """Task-local signals used to select candidate modules."""

    signals: frozenset[str]

    @classmethod
    def from_signals(cls, *signals: str) -> "Direction":
        normalized = frozenset(signal.strip().lower() for signal in signals if signal.strip())
        if not normalized:
            raise ValueError("a direction requires at least one non-empty signal")
        return cls(normalized)


@dataclass(frozen=True, slots=True)
class State:
    """Immutable task-local simulation state."""

    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def evolved(self, **changes: Any) -> "State":
        return State({**self.values, **changes})


__all__ = ["Direction", "RawNetwork", "SLOT_COUNT", "State", "random_network"]
