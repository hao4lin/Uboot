from pathlib import Path
from random import Random

from uboot.checkpoint import dynamics_state_hash, load_checkpoint, save_checkpoint
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.incremental import (
    choose_root_checkpoint,
    finalize_run_index,
    history_tail,
    run_incremental_chunk,
)


def _engine(seed: int) -> EdgeResponseEngine:
    rng = Random(seed)
    return EdgeResponseEngine(
        distinct_random_network(20, rng),
        policy_profile("M2"),
        rng,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )


def test_incremental_chunk_preserves_dynamics_and_writes_resume_checkpoint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    initial = _engine(7)
    initial.run(2)
    save_checkpoint(initial, source, seed=7, baseline_commit="test")
    observed, metadata = load_checkpoint(source)
    expected, _ = load_checkpoint(source)
    expected.run(5)

    manifest = run_incremental_chunk(
        observed,
        metadata,
        chunk_sweeps=3,
        snapshot_interval_sweeps=2,
        chunk_dir=tmp_path / "chunk",
        checkpoint_dir=tmp_path / "next",
        seed=7,
    )
    resumed, resumed_metadata = load_checkpoint(tmp_path / "next")

    assert dynamics_state_hash(observed) == dynamics_state_hash(expected)
    assert dynamics_state_hash(resumed) == dynamics_state_hash(expected)
    assert manifest["snapshot_count"] == 3
    assert resumed_metadata["parent_checkpoint_hash"] == metadata["full_checkpoint_hash"]
    assert (tmp_path / "chunk" / "system_snapshots.csv").is_file()
    assert (tmp_path / "chunk" / "closed_object_snapshots.csv").is_file()


def test_history_replays_from_fixed_root_before_next_chunk(tmp_path: Path) -> None:
    root = tmp_path / "root"
    run_dir = tmp_path / "run"
    engine = _engine(8)
    engine.run(2)
    save_checkpoint(engine, root, seed=8, baseline_commit="test")
    fixed = choose_root_checkpoint(run_dir, root)

    first, metadata = load_checkpoint(fixed)
    manifest = run_incremental_chunk(
        first,
        metadata,
        chunk_sweeps=2,
        snapshot_interval_sweeps=1,
        chunk_dir=run_dir / "chunks" / "first",
        checkpoint_dir=run_dir / "checkpoints" / "first",
        seed=8,
    )
    manifest.update(replay_sweeps=0, replay_runtime_seconds=0.0)
    finalize_run_index(run_dir, manifest)

    replay, _ = load_checkpoint(choose_root_checkpoint(run_dir, None))
    tail = history_tail(run_dir)
    assert tail is not None
    replay.run(int(tail["end_sweep"]))

    assert dynamics_state_hash(replay) == tail["final_dynamics_state_hash"]
