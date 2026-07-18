"""Pure edge-change policies for the independent comparison experiment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from random import Random
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RelationState:
    target_id: int
    strength: float = 0.0
    internal: bool = False
    hard_locked: bool = False
    born_tick: int = 0
    last_change_tick: int = 0


@dataclass(frozen=True, slots=True)
class LocalContext:
    reciprocal: bool
    closure_support_level: int
    event_kind: str


@dataclass(frozen=True, slots=True)
class EdgeDecision:
    action: str
    accepted: bool
    new_target: int | None
    strength_delta: float
    reason: str


@dataclass(frozen=True, slots=True)
class PolicyParameters:
    same_target_reinforce: float = 0.02
    reciprocal_reinforce: float = 0.10
    pair_bonus: float = 0.04
    triangle_bonus: float = 0.08
    nonreciprocal_decay: float = 0.01
    active_disturb: float = 0.05
    response_disturb: float = 0.05
    active_break_resistance: float = 1.0
    response_break_resistance: float = 1.0
    residual_factor: float = 0.20
    promote_threshold: float = 0.85
    demote_threshold: float = 0.55
    strength_graph_threshold: float = 0.67

    def __post_init__(self) -> None:
        if not 0 <= self.demote_threshold < self.promote_threshold <= 1:
            raise ValueError("thresholds must satisfy 0 <= demote < promote <= 1")


class EdgeChangePolicy(Protocol):
    name: str
    uses_strength: bool
    parameters: PolicyParameters

    def choose_candidate(
        self,
        global_candidates: tuple[int, ...],
        old_target: int,
        policy_rng: Random,
    ) -> int | None: ...

    def evaluate_active_change(
        self,
        relation_state: RelationState,
        proposed_target: int,
        local_context: LocalContext,
        policy_rng: Random,
    ) -> EdgeDecision: ...

    def evaluate_response_effect(
        self,
        relation_state: RelationState,
        proposed_target: int,
        local_context: LocalContext,
        policy_rng: Random,
    ) -> EdgeDecision: ...

    def update_relation_strength(
        self,
        relation_state: RelationState,
        decision: EdgeDecision,
        local_context: LocalContext,
        tick: int,
    ) -> RelationState: ...

    def may_promote_internal(
        self, relation_state: RelationState, local_context: LocalContext
    ) -> bool: ...


class _PolicyBase:
    name = "base"
    uses_strength = True

    def __init__(self, parameters: PolicyParameters | None = None) -> None:
        self.parameters = parameters or PolicyParameters()

    def choose_candidate(
        self,
        global_candidates: tuple[int, ...],
        old_target: int,
        policy_rng: Random,
    ) -> int | None:
        del old_target
        return policy_rng.choice(global_candidates) if global_candidates else None

    def evaluate_active_change(
        self,
        relation_state: RelationState,
        proposed_target: int,
        local_context: LocalContext,
        policy_rng: Random,
    ) -> EdgeDecision:
        return self._evaluate(
            relation_state, proposed_target, local_context, policy_rng, "active"
        )

    def evaluate_response_effect(
        self,
        relation_state: RelationState,
        proposed_target: int,
        local_context: LocalContext,
        policy_rng: Random,
    ) -> EdgeDecision:
        return self._evaluate(
            relation_state, proposed_target, local_context, policy_rng, "response"
        )

    def _evaluate(
        self,
        state: RelationState,
        proposed: int,
        context: LocalContext,
        rng: Random,
        event_kind: str,
    ) -> EdgeDecision:
        if proposed == state.target_id:
            return EdgeDecision(
                "same_target_sampled", False, state.target_id, 0.0, "natural_hold"
            )
        if state.hard_locked:
            return EdgeDecision(
                "change_resisted", False, state.target_id, 0.0, "hard_locked"
            )
        resistance = self._resistance(state, context, event_kind)
        if rng.random() < resistance:
            disturb = (
                self.parameters.active_disturb
                if event_kind == "active"
                else self.parameters.response_disturb
            )
            return EdgeDecision(
                "change_resisted", False, state.target_id, -disturb, "soft_inertia"
            )
        return EdgeDecision("change_accepted", True, proposed, 0.0, "accepted")

    def _resistance(
        self, state: RelationState, context: LocalContext, event_kind: str
    ) -> float:
        multiplier = (
            self.parameters.active_break_resistance
            if event_kind == "active"
            else self.parameters.response_break_resistance
        )
        return _clamp(state.strength * multiplier)

    def update_relation_strength(
        self,
        state: RelationState,
        decision: EdgeDecision,
        context: LocalContext,
        tick: int,
    ) -> RelationState:
        if decision.accepted and decision.new_target != state.target_id:
            strength = state.strength * self.parameters.residual_factor
            return RelationState(
                target_id=int(decision.new_target),
                strength=_clamp(strength),
                internal=False,
                hard_locked=False,
                born_tick=tick,
                last_change_tick=tick,
            )
        delta = decision.strength_delta
        if decision.action == "same_target_sampled":
            delta += self.parameters.same_target_reinforce
            if context.reciprocal:
                delta += self.parameters.reciprocal_reinforce
            else:
                delta -= self.parameters.nonreciprocal_decay
            delta += self._closure_bonus(context.closure_support_level)
        elif not context.reciprocal:
            delta -= self.parameters.nonreciprocal_decay
        updated = replace(state, strength=_clamp(state.strength + delta))
        return self._apply_internal_state(updated, context)

    def _closure_bonus(self, level: int) -> float:
        if level >= 2:
            return self.parameters.triangle_bonus
        if level == 1:
            return self.parameters.pair_bonus
        return 0.0

    def _apply_internal_state(
        self, state: RelationState, context: LocalContext
    ) -> RelationState:
        if state.internal and state.strength < self.parameters.demote_threshold:
            return replace(state, internal=False, hard_locked=False)
        if not state.internal and self.may_promote_internal(state, context):
            return replace(state, internal=True)
        return state

    def may_promote_internal(
        self, relation_state: RelationState, local_context: LocalContext
    ) -> bool:
        del relation_state, local_context
        return False

    def config(self) -> dict[str, object]:
        return {"name": self.name, "parameters": asdict(self.parameters)}


class HardReciprocalLockPolicy(_PolicyBase):
    name = "hard_reciprocal_lock"
    uses_strength = False

    def _evaluate(
        self,
        state: RelationState,
        proposed: int,
        context: LocalContext,
        rng: Random,
        event_kind: str,
    ) -> EdgeDecision:
        del rng, event_kind
        if proposed == state.target_id:
            return EdgeDecision(
                "same_target_sampled", False, state.target_id, 0.0, "natural_hold"
            )
        if state.hard_locked or context.reciprocal:
            return EdgeDecision(
                "change_resisted", False, state.target_id, 0.0, "reciprocal_lock"
            )
        return EdgeDecision("change_accepted", True, proposed, 0.0, "accepted")

    def update_relation_strength(
        self,
        state: RelationState,
        decision: EdgeDecision,
        context: LocalContext,
        tick: int,
    ) -> RelationState:
        if decision.accepted and decision.new_target != state.target_id:
            return RelationState(
                int(decision.new_target), born_tick=tick, last_change_tick=tick
            )
        if context.reciprocal:
            return replace(state, internal=True, hard_locked=True)
        return state


class SoftReciprocalInertiaPolicy(_PolicyBase):
    name = "soft_reciprocal_inertia"


class SoftInternalHarderPolicy(_PolicyBase):
    name = "soft_internal_harder_than_external"

    def _resistance(
        self, state: RelationState, context: LocalContext, event_kind: str
    ) -> float:
        base = super()._resistance(state, context, event_kind)
        return _clamp(base + 0.08 * context.closure_support_level)


class ThresholdInternalSoftBreakPolicy(SoftInternalHarderPolicy):
    name = "threshold_internal_soft_break"

    def may_promote_internal(
        self, relation_state: RelationState, local_context: LocalContext
    ) -> bool:
        return relation_state.strength >= self.parameters.promote_threshold and (
            local_context.reciprocal or local_context.closure_support_level > 0
        )

    def _resistance(
        self, state: RelationState, context: LocalContext, event_kind: str
    ) -> float:
        base = super()._resistance(state, context, event_kind)
        return _clamp(base + (0.15 if state.internal else 0.0))


class CertifiedInternalHardExternalFreePolicy(ThresholdInternalSoftBreakPolicy):
    name = "certified_internal_hard_external_free"

    def may_promote_internal(
        self, relation_state: RelationState, local_context: LocalContext
    ) -> bool:
        return (
            relation_state.strength >= self.parameters.promote_threshold
            and local_context.reciprocal
            and local_context.closure_support_level >= 2
        )

    def _apply_internal_state(
        self, state: RelationState, context: LocalContext
    ) -> RelationState:
        updated = super()._apply_internal_state(state, context)
        if (
            updated.internal
            and context.reciprocal
            and context.closure_support_level >= 2
        ):
            return replace(updated, hard_locked=True)
        return updated


POLICY_NAMES = (
    "baseline_current",
    "hard_reciprocal_lock",
    "soft_reciprocal_inertia",
    "soft_internal_harder_than_external",
    "threshold_internal_soft_break",
    "certified_internal_hard_external_free",
)

POLICY_SEED_OFFSETS = {
    "hard_reciprocal_lock": 10_001,
    "soft_reciprocal_inertia": 20_003,
    "soft_internal_harder_than_external": 30_007,
    "threshold_internal_soft_break": 40_009,
    "certified_internal_hard_external_free": 50_021,
}


def build_policy(
    name: str, parameters: PolicyParameters | None = None
) -> EdgeChangePolicy:
    types = {
        "hard_reciprocal_lock": HardReciprocalLockPolicy,
        "soft_reciprocal_inertia": SoftReciprocalInertiaPolicy,
        "soft_internal_harder_than_external": SoftInternalHarderPolicy,
        "threshold_internal_soft_break": ThresholdInternalSoftBreakPolicy,
        "certified_internal_hard_external_free": CertifiedInternalHardExternalFreePolicy,
    }
    if name not in types:
        raise ValueError(f"unknown non-baseline edge policy: {name}")
    return types[name](parameters)


def strength_band(value: float) -> str:
    if value < 0.2:
        return "[0.0,0.2)"
    if value < 0.4:
        return "[0.2,0.4)"
    if value < 0.6:
        return "[0.4,0.6)"
    if value < 0.8:
        return "[0.6,0.8)"
    return "[0.8,1.0]"


def transition_band(value: float) -> str:
    if value < 0.33:
        return "low"
    if value < 0.67:
        return "medium"
    return "high"


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
