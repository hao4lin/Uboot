from dataclasses import dataclass

import pytest

from uboot.dynamics import step
from uboot.kernel import Direction, State


@dataclass(frozen=True)
class Increment:
    name: str
    directions: frozenset[str]
    amount: int

    def apply(self, state: State, direction: Direction) -> State:
        return state.evolved(value=state.values.get("value", 0) + self.amount)


def test_direction_requires_a_signal() -> None:
    with pytest.raises(ValueError):
        Direction.from_signals("  ")


def test_step_selects_recomposes_and_validates() -> None:
    result = step(
        State({"value": 1}),
        Direction.from_signals("growth"),
        [
            Increment("grow", frozenset({"growth"}), 2),
            Increment("decay", frozenset({"decay"}), -1),
        ],
        {"positive": lambda state: state.values["value"] > 0},
    )

    assert result.state.values["value"] == 3
    assert result.selected_modules == ("grow",)
    assert result.failed_invariants == ()


def test_state_does_not_mutate_input_mapping() -> None:
    source = {"value": 1}
    state = State(source)
    source["value"] = 99
    assert state.values["value"] == 1
