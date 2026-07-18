from __future__ import annotations

import inspect
from random import Random

import uboot.minimal_edge_experiment as experiment_module
import uboot.minimal_edge_rules as rules_module
from uboot.kernel import RawNetwork
from uboot.minimal_edge_experiment import (
    MinimalEdgeEngine,
    StructureLifetimeTracker,
    initial_network,
    minimal_snapshot,
    network_hash,
    q_simple_graph,
    run_baseline_reference,
    run_minimal_comparison,
)
from uboot.minimal_edge_rules import (
    BaselineRandomReplace,
    DirectReciprocalCandidateFirst,
    OldNewReciprocalCompare,
)


def test_legal_global_candidates_allow_old_and_exclude_other_slots() -> None:
    network = initial_network(8, Random(1))
    engine = MinimalEdgeEngine(network, Random(2), BaselineRandomReplace())
    node, slot = 0, 1
    old = engine.targets[node][slot]
    legal = engine.legal_candidates(node, slot)
    assert old in legal
    assert node not in legal
    assert all(
        target not in legal
        for index, target in enumerate(engine.targets[node])
        if index != slot
    )


def test_baseline_wrapper_matches_direct_reference_hash_and_rng() -> None:
    result = run_minimal_comparison(
        size=20,
        seed=20260712,
        sweeps=40,
        snapshot_sweeps=(0, 40),
        policy_names=("baseline_random_replace",),
    )[0]
    network, rng_state = run_baseline_reference(size=20, seed=20260712, sweeps=40)
    assert result.final_hash == network_hash(network)
    assert result.rng_state == rng_state


def test_direct_reciprocal_rule_uses_only_legal_returners() -> None:
    targets = (
        (1, 2, 3),
        (0, 2, 3),
        (0, 1, 3),
        (0, 1, 2),
        (0, 1, 2),
    )
    proposed, selected = DirectReciprocalCandidateFirst().choose_target(
        targets, 0, 0, (1, 4), Random(4)
    )
    assert proposed == selected
    assert selected in {1, 4}
    assert targets[selected][0] == 0


def test_direct_reciprocal_rule_falls_back_to_global_pool() -> None:
    targets = (
        (1, 2, 3),
        (2, 3, 4),
        (3, 4, 0),
        (4, 0, 1),
        (1, 2, 3),
    )
    first = Random(8)
    second = Random(8)
    selected = DirectReciprocalCandidateFirst().choose_target(
        targets, 0, 0, (1, 4), first
    )
    baseline = BaselineRandomReplace().choose_target(targets, 0, 0, (1, 4), second)
    assert selected == baseline
    assert first.getstate() == second.getstate()


def test_old_new_compare_prefers_new_when_only_new_is_reciprocal() -> None:
    targets = [[1, 2, 3] for _ in range(5)]
    targets[1][0] = 2
    targets[4][0] = 0
    rng = ChoiceStub([4])
    assert OldNewReciprocalCompare().choose_target(targets, 0, 0, (1, 4), rng) == (
        4,
        4,
    )


def test_old_new_compare_keeps_old_when_only_old_is_reciprocal() -> None:
    targets = [[1, 2, 3] for _ in range(5)]
    targets[1][0] = 0
    targets[4][0] = 2
    rng = ChoiceStub([4])
    assert OldNewReciprocalCompare().choose_target(targets, 0, 0, (1, 4), rng) == (
        4,
        1,
    )


def test_old_new_compare_randomizes_when_statuses_match() -> None:
    targets = [[1, 2, 3] for _ in range(5)]
    targets[1][0] = 2
    targets[4][0] = 2
    rng = ChoiceStub([4, 1])
    assert OldNewReciprocalCompare().choose_target(targets, 0, 0, (1, 4), rng) == (
        4,
        1,
    )
    assert rng.calls == 2


def test_old_new_compare_naturally_holds_same_proposal_without_extra_rng() -> None:
    targets = [[1, 2, 3] for _ in range(5)]
    rng = ChoiceStub([1])
    assert OldNewReciprocalCompare().choose_target(targets, 0, 0, (1, 4), rng) == (
        1,
        1,
    )
    assert rng.calls == 1


def test_transition_counts_cover_each_selected_slot_update() -> None:
    rng = Random(10)
    engine = MinimalEdgeEngine(initial_network(20, rng), rng, OldNewReciprocalCompare())
    for _ in range(500):
        engine.step()
    transition_count = sum(
        engine.counters[f"transition_{kind}"] for kind in ("nn", "nr", "rr", "rn")
    )
    assert transition_count >= 500
    assert (
        engine.counters["target_keep_count"] + engine.counters["target_change_count"]
        == 500
    )


def test_partner_reciprocity_loss_and_gain_are_both_observed() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 2, 3),
            (3, 4, 0),
            (4, 0, 1),
            (0, 1, 2),
        )
    )
    engine = MinimalEdgeEngine(network, RandrangeStub([0, 0]), FixedRule(4))  # type: ignore[arg-type]
    engine.step()
    assert engine.counters["transition_rr"] == 1
    assert engine.counters["transition_rn"] == 1
    assert engine.counters["transition_nr"] == 1


def test_q_graph_and_triangle_component_are_derived_only_from_slots() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 3, 2),
            (3, 0, 1),
            (4, 5, 1),
            (5, 3, 0),
            (3, 4, 0),
        )
    )
    graph = q_simple_graph(network)
    assert graph[0] == (1, 2)
    assert graph[1] == (0, 2)
    assert graph[2] == (0, 1)
    engine = MinimalEdgeEngine(network, Random(1), BaselineRandomReplace())
    row, structures = minimal_snapshot("baseline_random_replace", 1, 0, engine)
    assert row["Q_triangle_component_count"] == 1
    assert ("triangle", (0, 1, 2)) in structures


def test_structure_tracker_separates_reappearance_episodes_and_censoring() -> None:
    tracker = StructureLifetimeTracker("p", 1)
    structure = ("pair", (1, 2))
    tracker.observe(0, (structure,))
    tracker.observe(100, (structure,))
    tracker.observe(300, ())
    tracker.observe(1000, (structure,))
    rows = tracker.rows()
    assert len(rows) == 2
    assert rows[0]["episode"] == 1
    assert rows[0]["observed_lifetime"] == 100
    assert not rows[0]["right_censored"]
    assert rows[1]["episode"] == 2
    assert rows[1]["right_censored"]


def test_relation_ages_are_observational_and_current_relations_are_censored() -> None:
    result = run_minimal_comparison(
        size=20,
        seed=33,
        sweeps=10,
        snapshot_sweeps=(0, 10),
        policy_names=("baseline_random_replace",),
    )[0]
    assert result.summary["mean_completed_relation_age"] != "N/A"
    assert result.summary["right_censored_relation_count"] == 60


def test_same_policy_is_exactly_reproducible() -> None:
    kwargs = dict(
        size=20,
        seed=87,
        sweeps=25,
        snapshot_sweeps=(0, 10, 25),
        policy_names=("direct_reciprocal_candidate_first",),
    )
    first = run_minimal_comparison(**kwargs)[0]
    second = run_minimal_comparison(**kwargs)[0]
    assert first.final_hash == second.final_hash
    assert first.rng_state == second.rng_state
    assert first.time_series == second.time_series


def test_every_update_preserves_three_distinct_targets() -> None:
    rng = Random(101)
    engine = MinimalEdgeEngine(
        initial_network(20, rng), rng, DirectReciprocalCandidateFirst()
    )
    for _ in range(2_000):
        engine.step()
    assert all(len(set(row)) == 3 for row in engine.targets)


def test_new_experiment_has_no_strength_scoring_or_lock_path() -> None:
    source = inspect.getsource(rules_module) + inspect.getsource(experiment_module)
    forbidden = (
        "relation_strength",
        "closure_bonus",
        "promote_threshold",
        "demote_threshold",
        "hard_lock",
        "soft_lock",
        "internal_candidate",
        "certified_internal",
    )
    assert all(token not in source for token in forbidden)


class ChoiceStub:
    def __init__(self, values: list[int]) -> None:
        self.values = iter(values)
        self.calls = 0

    def choice(self, values: object) -> int:
        del values
        self.calls += 1
        return next(self.values)


class RandrangeStub:
    def __init__(self, values: list[int]) -> None:
        self.values = iter(values)

    def randrange(self, stop: int) -> int:
        del stop
        return next(self.values)


class FixedRule:
    name = "fixed"

    def __init__(self, selected: int) -> None:
        self.selected = selected

    def choose_target(
        self,
        targets: object,
        node: int,
        slot: int,
        legal_candidates: tuple[int, ...],
        rng: object,
    ) -> tuple[int, int]:
        del targets, node, slot, legal_candidates, rng
        return self.selected, self.selected
