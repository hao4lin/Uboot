from __future__ import annotations

from random import Random

from uboot.deterministic_replay import (
    build_replay_oracle,
    load_replay_checkpoint,
)
from uboot.minimal_edge_experiment import (
    MinimalEdgeEngine,
    initial_network,
    network_hash,
)
from uboot.minimal_edge_rules import BaselineRandomReplace


def test_replay_matches_continuous_network_and_rng_at_multiple_locators() -> None:
    oracle = build_replay_oracle(
        size=12,
        seed=44,
        max_locator=240,
        checkpoint_locators=(0, 60, 120, 240),
        impact_index_mode="all-commits",
    )
    rng = Random(44)
    engine = MinimalEdgeEngine(initial_network(12, rng), rng, BaselineRandomReplace())
    for locator in (17, 60, 119, 180, 240):
        while engine.tick < locator:
            engine.step()
        replayed = oracle.replay_to(locator)
        assert replayed.network_hash == network_hash(engine.snapshot())
        assert replayed.rng_state == engine.rng.getstate()


def test_two_different_checkpoints_replay_to_the_same_target() -> None:
    oracle = build_replay_oracle(
        size=10,
        seed=8,
        max_locator=180,
        checkpoint_locators=(0, 50, 100, 180),
        impact_index_mode="all-commits",
    )
    from_zero = oracle.replay_to(175, checkpoint_locator=0)
    from_hundred = oracle.replay_to(175, checkpoint_locator=100)
    assert from_zero.state_hash == from_hundred.state_hash
    assert from_zero.rng_state == from_hundred.rng_state


def test_checkpoint_is_independently_loadable_and_fingerprint_guarded(tmp_path) -> None:
    checkpoint_dir = tmp_path / "checkpoints"
    oracle = build_replay_oracle(
        size=9,
        seed=3,
        max_locator=90,
        checkpoint_locators=(0, 45, 90),
        impact_index_mode="none",
        checkpoint_dir=checkpoint_dir,
    )
    fingerprint, state = load_replay_checkpoint(
        checkpoint_dir / "checkpoint_000000000045.json",
        oracle.fingerprint,
    )
    assert fingerprint.fingerprint_hash == oracle.fingerprint.fingerprint_hash
    assert state.state_hash == oracle.checkpoints[45].state_hash


def test_replay_observation_does_not_change_baseline_final_state() -> None:
    oracle = build_replay_oracle(
        size=15,
        seed=22,
        max_locator=300,
        checkpoint_locators=(0, 100, 300),
        impact_index_mode="all-commits",
    )
    rng = Random(22)
    engine = MinimalEdgeEngine(initial_network(15, rng), rng, BaselineRandomReplace())
    while engine.tick < 300:
        engine.step()
    final = oracle.checkpoints[300]
    assert final.network_hash == network_hash(engine.snapshot())
    assert final.rng_state == engine.rng.getstate()
