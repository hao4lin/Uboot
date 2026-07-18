"""Stateless target-selection rules for the minimal reciprocal experiment."""

from __future__ import annotations

from random import Random
from typing import Protocol, Sequence


class MinimalEdgeRule(Protocol):
    name: str

    def choose_target(
        self,
        targets: Sequence[Sequence[int]],
        node: int,
        slot: int,
        legal_candidates: tuple[int, ...],
        rng: Random,
    ) -> tuple[int, int]:
        """Return ``(proposed_target, selected_target)``."""


class BaselineRandomReplace:
    name = "baseline_random_replace"

    def choose_target(
        self,
        targets: Sequence[Sequence[int]],
        node: int,
        slot: int,
        legal_candidates: tuple[int, ...],
        rng: Random,
    ) -> tuple[int, int]:
        del targets, node, slot
        proposed = rng.choice(legal_candidates)
        return proposed, proposed


class DirectReciprocalCandidateFirst:
    name = "direct_reciprocal_candidate_first"

    def choose_target(
        self,
        targets: Sequence[Sequence[int]],
        node: int,
        slot: int,
        legal_candidates: tuple[int, ...],
        rng: Random,
    ) -> tuple[int, int]:
        reciprocal_candidates = tuple(
            candidate
            for candidate in legal_candidates
            if targets[candidate][slot] == node
        )
        pool = reciprocal_candidates or legal_candidates
        proposed = rng.choice(pool)
        return proposed, proposed


class OldNewReciprocalCompare:
    name = "old_new_reciprocal_compare"

    def choose_target(
        self,
        targets: Sequence[Sequence[int]],
        node: int,
        slot: int,
        legal_candidates: tuple[int, ...],
        rng: Random,
    ) -> tuple[int, int]:
        old_target = targets[node][slot]
        proposed = rng.choice(legal_candidates)
        if proposed == old_target:
            return proposed, old_target
        old_is_reciprocal = targets[old_target][slot] == node
        new_is_reciprocal = targets[proposed][slot] == node
        if old_is_reciprocal != new_is_reciprocal:
            selected = old_target if old_is_reciprocal else proposed
        else:
            selected = rng.choice((old_target, proposed))
        return proposed, selected


POLICY_NAMES = (
    "baseline_random_replace",
    "direct_reciprocal_candidate_first",
    "old_new_reciprocal_compare",
)


def build_minimal_rule(name: str) -> MinimalEdgeRule:
    rules = {
        BaselineRandomReplace.name: BaselineRandomReplace,
        DirectReciprocalCandidateFirst.name: DirectReciprocalCandidateFirst,
        OldNewReciprocalCompare.name: OldNewReciprocalCompare,
    }
    if name not in rules:
        raise ValueError(f"unknown minimal edge rule: {name}")
    return rules[name]()
