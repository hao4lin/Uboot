from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import pickle
from random import Random

from uboot.edge_change_policies import (
    HardReciprocalLockPolicy,
    LocalContext,
    PolicyParameters,
    RelationState,
    SoftInternalHarderPolicy,
    SoftReciprocalInertiaPolicy,
    ThresholdInternalSoftBreakPolicy,
)
from uboot.edge_policy_experiment import (
    PolicyComparisonEngine,
    run_policy_comparison,
)
from uboot.edge_response import (
    EdgeResponseEngine,
    distinct_random_network,
    policy_profile,
)
from uboot.kernel import RawNetwork


def test_baseline_wrapper_is_hash_rng_and_statistics_identical() -> None:
    seed = 731
    rng = Random(seed)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng),
        policy_profile("M2"),
        rng,
        worker_count=1,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )
    engine.run(40)
    expected_hash = sha256(
        pickle.dumps(engine.dynamics_state(), protocol=5)
    ).hexdigest()
    result = run_policy_comparison(
        size=20,
        seed=seed,
        sweeps=40,
        snapshot_sweeps=(0, 40),
        policy_names=("baseline_current",),
    )[0]
    assert result.final_hash == expected_hash
    assert result.main_rng_state == engine.rng.getstate()
    assert result.summary["current_candidate_g"] == 1.0


def test_one_internal_slot_does_not_freeze_sibling_slots() -> None:
    engine = _comparison_engine(SoftReciprocalInertiaPolicy())
    engine.relation_states[0][0] = replace(
        engine.relation_states[0][0], strength=1.0, internal=True, hard_locked=True
    )
    old = engine.targets[0][1]
    proposed = next(value for value in engine.global_candidates(0, 1) if value != old)
    engine._apply_policy_decision(0, 1, proposed, "active")
    assert engine.targets[0][1] == proposed
    assert engine.relation_states[0][0].hard_locked


def test_external_retarget_does_not_modify_internal_slot_state() -> None:
    engine = _comparison_engine(SoftReciprocalInertiaPolicy())
    internal = replace(
        engine.relation_states[0][0], strength=0.91, internal=True, hard_locked=True
    )
    engine.relation_states[0][0] = internal
    old = engine.targets[0][2]
    proposed = next(value for value in engine.global_candidates(0, 2) if value != old)
    engine._apply_policy_decision(0, 2, proposed, "response")
    assert engine.relation_states[0][0] == internal


def test_soft_reciprocity_strengthens_in_multiple_steps() -> None:
    policy = SoftReciprocalInertiaPolicy()
    context = LocalContext(True, 1, "active")
    state = RelationState(1)
    values = []
    for tick in range(3):
        decision = policy.evaluate_active_change(state, 1, context, Random(tick))
        state = policy.update_relation_strength(state, decision, context, tick)
        values.append(state.strength)
    assert 0 < values[0] < values[1] < values[2] < 1


def test_soft_relation_weakens_in_multiple_disturbance_steps() -> None:
    params = PolicyParameters(active_break_resistance=10.0)
    policy = SoftReciprocalInertiaPolicy(params)
    context = LocalContext(False, 0, "active")
    state = RelationState(1, strength=0.6)
    values = []
    for tick in range(3):
        decision = policy.evaluate_active_change(state, 2, context, Random(tick))
        assert not decision.accepted
        state = policy.update_relation_strength(state, decision, context, tick)
        values.append(state.strength)
    assert 0 < values[2] < values[1] < values[0] < 0.6


def test_promotion_and_demotion_use_hysteresis() -> None:
    policy = ThresholdInternalSoftBreakPolicy(
        PolicyParameters(promote_threshold=0.8, demote_threshold=0.5)
    )
    context = LocalContext(True, 1, "active")
    external_mid = policy._apply_internal_state(RelationState(1, 0.7), context)
    promoted = policy._apply_internal_state(RelationState(1, 0.81), context)
    internal_mid = policy._apply_internal_state(
        RelationState(1, 0.7, internal=True), context
    )
    demoted = policy._apply_internal_state(
        RelationState(1, 0.49, internal=True), context
    )
    assert not external_mid.internal
    assert promoted.internal and internal_mid.internal
    assert not demoted.internal


def test_active_and_response_resistance_are_independent() -> None:
    policy = SoftReciprocalInertiaPolicy(
        PolicyParameters(
            active_break_resistance=2.0,
            response_break_resistance=0.0,
        )
    )
    state = RelationState(1, strength=0.6)
    context = LocalContext(False, 0, "active")
    active = policy.evaluate_active_change(state, 2, context, Random(1))
    response = policy.evaluate_response_effect(
        state, 2, replace(context, event_kind="response"), Random(1)
    )
    assert not active.accepted
    assert response.accepted


def test_sampling_old_target_is_natural_hold_not_resistance() -> None:
    policy = SoftReciprocalInertiaPolicy()
    state = RelationState(3, strength=1.0)
    decision = policy.evaluate_active_change(
        state, 3, LocalContext(True, 1, "active"), Random(0)
    )
    assert decision.action == "same_target_sampled"
    assert decision.reason == "natural_hold"


def test_policy_rng_does_not_advance_main_rng() -> None:
    main_rng = Random(41)
    expected = Random(41)
    policy_rng = Random(9001)
    policy = SoftReciprocalInertiaPolicy()
    state = RelationState(1, strength=0.5)
    for _ in range(100):
        proposed = policy.choose_candidate((1, 2, 3), 1, policy_rng)
        policy.evaluate_active_change(
            state, int(proposed), LocalContext(False, 0, "active"), policy_rng
        )
    assert main_rng.getstate() == expected.getstate()


def test_new_reciprocal_hard_locks_are_monotone() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (2, 3, 4),
            (3, 4, 0),
            (4, 0, 1),
            (0, 1, 2),
        )
    )
    engine = PolicyComparisonEngine(
        network, Random(0), HardReciprocalLockPolicy(), Random(1)
    )
    engine._apply_policy_decision(0, 0, 4, "active")
    assert engine.relation_states[0][0].hard_locked
    assert engine.relation_states[4][0].hard_locked
    engine._apply_policy_decision(0, 0, 1, "response")
    assert engine.targets[0][0] == 4
    assert engine.relation_states[0][0].hard_locked


def test_external_slots_keep_drifting_beside_internal_slot() -> None:
    engine = _comparison_engine(SoftReciprocalInertiaPolicy())
    engine.relation_states[0][0] = replace(
        engine.relation_states[0][0], internal=True, hard_locked=True
    )
    observed = {engine.targets[0][1]}
    for _ in range(8):
        old = engine.targets[0][1]
        proposed = next(
            value for value in engine.global_candidates(0, 1) if value != old
        )
        engine._apply_policy_decision(0, 1, proposed, "active")
        observed.add(engine.targets[0][1])
    assert len(observed) > 1
    assert engine.edge_counts["external_slot_change_count"] == 8
    assert engine.relation_states[0][0].hard_locked


def test_closure_supported_relation_has_longer_synthetic_lifetime() -> None:
    policy = SoftInternalHarderPolicy()
    state = RelationState(1, strength=0.5)
    external_context = LocalContext(False, 0, "active")
    internal_context = LocalContext(True, 2, "active")
    external_breaks = sum(
        policy.evaluate_active_change(state, 2, external_context, Random(seed)).accepted
        for seed in range(500)
    )
    internal_breaks = sum(
        policy.evaluate_active_change(state, 2, internal_context, Random(seed)).accepted
        for seed in range(500)
    )
    assert internal_breaks < external_breaks


def test_strategy_run_preserves_three_distinct_targets() -> None:
    main_rng = Random(17)
    engine = PolicyComparisonEngine(
        distinct_random_network(20, main_rng),
        main_rng,
        SoftInternalHarderPolicy(),
        Random(30_024),
    )
    engine.run(30)
    assert all(len(set(row)) == 3 for row in engine.snapshot().targets)


def _comparison_engine(policy: SoftReciprocalInertiaPolicy) -> PolicyComparisonEngine:
    network = distinct_random_network(8, Random(19))
    return PolicyComparisonEngine(network, Random(23), policy, Random(29))
