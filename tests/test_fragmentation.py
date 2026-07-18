from random import Random

from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.fragmentation import (
    first_sustained_fragmented_index,
    fragmentation_metrics,
    giant_state,
)
from uboot.kernel import RawNetwork
from uboot.snapshot_lineage import closed_object_rows


def test_giant_fraction_and_threshold_boundaries() -> None:
    network = RawNetwork(
        ((1, 2, 3), (0, 2, 3), (0, 1, 3), (0, 1, 2), (0, 1, 2))
    )
    metrics, _, _ = fragmentation_metrics(
        network, [(1,), (0,), (3,), (2,), tuple()]
    )

    assert metrics["largest_candidate_scc_size"] == 2
    assert metrics["largest_candidate_scc_fraction"] == 0.4
    assert metrics["raw_target_uniqueness_violation_count"] == 0
    assert metrics["duplicate_slot_ratio"] == 0
    assert giant_state(0.81) == "whole_or_near_whole"
    assert giant_state(0.8) == "giant"
    assert giant_state(0.5) == "intermediate"
    assert giant_state(0.2) == "fragmented"


def test_sustained_fragmentation_requires_three_consecutive_snapshots() -> None:
    assert first_sustained_fragmented_index([0.19, 0.21, 0.18, 0.17, 0.16]) == 4


def test_exact_absolute_checkpoint_runs_do_not_duplicate_final() -> None:
    rng = Random(30)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng),
        policy_profile("M2"),
        rng,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )
    ticks = []
    for sweep in (0, 3, 7):
        engine.run(sweep)
        ticks.append(engine.tick)

    assert ticks == [0, 180, 420]
    assert len(ticks) == len(set(ticks))


def test_large_object_suppresses_members_and_keeps_hash() -> None:
    network = RawNetwork(
        ((1, 2, 3), (0, 2, 3), (0, 1, 3), (0, 1, 2))
    )
    rows = closed_object_rows(
        network,
        [(1,), (2,), (3,), (0,)],
        0,
        0,
        member_inline_limit=2,
    )

    assert rows[0]["size"] == 4
    assert rows[0]["member_ids"] == ""
    assert rows[0]["member_hash"]


def test_fragmentation_statistics_do_not_change_dynamics_or_rng() -> None:
    engines = []
    for _ in range(2):
        rng = Random(31)
        engines.append(
            EdgeResponseEngine(
                distinct_random_network(20, rng),
                policy_profile("M2"),
                rng,
                mode="M1",
                candidate_rule="endogenous_in_out2",
            )
        )
    engines[0].run(5)
    fragmentation_metrics(engines[0].snapshot(), engines[0].candidate_graph())
    engines[0].run(10)
    engines[1].run(10)

    assert engines[0].dynamics_state() == engines[1].dynamics_state()
