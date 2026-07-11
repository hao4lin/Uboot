"""Directional module selection."""

from collections.abc import Iterable

from uboot.kernel import Direction
from uboot.modules import Module


def select_modules(modules: Iterable[Module], direction: Direction) -> tuple[Module, ...]:
    """Select only modules whose declared directions overlap the task direction."""

    ranked = []
    for module in modules:
        overlap = len(module.directions & direction.signals)
        if overlap:
            ranked.append((-overlap, module.name, module))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in ranked)
