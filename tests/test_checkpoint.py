import json
from hashlib import sha256
from pathlib import Path
import pickle
from random import Random

import pytest

from uboot.checkpoint import (
    CheckpointCompatibilityError,
    dynamics_state_hash,
    load_checkpoint,
    phase2_readiness,
    save_checkpoint,
)
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile


def _engine(seed: int, *, stats: bool = False) -> EdgeResponseEngine:
    rng = Random(seed)
    return EdgeResponseEngine(
        distinct_random_network(20, rng), policy_profile("M2"), rng,
        mode="M1", candidate_rule="endogenous_in_out2",
        enable_worker_stats=stats, enable_response_histograms=stats,
    )


def test_checkpoint_deterministic_continuation(tmp_path: Path) -> None:
    direct = _engine(41)
    split = _engine(41)
    direct.run(12)
    split.run(10)
    save_checkpoint(split, tmp_path, seed=41)
    loaded, _ = load_checkpoint(tmp_path)
    loaded.run(12)
    assert loaded.dynamics_state() == direct.dynamics_state()
    assert dynamics_state_hash(loaded) == dynamics_state_hash(direct)


def test_checkpoint_double_load_fork_and_stats_identity(tmp_path: Path) -> None:
    plain, stats = _engine(42), _engine(42, stats=True)
    plain.run(10)
    stats.run(10)
    assert dynamics_state_hash(plain) == dynamics_state_hash(stats)
    save_checkpoint(plain, tmp_path, seed=42)
    left, _ = load_checkpoint(tmp_path)
    right, _ = load_checkpoint(tmp_path)
    left.run(12)
    right.run(12)
    assert left.dynamics_state() == right.dynamics_state()


def test_checkpoint_preserves_incoming_set_iteration_layout(tmp_path: Path) -> None:
    engine = _engine(420)
    bucket = engine.incoming[0][0]
    for value in range(100, 180):
        bucket.add(value)
    for value in range(100, 180):
        bucket.remove(value)
    before = tuple(tuple(tuple(group) for group in slot) for slot in engine.incoming)
    save_checkpoint(engine, tmp_path, seed=420)
    loaded, _ = load_checkpoint(tmp_path)
    after = tuple(tuple(tuple(group) for group in slot) for slot in loaded.incoming)
    assert after == before


def test_model_version_mismatch_rejected(tmp_path: Path) -> None:
    engine = _engine(43)
    save_checkpoint(engine, tmp_path, seed=43)
    metadata_path = tmp_path / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["model_version"] = "wrong"
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(CheckpointCompatibilityError):
        load_checkpoint(tmp_path)
    _, loaded_metadata = load_checkpoint(tmp_path, allow_version_mismatch=True)
    assert loaded_metadata["version_mismatch_allowed"] is True


def test_checkpoint_with_duplicate_targets_is_rejected(tmp_path: Path) -> None:
    engine = _engine(44)
    save_checkpoint(engine, tmp_path, seed=44)
    dynamic_path = tmp_path / "dynamics.pkl"
    dynamic = pickle.loads(dynamic_path.read_bytes())  # noqa: S301 - test fixture
    dynamic["targets"][0][1] = dynamic["targets"][0][0]
    dynamic_bytes = pickle.dumps(dynamic, protocol=5)
    dynamic_path.write_bytes(dynamic_bytes)
    metadata_path = tmp_path / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["dynamics_state_hash"] = sha256(dynamic_bytes).hexdigest()
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="three distinct objects"):
        load_checkpoint(tmp_path)


def test_pair_triple_phase2_readiness_validation() -> None:
    class Partitioned:
        targets = [[0]] * 6

        @staticmethod
        def candidate_graph() -> list[tuple[int, ...]]:
            return [(1, 2), (0, 2), (0, 1), (4, 5), (3, 5), (3, 4)]

    result = phase2_readiness(Partitioned())  # type: ignore[arg-type]
    assert result["closed_larger_count"] == 0
    assert result["closed_object_node_coverage"] == 1.0
    assert result["phase2_ready"] is True
