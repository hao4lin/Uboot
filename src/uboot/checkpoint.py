"""Deterministic checkpoints for the frozen M2 generation baseline."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import pickle
import platform
from random import Random
import subprocess
import sys
from typing import Any

from uboot.candidate_graph import candidate_objects, strongly_connected_components
from uboot.edge_response import (
    EdgeResponseEngine,
    ExperimentPolicy,
    M2_BASELINE_GIT_TAG,
    M2_BASELINE_MODEL_NAME,
    M2_BASELINE_MODEL_VERSION,
    ResponsePolicy,
    ResponseTask,
    SelectionPolicy,
    distinct_random_network,
)
from uboot.kernel import SLOT_COUNT


class CheckpointCompatibilityError(ValueError):
    """Raised when a checkpoint is not compatible with the frozen runner."""


def dynamics_payload(engine: EdgeResponseEngine) -> dict[str, Any]:
    return {
        "targets": engine.targets,
        "workers": engine.workers,
        "queue": list(engine.queue),
        "threads": {key: asdict(value) for key, value in sorted(engine.threads.items())},
        "next_task_id": engine._next_task,  # noqa: SLF001
        "fair_ring": engine._fair_ring,  # noqa: SLF001
        "fair_index": engine._fair_index,  # noqa: SLF001
        "background_sweep": engine.background_sweep,
        "tick": engine.tick,
        "rng_state": engine.rng.getstate(),
    }


def dynamics_state_hash(engine: EdgeResponseEngine) -> str:
    return sha256(pickle.dumps(dynamics_payload(engine), protocol=5)).hexdigest()


def phase2_readiness(engine: EdgeResponseEngine) -> dict[str, Any]:
    graph = engine.candidate_graph()
    objects = [item for item in candidate_objects(graph) if len(item.members) >= 2]
    components = strongly_connected_components(graph)
    pairs = sum(len(item.members) == 2 for item in objects)
    triples = sum(len(item.members) == 3 for item in objects)
    larger = sum(len(item.members) > 3 for item in objects)
    coverage = sum(map(lambda item: len(item.members), objects)) / len(graph)
    ready = (
        max(map(len, components), default=0) <= 3
        and larger == 0
        and 2 * pairs + 3 * triples == len(graph)
        and coverage == 1.0
    )
    return {
        "largest_candidate_scc_size": max(map(len, components), default=0),
        "closed_pair_count": pairs,
        "closed_triple_count": triples,
        "closed_larger_count": larger,
        "closed_object_node_coverage": coverage,
        "phase2_ready": ready,
        "readiness_status": "phase2_ready" if ready else "not_phase2_ready",
    }


def save_checkpoint(
    engine: EdgeResponseEngine,
    path: Path,
    *,
    seed: int,
    baseline_commit: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    commit = baseline_commit or _git_commit()
    payload = {
        "dynamics": dynamics_payload(engine),
        "incoming_sets": engine.incoming,
        "policy": asdict(engine.policy),
        "counters": dict(engine.counters),
        "response_aggregates": dict(engine.response_aggregates),
        "active_attempts": engine.active_attempts,
        "completed_chain_histogram": dict(engine.completed_chain_histogram),
        "completed_lifetime_histogram": dict(engine.completed_lifetime_histogram),
    }
    dynamic_bytes = pickle.dumps(payload["dynamics"], protocol=5)
    state_bytes = pickle.dumps(payload, protocol=5)
    (path / "state.pkl").write_bytes(state_bytes)
    (path / "dynamics.pkl").write_bytes(dynamic_bytes)
    dyn_hash = sha256(dynamic_bytes).hexdigest()
    readiness = phase2_readiness(engine)
    metadata = {
        "model_name": M2_BASELINE_MODEL_NAME,
        "model_version": M2_BASELINE_MODEL_VERSION,
        "baseline_git_tag": M2_BASELINE_GIT_TAG,
        "baseline_git_commit": commit,
        "N": len(engine.targets),
        "tick": engine.tick,
        "sweep": engine.tick // (SLOT_COUNT * len(engine.targets)),
        "slot_meanings": [1, 2, 3],
        "candidate_rule": engine.candidate_rule,
        "worker_count": engine.worker_count,
        "queue_capacity": engine.response_queue_capacity,
        "queue_policy": engine.response_queue_policy,
        "seed": seed,
        "dynamics_state_hash": dyn_hash,
        "dynamics_blob": "dynamics.pkl",
        **readiness,
        **(extra_metadata or {}),
    }
    metadata_bytes = json.dumps(metadata, indent=2, sort_keys=True).encode()
    (path / "metadata.json").write_bytes(metadata_bytes)
    full_hash = sha256(metadata_bytes + state_bytes).hexdigest()
    metadata["full_checkpoint_hash"] = full_hash
    (path / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )
    (path / "state_hash.txt").write_text(
        f"dynamics_state_hash {dyn_hash}\nfull_checkpoint_hash {full_hash}\n",
        encoding="utf-8",
    )
    return metadata


def load_checkpoint(
    path: Path,
    *,
    allow_version_mismatch: bool = False,
    expected_candidate_rule: str = "endogenous_in_out2",
    expected_tag: str = M2_BASELINE_GIT_TAG,
) -> tuple[EdgeResponseEngine, dict[str, Any]]:
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    mismatches = []
    for field, actual, expected in (
        ("model_version", metadata.get("model_version"), M2_BASELINE_MODEL_VERSION),
        ("candidate_rule", metadata.get("candidate_rule"), expected_candidate_rule),
        ("baseline_git_tag", metadata.get("baseline_git_tag"), expected_tag),
    ):
        if actual != expected:
            mismatches.append(f"{field}: {actual!r} != {expected!r}")
    if mismatches and not allow_version_mismatch:
        raise CheckpointCompatibilityError("; ".join(mismatches))
    if mismatches:
        print("WARNING: loading incompatible checkpoint: " + "; ".join(mismatches), file=sys.stderr)
        metadata["version_mismatch_allowed"] = True
        metadata["version_mismatch_details"] = mismatches
    payload = pickle.loads((path / "state.pkl").read_bytes())  # noqa: S301 - trusted local artifact
    if metadata.get("dynamics_blob"):
        dynamic_bytes = (path / metadata["dynamics_blob"]).read_bytes()
        if sha256(dynamic_bytes).hexdigest() != metadata["dynamics_state_hash"]:
            raise ValueError("checkpoint dynamics hash verification failed")
        dynamic = pickle.loads(dynamic_bytes)  # noqa: S301 - trusted local artifact
    else:
        dynamic = payload["dynamics"]
    policy_data = payload["policy"]
    policy = ExperimentPolicy(
        selection=SelectionPolicy(**policy_data["selection"]),
        response=ResponsePolicy(**policy_data["response"]),
        scheduler_mode=policy_data["scheduler_mode"],
        p_process_response=policy_data["p_process_response"],
    )
    rng = Random()
    rng.setstate(dynamic["rng_state"])
    engine = EdgeResponseEngine(
        distinct_random_network(metadata["N"], Random(0)),
        policy,
        rng,
        worker_count=metadata["worker_count"],
        response_queue_capacity=metadata["queue_capacity"],
        response_queue_policy=metadata["queue_policy"],
        mode="M1",
        candidate_rule=metadata["candidate_rule"],
    )
    engine.targets = [list(row) for row in dynamic["targets"]]
    if "incoming_sets" in payload:
        engine.incoming = payload["incoming_sets"]
    elif "incoming" in dynamic:
        engine.incoming = dynamic["incoming"]
    else:
        engine.incoming = [[set() for _ in engine.targets] for _ in range(SLOT_COUNT)]
        for source, row in enumerate(engine.targets):
            for slot, target in enumerate(row):
                engine.incoming[slot][target].add(source)
    engine.workers = list(dynamic["workers"])
    engine.queue = deque(dynamic["queue"])
    engine.threads = {int(key): ResponseTask(**value) for key, value in dynamic["threads"].items()}
    engine._next_task = dynamic["next_task_id"]  # noqa: SLF001
    engine._fair_ring = [tuple(item) for item in dynamic["fair_ring"]]  # noqa: SLF001
    engine._fair_index = dynamic["fair_index"]  # noqa: SLF001
    engine.background_sweep = dynamic["background_sweep"]
    engine.tick = dynamic["tick"]
    engine.counters = Counter(payload["counters"])
    engine.response_aggregates = Counter(payload["response_aggregates"])
    engine.active_attempts = payload["active_attempts"]
    engine.completed_chain_histogram = Counter(payload["completed_chain_histogram"])
    engine.completed_lifetime_histogram = Counter(payload["completed_lifetime_histogram"])
    return engine, metadata


def dependency_snapshot() -> dict[str, str]:
    result = {"python": platform.python_version(), "platform": platform.platform()}
    for package in ("pytest", "ruff"):
        try:
            result[package] = __import__(package).__version__
        except (ImportError, AttributeError):
            result[package] = "unavailable"
    return result


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
