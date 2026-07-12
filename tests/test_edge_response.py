from random import Random

import pytest

from uboot.edge_response import (
    EdgeResponseEngine,
    SelectionPolicy,
    distinct_random_network,
    policy_profile,
)


def test_distinct_targets_and_fair_slot_schedule_are_preserved() -> None:
    rng = Random(10)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng), policy_profile("M0"), rng
    )

    engine.run(5)

    assert all(len(set(row)) == 3 for row in engine.targets)
    assert {count for row in engine.active_attempts for count in row} == {5}


def test_mutual_and_same_slot_consensus_are_separate_statistics() -> None:
    rng = Random(11)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng), policy_profile("M0"), rng
    )
    engine.targets[0] = [1, 2, 3]
    engine.targets[1] = [4, 0, 5]
    engine.incoming = [[set() for _ in range(20)] for _ in range(3)]
    for source, row in enumerate(engine.targets):
        for slot, target in enumerate(row):
            engine.incoming[slot][target].add(source)

    summary = engine.summary()

    assert summary["mutual_connection_count"] >= 1
    assert not any(engine.is_consistent(0, 1, slot) for slot in range(3))


def test_same_return_profile_can_generate_response_events() -> None:
    rng = Random(12)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng), policy_profile("M1"), rng
    )

    engine.run(100)

    assert engine.counters["response_events"] > 0
    assert all(len(set(row)) == 3 for row in engine.targets)


def test_reserved_candidate_weight_modes_fail_explicitly() -> None:
    with pytest.raises(NotImplementedError):
        SelectionPolicy(candidate_weight_mode="incoming_count")


def test_progress_reports_final_active_and_response_state() -> None:
    rng = Random(13)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng), policy_profile("M1"), rng
    )
    reports: list[tuple[int, int, int, int]] = []

    engine.run(2, lambda *values: reports.append(values), progress_check_every=17)

    assert reports[-1][0:2] == (120, 120)
    assert reports[-1][2] == engine.counters["response_events"]
    assert reports[-1][3] == 0
