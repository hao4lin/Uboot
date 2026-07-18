"""Exact checkpointed replay for the stateless random-replacement baseline."""

from __future__ import annotations

from array import array
import base64
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import pickle
import platform
from random import Random
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Literal

from uboot.kernel import SLOT_COUNT, RawNetwork
from uboot.minimal_edge_experiment import (
    initial_network,
    network_hash,
    state_hash,
)

if TYPE_CHECKING:
    from uboot.adaptive_relation_tracer import CommitTransition


CHECKPOINT_FORMAT_VERSION = "deterministic-replay-checkpoint-v1"
IMPACT_INDEX_MODES = ("none", "tracked-members", "all-commits")


@dataclass(frozen=True, slots=True)
class RunFingerprint:
    code_version: str
    dynamics_policy: str
    size: int
    seed: int
    initialization_parameters: dict[str, Any]
    runtime_parameters: dict[str, Any]
    main_rng_initial_state_hash: str
    initial_network_state_hash: str
    slot_semantics_version: str
    commit_semantics_version: str
    git_commit_hash: str
    python_version: str
    serialization_format_version: str

    @property
    def fingerprint_hash(self) -> str:
        return _json_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class ReplayState:
    network: RawNetwork
    rng_state: object
    atom_commit_locator: int
    completed_sweep_locator: int
    sweep_atom_offset: int
    dynamics_internal_state: tuple[tuple[str, Any], ...]
    state_hash: str

    @property
    def network_hash(self) -> str:
        return network_hash(self.network)

    @property
    def rng_state_hash(self) -> str:
        return state_hash(self.rng_state)


@dataclass(frozen=True, slots=True)
class UpdateImpactRecord:
    commit_locator: int
    source_raw_id: int
    slot_semantic: int
    old_target: int
    new_target: int


@dataclass(frozen=True, slots=True)
class RelevantUpdateSummary:
    interval_left_locator: int
    interval_right_locator: int
    relevant_update_count: int
    source_raw_ids: tuple[int, ...]
    old_target_raw_ids: tuple[int, ...]
    new_target_raw_ids: tuple[int, ...]
    slot_semantics: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CommitDetails:
    commit_locator: int
    source_raw_id: int
    slot_semantic: int
    old_target: int
    candidate_target: int
    actual_new_target: int
    directly_modified_slot: str
    indirectly_affected_raw_ids: tuple[int, ...]
    network_hash_before: str
    network_hash_after: str
    rng_hash_before: str
    rng_hash_after: str


class ReplayBaselineEngine:
    """Minimal exact baseline state; it intentionally has no passive statistics."""

    def __init__(self, network: RawNetwork, rng: Random, tick: int = 0) -> None:
        self.targets = [list(row) for row in network.targets]
        self.rng = rng
        self.tick = tick

    @property
    def size(self) -> int:
        return len(self.targets)

    def legal_candidates(self, source: int, slot: int) -> tuple[int, ...]:
        blocked = {
            source,
            *(
                target
                for index, target in enumerate(self.targets[source])
                if index != slot
            ),
        }
        return tuple(raw_id for raw_id in range(self.size) if raw_id not in blocked)

    def step(self) -> None:
        self.step_observed()

    def step_observed(self) -> UpdateImpactRecord:
        source = self.rng.randrange(self.size)
        slot = self.rng.randrange(SLOT_COUNT)
        old_target = self.targets[source][slot]
        target = self.rng.choice(self.legal_candidates(source, slot))
        self.targets[source][slot] = target
        self.tick += 1
        return UpdateImpactRecord(self.tick, source, slot, old_target, target)

    def snapshot(self) -> RawNetwork:
        return RawNetwork(tuple(tuple(row) for row in self.targets))  # type: ignore[arg-type]


class ImpactIndex:
    """Packed update locators; it stores no complete network or relation slice."""

    def __init__(
        self,
        mode: Literal["none", "tracked-members", "all-commits"],
        tracked_members: Iterable[int] = (),
    ) -> None:
        if mode not in IMPACT_INDEX_MODES:
            raise ValueError(f"unknown impact-index mode: {mode}")
        self.mode = mode
        self.tracked_members = frozenset(tracked_members)
        self.commit_locator = array("I")
        self.source_raw_id = array("I")
        self.slot_semantic = array("B")
        self.old_target = array("I")
        self.new_target = array("I")

    def append(self, record: UpdateImpactRecord) -> None:
        if self.mode == "none":
            return
        if self.mode == "tracked-members" and {
            record.source_raw_id,
            record.old_target,
            record.new_target,
        }.isdisjoint(self.tracked_members):
            return
        self.commit_locator.append(record.commit_locator)
        self.source_raw_id.append(record.source_raw_id)
        self.slot_semantic.append(record.slot_semantic)
        self.old_target.append(record.old_target)
        self.new_target.append(record.new_target)

    def __len__(self) -> int:
        return len(self.commit_locator)

    def records(
        self,
        left_locator: int = 0,
        right_locator: int | None = None,
    ) -> Iterator[UpdateImpactRecord]:
        right = right_locator if right_locator is not None else 2**63 - 1
        for values in zip(
            self.commit_locator,
            self.source_raw_id,
            self.slot_semantic,
            self.old_target,
            self.new_target,
        ):
            locator, source, slot, old, new = values
            if locator <= left_locator:
                continue
            if locator > right:
                break
            yield UpdateImpactRecord(locator, source, slot, old, new)

    def relevant_records(
        self,
        left_locator: int,
        right_locator: int,
        raw_ids: Iterable[int],
    ) -> tuple[UpdateImpactRecord, ...]:
        members = frozenset(raw_ids)
        return tuple(
            record
            for record in self.records(left_locator, right_locator)
            if record.source_raw_id in members
            or record.old_target in members
            or record.new_target in members
        )

    def summarize(
        self,
        left_locator: int,
        right_locator: int,
        raw_ids: Iterable[int],
    ) -> RelevantUpdateSummary:
        records = self.relevant_records(left_locator, right_locator, raw_ids)
        return RelevantUpdateSummary(
            left_locator,
            right_locator,
            len(records),
            tuple(sorted({record.source_raw_id for record in records})),
            tuple(sorted({record.old_target for record in records})),
            tuple(sorted({record.new_target for record in records})),
            tuple(sorted({record.slot_semantic for record in records})),
        )


class ReplayOracle:
    """Restore exact baseline state and generate passive commit observations."""

    def __init__(
        self,
        fingerprint: RunFingerprint,
        checkpoints: dict[int, ReplayState],
        impact_index: ImpactIndex,
    ) -> None:
        if not checkpoints or 0 not in checkpoints:
            raise ValueError("replay oracle requires a locator-zero checkpoint")
        self.fingerprint = fingerprint
        self.checkpoints = dict(sorted(checkpoints.items()))
        self.impact_index = impact_index
        self.replayed_atom_count = 0

    def restore_nearest_checkpoint(self, target_locator: int) -> ReplayState:
        eligible = [locator for locator in self.checkpoints if locator <= target_locator]
        if not eligible:
            raise ValueError(f"no checkpoint at or before locator {target_locator}")
        return self.checkpoints[max(eligible)]

    def replay_to(
        self,
        target_locator: int,
        *,
        checkpoint_locator: int | None = None,
    ) -> ReplayState:
        checkpoint = (
            self.restore_nearest_checkpoint(target_locator)
            if checkpoint_locator is None
            else self.checkpoints[checkpoint_locator]
        )
        if checkpoint.atom_commit_locator > target_locator:
            raise ValueError("checkpoint is after the target locator")
        engine = engine_from_state(checkpoint)
        while engine.tick < target_locator:
            engine.step()
        self.replayed_atom_count += target_locator - checkpoint.atom_commit_locator
        return capture_replay_state(engine)

    def inspect_relation_slice(self, locator: int, query: object) -> object:
        from uboot.adaptive_relation_tracer import generate_slice

        return generate_slice(self.replay_to(locator).network, query)

    def inspect_before_after_commit(
        self, commit_locator: int, query: object
    ) -> "CommitTransition":
        return self.inspect_commit_sequence((commit_locator,), query)[0]

    def inspect_commit_sequence(
        self, commit_locators: Iterable[int], query: object
    ) -> tuple["CommitTransition", ...]:
        """Inspect many commits with one forward replay per checkpoint segment."""
        from uboot.adaptive_relation_tracer import CommitTransition, generate_slice

        locators = sorted(set(commit_locators))
        if any(locator < 1 for locator in locators):
            raise ValueError("commit locators are one-based")
        grouped: dict[int, list[int]] = {}
        for locator in locators:
            checkpoint = self.restore_nearest_checkpoint(locator - 1)
            grouped.setdefault(checkpoint.atom_commit_locator, []).append(locator)
        transitions = []
        for checkpoint_locator, segment_locators in sorted(grouped.items()):
            engine = engine_from_state(self.checkpoints[checkpoint_locator])
            for commit_locator in segment_locators:
                while engine.tick < commit_locator - 1:
                    engine.step()
                    self.replayed_atom_count += 1
                before_state = capture_replay_state(engine)
                slice_before = generate_slice(before_state.network, query)
                impact = engine.step_observed()
                self.replayed_atom_count += 1
                after_state = capture_replay_state(engine)
                slice_after = generate_slice(after_state.network, query)
                details = CommitDetails(
                    commit_locator=commit_locator,
                    source_raw_id=impact.source_raw_id,
                    slot_semantic=impact.slot_semantic,
                    old_target=impact.old_target,
                    candidate_target=impact.new_target,
                    actual_new_target=impact.new_target,
                    directly_modified_slot=f"{impact.source_raw_id}:{impact.slot_semantic}",
                    indirectly_affected_raw_ids=tuple(
                        sorted(
                            {
                                impact.source_raw_id,
                                impact.old_target,
                                impact.new_target,
                            }
                        )
                    ),
                    network_hash_before=before_state.network_hash,
                    network_hash_after=after_state.network_hash,
                    rng_hash_before=before_state.rng_state_hash,
                    rng_hash_after=after_state.rng_state_hash,
                )
                transitions.append(
                    CommitTransition(
                        before_state,
                        after_state,
                        slice_before,
                        slice_after,
                        details,
                    )
                )
        transitions.sort(key=lambda item: item.details.commit_locator)
        return tuple(transitions)

    def replay_cost_for_commits(self, commit_locators: Iterable[int]) -> int:
        grouped: dict[int, list[int]] = {}
        for locator in sorted(set(commit_locators)):
            checkpoint = self.restore_nearest_checkpoint(locator - 1)
            grouped.setdefault(checkpoint.atom_commit_locator, []).append(locator)
        return sum(max(locators) - checkpoint for checkpoint, locators in grouped.items())


def build_replay_oracle(
    *,
    size: int,
    seed: int,
    max_locator: int,
    checkpoint_locators: Iterable[int],
    impact_index_mode: Literal["none", "tracked-members", "all-commits"],
    tracked_members: Iterable[int] = (),
    git_commit_hash: str = "unknown",
    checkpoint_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    progress_check_every: int = 16_384,
) -> ReplayOracle:
    rng = Random(seed)
    network = initial_network(size, rng)
    fingerprint = RunFingerprint(
        code_version="deterministic-adaptive-replay-v1",
        dynamics_policy="baseline_random_replace",
        size=size,
        seed=seed,
        initialization_parameters={
            "slot_count": SLOT_COUNT,
            "targets_distinct_per_source": True,
            "self_targets_allowed": False,
        },
        runtime_parameters={"policy_persistent_state": False},
        main_rng_initial_state_hash=state_hash(rng.getstate()),
        initial_network_state_hash=network_hash(network),
        slot_semantics_version="three-indexed-slots-v1",
        commit_semantics_version="state-after-N-one-based-commits-v1",
        git_commit_hash=git_commit_hash,
        python_version=platform.python_version(),
        serialization_format_version=CHECKPOINT_FORMAT_VERSION,
    )
    engine = ReplayBaselineEngine(network, rng)
    requested = sorted(set(checkpoint_locators) | {0, max_locator})
    if requested[0] < 0 or requested[-1] > max_locator:
        raise ValueError("checkpoint locator is outside the run")
    checkpoints = {0: capture_replay_state(engine)}
    impact_index = ImpactIndex(impact_index_mode, tracked_members)
    requested_set = set(requested)
    while engine.tick < max_locator:
        record = engine.step_observed()
        impact_index.append(record)
        if engine.tick in requested_set:
            checkpoints[engine.tick] = capture_replay_state(engine)
        if progress is not None and engine.tick % progress_check_every == 0:
            progress(engine.tick, max_locator)
    if progress is not None:
        progress(engine.tick, max_locator)
    oracle = ReplayOracle(fingerprint, checkpoints, impact_index)
    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        for locator, state in checkpoints.items():
            save_replay_checkpoint(
                checkpoint_dir / f"checkpoint_{locator:012d}.json",
                fingerprint,
                state,
            )
    return oracle


def capture_replay_state(engine: ReplayBaselineEngine) -> ReplayState:
    network = engine.snapshot()
    payload = {
        "network_targets": network.targets,
        "rng_state": _encode_pickle(engine.rng.getstate()),
        "atom_commit_locator": engine.tick,
        "dynamics_internal_state": (),
    }
    atoms_per_sweep = SLOT_COUNT * engine.size
    return ReplayState(
        network=network,
        rng_state=engine.rng.getstate(),
        atom_commit_locator=engine.tick,
        completed_sweep_locator=engine.tick // atoms_per_sweep,
        sweep_atom_offset=engine.tick % atoms_per_sweep,
        dynamics_internal_state=(),
        state_hash=_json_hash(payload),
    )


def engine_from_state(state: ReplayState) -> ReplayBaselineEngine:
    rng = Random()
    rng.setstate(state.rng_state)
    return ReplayBaselineEngine(state.network, rng, state.atom_commit_locator)


def save_replay_checkpoint(
    path: Path, fingerprint: RunFingerprint, state: ReplayState
) -> None:
    payload = {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "fingerprint": asdict(fingerprint),
        "fingerprint_hash": fingerprint.fingerprint_hash,
        "state": {
            "network_targets": state.network.targets,
            "rng_state_pickle_base64": _encode_pickle(state.rng_state),
            "atom_commit_locator": state.atom_commit_locator,
            "completed_sweep_locator": state.completed_sweep_locator,
            "sweep_atom_offset": state.sweep_atom_offset,
            "dynamics_internal_state": state.dynamics_internal_state,
            "state_hash": state.state_hash,
            "network_hash": state.network_hash,
            "rng_state_hash": state.rng_state_hash,
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_replay_checkpoint(
    path: Path, expected_fingerprint: RunFingerprint | None = None
) -> tuple[RunFingerprint, ReplayState]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["format_version"] != CHECKPOINT_FORMAT_VERSION:
        raise ValueError("unsupported replay checkpoint format")
    fingerprint = RunFingerprint(**payload["fingerprint"])
    if payload["fingerprint_hash"] != fingerprint.fingerprint_hash:
        raise ValueError("checkpoint fingerprint hash mismatch")
    if (
        expected_fingerprint is not None
        and fingerprint.fingerprint_hash != expected_fingerprint.fingerprint_hash
    ):
        raise ValueError("checkpoint belongs to a different deterministic trajectory")
    raw = payload["state"]
    network = RawNetwork(tuple(tuple(row) for row in raw["network_targets"]))  # type: ignore[arg-type]
    state = ReplayState(
        network=network,
        rng_state=_decode_pickle(raw["rng_state_pickle_base64"]),
        atom_commit_locator=raw["atom_commit_locator"],
        completed_sweep_locator=raw["completed_sweep_locator"],
        sweep_atom_offset=raw["sweep_atom_offset"],
        dynamics_internal_state=tuple(
            (key, value) for key, value in raw["dynamics_internal_state"]
        ),
        state_hash=raw["state_hash"],
    )
    if capture_replay_state(engine_from_state(state)).state_hash != state.state_hash:
        raise ValueError("checkpoint state hash mismatch")
    if state.network_hash != raw["network_hash"] or state.rng_state_hash != raw["rng_state_hash"]:
        raise ValueError("checkpoint network or RNG hash mismatch")
    return fingerprint, state


def checkpoint_manifest_rows(oracle: ReplayOracle) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "atom_commit_locator": locator,
            "completed_sweep_locator": state.completed_sweep_locator,
            "sweep_atom_offset": state.sweep_atom_offset,
            "state_hash": state.state_hash,
            "network_hash": state.network_hash,
            "rng_state_hash": state.rng_state_hash,
        }
        for locator, state in oracle.checkpoints.items()
    )


def _encode_pickle(value: object) -> str:
    return base64.b64encode(pickle.dumps(value, protocol=5)).decode("ascii")


def _decode_pickle(value: str) -> object:
    return pickle.loads(base64.b64decode(value.encode("ascii")))  # noqa: S301


def _json_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()
