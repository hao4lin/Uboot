"""High-level selection, recomposition, execution, and validation."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from uboot.fusion import FusionPlan
from uboot.kernel import Direction, State
from uboot.modules import Module
from uboot.selection import select_modules
from uboot.validation import Invariant, validate


@dataclass(frozen=True, slots=True)
class StepResult:
    state: State
    selected_modules: tuple[str, ...]
    failed_invariants: tuple[str, ...]


def step(
    initial: State,
    direction: Direction,
    modules: Iterable[Module],
    invariants: Mapping[str, Invariant] | None = None,
) -> StepResult:
    """Run one explicit select-fuse-execute-validate cycle."""

    selected = select_modules(modules, direction)
    result = FusionPlan(direction, selected).run(initial)
    failures = validate(result, invariants or {})
    return StepResult(result, tuple(module.name for module in selected), failures)
