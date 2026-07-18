from __future__ import annotations

from random import Random

from uboot.adaptive_relation_tracer import (
    PairSliceQuery,
    ThreeMemberSliceQuery,
    generate_slice,
    trace_interval,
)
from uboot.deterministic_replay import build_replay_oracle
from uboot.kernel import RawNetwork
from uboot.minimal_edge_experiment import MinimalEdgeEngine, initial_network
from uboot.minimal_edge_rules import BaselineRandomReplace


def test_slice_generation_is_pure_and_byte_identical() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 2, 3),
            (0, 1, 3),
            (0, 1, 2),
        )
    )
    query = ThreeMemberSliceQuery((2, 0, 1))
    first = generate_slice(network, query)
    second = generate_slice(network, query)
    assert first.serialization == second.serialization
    assert first.slice_hash == second.slice_hash


def test_equal_endpoints_with_hidden_a_b_a_changes_are_found() -> None:
    oracle, query, left, right = _oracle_with_pair_return()
    endpoints = (
        oracle.inspect_relation_slice(left, query),
        oracle.inspect_relation_slice(right, query),
    )
    assert endpoints[0].slice_hash == endpoints[1].slice_hash
    result = trace_interval(
        oracle,
        candidate_id="return",
        left_locator=left,
        right_locator=right,
        query=query,
        max_probes=500,
        max_replay_atoms=1_000_000,
    )
    assert result.status == "LOCALIZED_CHANGES"
    assert len(result.localized_commits) >= 2


def test_equal_endpoints_without_relevant_updates_close_safely() -> None:
    oracle = build_replay_oracle(
        size=10,
        seed=3,
        max_locator=40,
        checkpoint_locators=(0, 40),
        impact_index_mode="all-commits",
    )
    impact = next(oracle.impact_index.records(0, 1))
    untouched = next(
        pair
        for pair in ((a, b) for a in range(10) for b in range(a + 1, 10))
        if set(pair).isdisjoint(
            {impact.source_raw_id, impact.old_target, impact.new_target}
        )
    )
    result = trace_interval(
        oracle,
        candidate_id="quiet",
        left_locator=0,
        right_locator=1,
        query=PairSliceQuery(*untouched),
    )
    assert result.status == "CLOSED_NO_RELEVANT_UPDATE"


def test_relevant_update_that_cannot_change_pair_is_recorded() -> None:
    oracle = build_replay_oracle(
        size=10,
        seed=5,
        max_locator=20,
        checkpoint_locators=(0, 20),
        impact_index_mode="all-commits",
    )
    impact = next(oracle.impact_index.records(0, 1))
    other = next(
        raw_id
        for raw_id in range(10)
        if raw_id
        not in {impact.source_raw_id, impact.old_target, impact.new_target}
    )
    result = trace_interval(
        oracle,
        candidate_id="related-no-change",
        left_locator=0,
        right_locator=1,
        query=PairSliceQuery(impact.source_raw_id, other),
    )
    assert result.status == "RELEVANT_UPDATE_NO_SLICE_CHANGE"
    assert result.relevant_update_no_slice_change_count == 1


def test_change_localizes_to_one_commit_and_resource_limit_is_not_no_structure() -> None:
    oracle, query, left, right = _oracle_with_pair_return()
    change = next(
        record
        for record in oracle.impact_index.records(left, right)
        if generate_slice(oracle.replay_to(record.commit_locator - 1).network, query).slice_hash
        != generate_slice(oracle.replay_to(record.commit_locator).network, query).slice_hash
    )
    localized = trace_interval(
        oracle,
        candidate_id="one",
        left_locator=change.commit_locator - 1,
        right_locator=change.commit_locator,
        query=query,
        max_probes=10,
        max_replay_atoms=10_000,
    )
    assert localized.localized_commits[0].responsible_commit_locator == (
        change.commit_locator
    )
    limited = trace_interval(
        oracle,
        candidate_id="limited",
        left_locator=left,
        right_locator=right,
        query=query,
        max_probes=2,
        max_replay_atoms=10_000,
    )
    assert limited.status == "INCOMPLETE_RESOURCE_LIMIT"


def test_locator_or_probe_logging_does_not_enter_slice_identity() -> None:
    oracle = build_replay_oracle(
        size=8,
        seed=4,
        max_locator=20,
        checkpoint_locators=(0, 20),
        impact_index_mode="all-commits",
    )
    query = PairSliceQuery(0, 1)
    first = oracle.inspect_relation_slice(0, query)
    result = trace_interval(
        oracle,
        candidate_id="logs",
        left_locator=0,
        right_locator=0,
        query=query,
    )
    assert result.probes
    second = oracle.inspect_relation_slice(0, query)
    assert first.serialization == second.serialization
    assert "locator" not in first.serialization


def test_missing_impact_index_never_proves_an_interval_unchanged() -> None:
    oracle = build_replay_oracle(
        size=8,
        seed=4,
        max_locator=20,
        checkpoint_locators=(0, 20),
        impact_index_mode="none",
    )
    result = trace_interval(
        oracle,
        candidate_id="no-index",
        left_locator=0,
        right_locator=20,
        query=PairSliceQuery(0, 1),
    )
    assert result.status == "INCOMPLETE_RESOURCE_LIMIT"
    assert result.probes[-1].decision.endswith("NO_IMPACT_INDEX")


def _oracle_with_pair_return():
    size, seed, max_locator = 8, 17, 600
    oracle = build_replay_oracle(
        size=size,
        seed=seed,
        max_locator=max_locator,
        checkpoint_locators=(0, 100, 300, 600),
        impact_index_mode="all-commits",
    )
    rng = Random(seed)
    engine = MinimalEdgeEngine(initial_network(size, rng), rng, BaselineRandomReplace())
    history = [engine.snapshot()]
    while engine.tick < max_locator:
        engine.step()
        history.append(engine.snapshot())
    for raw_a in range(size):
        for raw_b in range(raw_a + 1, size):
            query = PairSliceQuery(raw_a, raw_b)
            hashes = [generate_slice(network, query).slice_hash for network in history]
            for left in range(len(hashes) - 2):
                first_change = next(
                    (
                        index
                        for index in range(left + 1, len(hashes))
                        if hashes[index] != hashes[left]
                    ),
                    None,
                )
                if first_change is None:
                    break
                right = next(
                    (
                        index
                        for index in range(first_change + 1, len(hashes))
                        if hashes[index] == hashes[left]
                    ),
                    None,
                )
                if right is not None:
                    return oracle, query, left, right
    raise AssertionError("test trajectory has no pair A-B-A episode")
