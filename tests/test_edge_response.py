from random import Random

import pytest

from uboot.edge_response import (
    EdgeResponseEngine,
    ResponseTask,
    SelectionPolicy,
    distinct_random_network,
    policy_profile,
)
from uboot.kernel import RawNetwork


def _engine(seed: int, *, stats: bool, workers: int = 1) -> EdgeResponseEngine:
    rng = Random(seed)
    return EdgeResponseEngine(
        distinct_random_network(20, rng),
        policy_profile("M1"),
        rng,
        worker_count=workers,
        enable_worker_stats=stats,
        enable_response_histograms=stats,
        mode="M1",
    )


def test_stats_invariance_includes_rng_tasks_and_queue() -> None:
    disabled = _engine(21, stats=False)
    enabled = _engine(21, stats=True)

    disabled.run(20)
    enabled.run(20)

    assert disabled.dynamics_state() == enabled.dynamics_state()
    assert disabled.summary()["all_mutual_pair_count"] == enabled.summary()[
        "all_mutual_pair_count"
    ]


def test_m0_bypasses_response_for_every_worker_count() -> None:
    results = []
    for workers in (1, 8):
        rng = Random(22)
        engine = EdgeResponseEngine(
            distinct_random_network(20, rng),
            policy_profile("M0"),
            rng,
            worker_count=workers,
            mode="M0",
        )
        engine.run(10)
        summary = engine.summary()
        assert summary.get("responses_created_total", 0) == 0
        assert summary["active_worker_count"] == 0
        assert summary["pending_response_count"] == 0
        results.append((engine.snapshot(), engine.rng.getstate()))
    assert results[0] == results[1]


def test_worker_capacity_is_real() -> None:
    maxima = []
    for workers in (1, 2):
        engine = _engine(23, stats=False, workers=workers)
        for task_id in (1, 2):
            engine._submit_task(  # noqa: SLF001 - state-machine fixture
                ResponseTask(task_id, 0, 1, 0, 0, (0,), "SAME_RETURN")
            )
        maxima.append(engine.summary()["max_concurrent_workers"])
    assert maxima == [1, 2]


def test_bounded_queue_rejects_explicitly() -> None:
    engine = _engine(230, stats=False, workers=1)
    engine.response_queue_capacity = 1
    engine._submit_task(  # noqa: SLF001
        ResponseTask(1, 0, 1, 0, 0, (0,), "SAME_RETURN")
    )
    engine._submit_task(  # noqa: SLF001
        ResponseTask(2, 0, 1, 0, 0, (0,), "SAME_RETURN")
    )
    engine._submit_task(  # noqa: SLF001
        ResponseTask(3, 0, 1, 0, 0, (0,), "SAME_RETURN")
    )

    assert engine.workers == [1]
    assert tuple(engine.queue) == (2,)
    assert engine.counters["responses_rejected_total"] == 1
    assert 3 not in engine.threads


def test_response_chain_lifetime_spans_ticks() -> None:
    network = RawNetwork(
        (
            (1, 4, 5),
            (0, 4, 5),
            (1, 6, 7),
            (2, 6, 7),
            (0, 2, 3),
            (0, 2, 3),
            (0, 3, 4),
            (0, 3, 4),
        )
    )
    rng = Random(24)
    policy = policy_profile("M1", response={"max_chain_depth": 2})
    engine = EdgeResponseEngine(network, policy, rng, mode="M1")
    engine._submit_task(  # noqa: SLF001
        ResponseTask(1, 0, 1, 0, 0, (0,), "SAME_RETURN")
    )
    engine._assign_waiting_tasks()  # noqa: SLF001

    engine.tick = 1
    engine._advance_worker(0)  # noqa: SLF001
    assert engine.workers[0] == 1
    engine.tick = 2
    engine._advance_worker(0)  # noqa: SLF001

    assert engine.workers[0] is None
    assert engine.completed_lifetime_histogram[2] == 1


def test_density_formula_uses_same_meaning_pairs() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 2, 3),
            (4, 3, 0),
            (4, 2, 5),
            (7, 5, 6),
            (7, 4, 6),
            (2, 3, 7),
            (2, 3, 5),
        )
    )
    engine = EdgeResponseEngine(network, policy_profile("M1"), Random(25), mode="M1")
    summary = engine.summary()

    assert summary["all_mutual_pair_count"] == 5
    assert summary["same_meaning_mutual_pair_count"] == 3
    assert summary["all_mutual_pair_density_per_slot"] == 5 / 24
    assert summary["same_meaning_mutual_pair_density_per_slot"] == 3 / 24


def test_reserved_candidate_weight_modes_fail_explicitly() -> None:
    with pytest.raises(NotImplementedError):
        SelectionPolicy(candidate_weight_mode="incoming_count")


def test_deterministic_replay() -> None:
    first = _engine(26, stats=True, workers=2)
    second = _engine(26, stats=True, workers=2)
    first.run(25)
    second.run(25)
    assert first.dynamics_state() == second.dynamics_state()
    assert first.summary() == second.summary()


def test_m2_stats_invariance_and_replay() -> None:
    engines = []
    for stats in (False, True):
        rng = Random(28)
        engines.append(
            EdgeResponseEngine(
                distinct_random_network(20, rng),
                policy_profile("M2"),
                rng,
                mode="M1",
                candidate_rule="endogenous_in_out2",
                enable_worker_stats=stats,
                enable_response_histograms=stats,
            )
        )
    for engine in engines:
        engine.run(20)
    assert engines[0].dynamics_state() == engines[1].dynamics_state()


def test_m2_executable_pool_preserves_old_target_and_blocks_other_slots() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 4, 5),
            (0, 4, 5),
            (0, 4, 5),
            (1, 2, 3),
            (1, 2, 3),
        )
    )
    engine = EdgeResponseEngine(
        network,
        policy_profile("M2"),
        Random(29),
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )

    assert set(engine.direct_candidates(0)) == {1, 2, 3, 4, 5}
    assert set(engine.executable_candidates(0, 0)) == {1, 4, 5}
    engine._retarget(0, 0, 1)  # noqa: SLF001 - holding is a valid transition
    with pytest.raises(ValueError, match="multiple slots"):
        engine._retarget(0, 0, 2)  # noqa: SLF001 - invariant guard


def test_m2_active_and_response_updates_preserve_distinct_targets() -> None:
    rng = Random(30)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng),
        policy_profile("M2"),
        rng,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )

    for _ in range(20_000):
        engine.step_tick()
        assert all(len(set(row)) == 3 for row in engine.targets)
