"""Recompose selected modules into a task-local execution plan."""

from dataclasses import dataclass

from uboot.kernel import Direction, State
from uboot.modules import Module


@dataclass(frozen=True, slots=True)
class FusionPlan:
    """An explicit composition boundary created before simulation work begins."""

    direction: Direction
    modules: tuple[Module, ...]

    def run(self, initial: State) -> State:
        state = initial
        for module in self.modules:
            state = module.apply(state, self.direction)
        return state
