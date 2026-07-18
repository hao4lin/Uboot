"""Pure local slices and adaptive refinement over deterministic replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Callable

from uboot.deterministic_replay import (
    CommitDetails,
    ReplayOracle,
    ReplayState,
    UpdateImpactRecord,
)
from uboot.kernel import RawNetwork


SLICE_GENERATOR_VERSION = "adaptive-local-relation-slice-v1"
MOTION_CLASSIFICATIONS = (
    "NEIGHBOR_CHANGED_BODY_SUPPORT_PRESERVED",
    "BODY_AND_NEIGHBOR_CHANGED_LOCAL_SUPPORT_PRESERVED",
    "MEMBERS_SAME_RELATION_CHANGED",
    "ONE_MEMBER_ONLY",
    "NO_LOCAL_SUPPORT",
    "UNRESOLVED",
)


@dataclass(frozen=True, slots=True)
class PairSliceQuery:
    raw_a: int
    raw_b: int
    include_all_slot_relations: bool = True


@dataclass(frozen=True, slots=True)
class AnchorNeighborhoodQuery:
    anchor: int
    radius: int = 1

    def __post_init__(self) -> None:
        if self.radius != 1:
            raise ValueError("the first tracer supports anchor radius=1 only")


@dataclass(frozen=True, slots=True)
class ThreeMemberSliceQuery:
    raw_ids: tuple[int, int, int]

    def __post_init__(self) -> None:
        if len(set(self.raw_ids)) != 3:
            raise ValueError("three-member query requires three distinct raw IDs")


@dataclass(frozen=True, slots=True)
class AdaptiveSupportQuery:
    raw_a: int
    raw_b: int


SliceQuery = (
    PairSliceQuery
    | AnchorNeighborhoodQuery
    | ThreeMemberSliceQuery
    | AdaptiveSupportQuery
)


@dataclass(frozen=True, slots=True)
class GeneratedSlice:
    query_type: str
    query_hash: str
    member_ids: tuple[int, ...]
    serialization: str
    slice_hash: str
    slice_generator_version: str = SLICE_GENERATOR_VERSION

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.serialization)


@dataclass(frozen=True, slots=True)
class CommitTransition:
    before_state: ReplayState
    after_state: ReplayState
    slice_before: GeneratedSlice
    slice_after: GeneratedSlice
    details: CommitDetails


@dataclass(frozen=True, slots=True)
class TraceCandidate:
    candidate_id: str
    involved_raw_ids: tuple[int, ...]
    query_raw_ids: tuple[int, ...]
    coarse_locators: tuple[int, ...]
    query_type: str
    expected_content_difference: str
    source_artifact: str
    source_row_number: int
    slice_a_id: str
    slice_b_id: str
    selection_group: str


@dataclass(frozen=True, slots=True)
class ProbeRecord:
    candidate_id: str
    interval_left_locator: int
    interval_right_locator: int
    probe_locator: int
    query_hash: str
    slice_hash: str
    relevant_update_count: int
    decision: str


@dataclass(frozen=True, slots=True)
class LocalizedCommit:
    candidate_id: str
    last_locator_with_left_content: int
    first_locator_with_right_content: int
    responsible_commit_locator: int
    transition: CommitTransition
    content_diff: str


@dataclass(frozen=True, slots=True)
class TraceIntervalResult:
    candidate_id: str
    status: str
    probes: tuple[ProbeRecord, ...]
    localized_commits: tuple[LocalizedCommit, ...]
    relevant_update_count: int
    relevant_update_no_slice_change_count: int
    replayed_atom_count: int


@dataclass(frozen=True, slots=True)
class MotionCandidate:
    candidate_id: str
    commit_locator: int
    before_member_ids: tuple[int, ...]
    after_member_ids: tuple[int, ...]
    preserved_member_ids: tuple[int, ...]
    preserved_internal_relations: tuple[str, ...]
    changed_members: tuple[int, ...]
    changed_relations: tuple[str, ...]
    classification: str
    evidence_slice_before: str
    evidence_slice_after: str


def generate_slice(network_state: RawNetwork, query: SliceQuery) -> GeneratedSlice:
    """Generate a stable byte-identical local slice without history or RNG."""
    query_payload = _query_payload(query)
    query_hash = _hash_json(query_payload)
    if isinstance(query, PairSliceQuery):
        members = tuple(sorted((query.raw_a, query.raw_b)))
        payload = {
            "slice_generator_version": SLICE_GENERATOR_VERSION,
            "query": query_payload,
            "member_ids": members,
            "directed_relations": _internal_relations(network_state, set(members)),
            "reciprocal_relations": _reciprocal_relations(
                network_state, set(members)
            ),
        }
    elif isinstance(query, ThreeMemberSliceQuery):
        members = tuple(query.raw_ids)
        payload = {
            "slice_generator_version": SLICE_GENERATOR_VERSION,
            "query": query_payload,
            "member_ids": members,
            "directed_relations": _internal_relations(network_state, set(members)),
            "reciprocal_relations": _reciprocal_relations(
                network_state, set(members)
            ),
        }
    elif isinstance(query, AnchorNeighborhoodQuery):
        members = _anchor_members(network_state, query.anchor)
        payload = {
            "slice_generator_version": SLICE_GENERATOR_VERSION,
            "query": query_payload,
            "member_ids": members,
            "slot_relations": _neighborhood_relations(network_state, set(members)),
        }
    elif isinstance(query, AdaptiveSupportQuery):
        thirds = _support_thirds(network_state, query.raw_a, query.raw_b)
        generated = [
            generate_slice(
                network_state,
                ThreeMemberSliceQuery((query.raw_a, query.raw_b, third)),
            )
            for third in thirds
        ]
        members = tuple(sorted({query.raw_a, query.raw_b, *thirds}))
        payload = {
            "slice_generator_version": SLICE_GENERATOR_VERSION,
            "query": query_payload,
            "member_ids": members,
            "support_third_ids": thirds,
            "three_member_slice_hashes": [item.slice_hash for item in generated],
            "three_member_serializations": [item.serialization for item in generated],
        }
    else:
        raise TypeError(f"unsupported slice query: {type(query)!r}")
    serialization = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return GeneratedSlice(
        type(query).__name__,
        query_hash,
        tuple(payload["member_ids"]),
        serialization,
        sha256(serialization.encode()).hexdigest(),
    )


def trace_interval(
    oracle: ReplayOracle,
    *,
    candidate_id: str,
    left_locator: int,
    right_locator: int,
    query: SliceQuery,
    target_predicate: Callable[[GeneratedSlice], bool] | None = None,
    max_probes: int = 500,
    max_replay_atoms: int = 50_000_000,
) -> TraceIntervalResult:
    """Refine a non-monotone interval using impacts, not endpoint order."""
    if left_locator > right_locator:
        left_locator, right_locator = right_locator, left_locator
    if max_probes < 2 or max_replay_atoms < 0:
        raise ValueError("resource limits cannot inspect both interval endpoints")
    replay_start = oracle.replayed_atom_count
    left = oracle.inspect_relation_slice(left_locator, query)
    right = oracle.inspect_relation_slice(right_locator, query)
    predicate = target_predicate or (lambda item: bool(item.slice_hash))
    endpoint_decision = (
        f"ENDPOINT_CONTENT predicate={predicate(left)}/{predicate(right)} "
        f"same={left.slice_hash == right.slice_hash}"
    )
    raw_ids = set(_fixed_query_members(query)) | set(left.member_ids) | set(
        right.member_ids
    )
    update_summary = oracle.impact_index.summarize(
        left_locator, right_locator, raw_ids
    )
    relevant = oracle.impact_index.relevant_records(left_locator, right_locator, raw_ids)
    probes = [
        ProbeRecord(
            candidate_id,
            left_locator,
            right_locator,
            left_locator,
            left.query_hash,
            left.slice_hash,
            update_summary.relevant_update_count,
            endpoint_decision,
        ),
        ProbeRecord(
            candidate_id,
            left_locator,
            right_locator,
            right_locator,
            right.query_hash,
            right.slice_hash,
            update_summary.relevant_update_count,
            endpoint_decision,
        ),
    ]
    if oracle.impact_index.mode == "none" and left_locator < right_locator:
        probes.append(
            ProbeRecord(
                candidate_id,
                left_locator,
                right_locator,
                left_locator,
                left.query_hash,
                left.slice_hash,
                0,
                "INCOMPLETE_RESOURCE_LIMIT_NO_IMPACT_INDEX",
            )
        )
        return TraceIntervalResult(
            candidate_id,
            "INCOMPLETE_RESOURCE_LIMIT",
            tuple(probes),
            (),
            0,
            0,
            oracle.replayed_atom_count - replay_start,
        )
    if not relevant:
        return TraceIntervalResult(
            candidate_id,
            "CLOSED_NO_RELEVANT_UPDATE",
            tuple(probes),
            (),
            0,
            0,
            oracle.replayed_atom_count - replay_start,
        )
    possible = tuple(
        record for record in relevant if _can_change_slice(record, query, raw_ids)
    )
    if not possible:
        probes.append(
            ProbeRecord(
                candidate_id,
                left_locator,
                right_locator,
                left_locator,
                left.query_hash,
                left.slice_hash,
                len(relevant),
                "RELEVANT_UPDATE_NO_SLICE_CHANGE",
            )
        )
        return TraceIntervalResult(
            candidate_id,
            "RELEVANT_UPDATE_NO_SLICE_CHANGE",
            tuple(probes),
            (),
            len(relevant),
            len(relevant),
            oracle.replayed_atom_count - replay_start,
        )
    localized = []
    no_change_count = len(relevant) - len(possible)
    capacity = max(0, (max_probes - len(probes)) // 2)
    selected_impacts = _evenly_spaced(possible, capacity)
    incomplete = len(selected_impacts) < len(possible)
    endpoint_replay_cost = oracle.replayed_atom_count - replay_start
    while selected_impacts and (
        endpoint_replay_cost
        + oracle.replay_cost_for_commits(
            impact.commit_locator for impact in selected_impacts
        )
        > max_replay_atoms
    ):
        selected_impacts = selected_impacts[: max(0, len(selected_impacts) // 2)]
        incomplete = True
    transitions = oracle.inspect_commit_sequence(
        (impact.commit_locator for impact in selected_impacts), query
    )
    for impact, transition in zip(selected_impacts, transitions):
        changed = transition.slice_before.slice_hash != transition.slice_after.slice_hash
        decision = "LOCAL_SLICE_CHANGED" if changed else "RELEVANT_UPDATE_NO_SLICE_CHANGE"
        probes.extend(
            (
                ProbeRecord(
                    candidate_id,
                    impact.commit_locator - 1,
                    impact.commit_locator,
                    impact.commit_locator - 1,
                    transition.slice_before.query_hash,
                    transition.slice_before.slice_hash,
                    1,
                    decision,
                ),
                ProbeRecord(
                    candidate_id,
                    impact.commit_locator - 1,
                    impact.commit_locator,
                    impact.commit_locator,
                    transition.slice_after.query_hash,
                    transition.slice_after.slice_hash,
                    1,
                    decision,
                ),
            )
        )
        if changed:
            localized.append(
                LocalizedCommit(
                    candidate_id,
                    impact.commit_locator - 1,
                    impact.commit_locator,
                    impact.commit_locator,
                    transition,
                    content_diff(
                        transition.slice_before, transition.slice_after
                    ),
                )
            )
        else:
            no_change_count += 1
    if incomplete:
        status = "INCOMPLETE_RESOURCE_LIMIT"
    elif localized:
        status = "LOCALIZED_CHANGES"
    else:
        status = "RELEVANT_UPDATE_NO_SLICE_CHANGE"
    return TraceIntervalResult(
        candidate_id,
        status,
        tuple(probes),
        tuple(localized),
        len(relevant),
        no_change_count,
        oracle.replayed_atom_count - replay_start,
    )


def _evenly_spaced(
    records: tuple[UpdateImpactRecord, ...], capacity: int
) -> tuple[UpdateImpactRecord, ...]:
    if capacity <= 0:
        return ()
    if len(records) <= capacity:
        return records
    if capacity == 1:
        return (records[len(records) // 2],)
    indices = {
        round(index * (len(records) - 1) / (capacity - 1))
        for index in range(capacity)
    }
    return tuple(records[index] for index in sorted(indices))


def focus_localized_commit(
    localized: LocalizedCommit,
    *,
    max_focus_level: int,
) -> tuple[MotionCandidate, ...]:
    if max_focus_level < 1:
        return ()
    transition = localized.transition
    details = transition.details
    source, old, new = (
        details.source_raw_id,
        details.old_target,
        details.actual_new_target,
    )
    if old == new:
        return (
            _motion_row(
                localized,
                (source, old),
                (source, new),
                (),
                "MEMBERS_SAME_RELATION_CHANGED",
                transition.slice_before,
                transition.slice_after,
            ),
        )
    before_support = generate_slice(
        transition.before_state.network, AdaptiveSupportQuery(source, old)
    )
    after_support = generate_slice(
        transition.after_state.network, AdaptiveSupportQuery(source, new)
    )
    if max_focus_level == 1:
        return ()
    thirds = sorted(
        (
            set(before_support.payload["support_third_ids"])
            | set(after_support.payload["support_third_ids"])
        )
        - {source, old, new}
    )
    if not thirds:
        pair_before = generate_slice(
            transition.before_state.network, PairSliceQuery(source, old)
        )
        pair_after = generate_slice(
            transition.after_state.network, PairSliceQuery(source, new)
        )
        return (
            _motion_row(
                localized,
                (source, old),
                (source, new),
                (),
                "ONE_MEMBER_ONLY",
                pair_before,
                pair_after,
            ),
        )
    rows = []
    for third in thirds:
        before = generate_slice(
            transition.before_state.network,
            ThreeMemberSliceQuery((source, old, third)),
        )
        after = generate_slice(
            transition.after_state.network,
            ThreeMemberSliceQuery((source, new, third)),
        )
        preserved = (source, third)
        preserved_relations = _preserved_relations(before, after, set(preserved))
        classification = (
            "NEIGHBOR_CHANGED_BODY_SUPPORT_PRESERVED"
            if preserved_relations and max_focus_level >= 3
            else "NO_LOCAL_SUPPORT"
        )
        rows.append(
            _motion_row(
                localized,
                (source, old, third),
                (source, new, third),
                preserved_relations,
                classification,
                before,
                after,
            )
        )
    rows.sort(
        key=lambda item: (
            item.classification != "NEIGHBOR_CHANGED_BODY_SUPPORT_PRESERVED",
            -len(item.preserved_internal_relations),
            item.before_member_ids,
            item.after_member_ids,
        )
    )
    return tuple(rows[:1])


def content_diff(before: GeneratedSlice, after: GeneratedSlice) -> str:
    before_payload = before.payload
    after_payload = after.payload
    keys = sorted(set(before_payload) | set(after_payload))
    changes = {
        key: {"before": before_payload.get(key), "after": after_payload.get(key)}
        for key in keys
        if before_payload.get(key) != after_payload.get(key)
    }
    return json.dumps(changes, sort_keys=True, separators=(",", ":"))


def _query_payload(query: SliceQuery) -> dict[str, Any]:
    return {"query_type": type(query).__name__, **asdict(query)}


def _fixed_query_members(query: SliceQuery) -> tuple[int, ...]:
    if isinstance(query, PairSliceQuery):
        return query.raw_a, query.raw_b
    if isinstance(query, ThreeMemberSliceQuery):
        return query.raw_ids
    if isinstance(query, AnchorNeighborhoodQuery):
        return (query.anchor,)
    if isinstance(query, AdaptiveSupportQuery):
        return query.raw_a, query.raw_b
    raise TypeError(query)


def _internal_relations(
    network: RawNetwork, members: set[int]
) -> list[dict[str, int]]:
    return [
        {"source_raw_id": source, "slot_semantic": slot, "target_raw_id": target}
        for source in sorted(members)
        for slot, target in enumerate(network.targets[source])
        if target in members
    ]


def _reciprocal_relations(
    network: RawNetwork, members: set[int]
) -> list[dict[str, Any]]:
    rows = []
    for left in sorted(members):
        for right in sorted(member for member in members if member > left):
            for left_slot, target in enumerate(network.targets[left]):
                if target != right:
                    continue
                for right_slot, reverse in enumerate(network.targets[right]):
                    if reverse == left:
                        rows.append(
                            {
                                "left_raw_id": left,
                                "right_raw_id": right,
                                "left_slot_semantic": left_slot,
                                "right_slot_semantic": right_slot,
                                "relation_kind": (
                                    "reciprocal_same_slot"
                                    if left_slot == right_slot
                                    else "reciprocal_cross_slot"
                                ),
                            }
                        )
    return rows


def _anchor_members(network: RawNetwork, anchor: int) -> tuple[int, ...]:
    outgoing = set(network.targets[anchor])
    incoming = {
        source
        for source, targets in enumerate(network.targets)
        if anchor in targets
    }
    return tuple(sorted({anchor, *outgoing, *incoming}))


def _neighborhood_relations(
    network: RawNetwork, members: set[int]
) -> list[dict[str, Any]]:
    return [
        {
            "source_raw_id": source,
            "slot_semantic": slot,
            "target_raw_id": target,
            "target_scope": "LOCAL" if target in members else "OUTSIDE",
        }
        for source in sorted(members)
        for slot, target in enumerate(network.targets[source])
    ]


def _support_thirds(network: RawNetwork, raw_a: int, raw_b: int) -> tuple[int, ...]:
    excluded = {raw_a, raw_b}
    candidates = set(network.targets[raw_a]) | set(network.targets[raw_b])
    candidates.update(
        source
        for source, targets in enumerate(network.targets)
        if raw_a in targets or raw_b in targets
    )
    return tuple(sorted(candidates - excluded))


def _can_change_slice(
    record: UpdateImpactRecord,
    query: SliceQuery,
    current_members: set[int],
) -> bool:
    if isinstance(query, (PairSliceQuery, ThreeMemberSliceQuery)):
        return record.source_raw_id in current_members and (
            record.old_target in current_members or record.new_target in current_members
        )
    return (
        record.source_raw_id in current_members
        or record.old_target in current_members
        or record.new_target in current_members
    )


def _preserved_relations(
    before: GeneratedSlice, after: GeneratedSlice, preserved_members: set[int]
) -> tuple[str, ...]:
    before_relations = {
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in before.payload.get("directed_relations", ())
        if row["source_raw_id"] in preserved_members
        and row["target_raw_id"] in preserved_members
    }
    after_relations = {
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in after.payload.get("directed_relations", ())
        if row["source_raw_id"] in preserved_members
        and row["target_raw_id"] in preserved_members
    }
    return tuple(sorted(before_relations & after_relations))


def _motion_row(
    localized: LocalizedCommit,
    before_members: tuple[int, ...],
    after_members: tuple[int, ...],
    preserved_relations: tuple[str, ...],
    classification: str,
    before: GeneratedSlice,
    after: GeneratedSlice,
) -> MotionCandidate:
    preserved = tuple(sorted(set(before_members) & set(after_members)))
    changed_members = tuple(sorted(set(before_members) ^ set(after_members)))
    before_relations = {
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in before.payload.get("directed_relations", ())
    }
    after_relations = {
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in after.payload.get("directed_relations", ())
    }
    return MotionCandidate(
        localized.candidate_id,
        localized.responsible_commit_locator,
        before_members,
        after_members,
        preserved,
        preserved_relations,
        changed_members,
        tuple(sorted(before_relations ^ after_relations)),
        classification,
        before.serialization,
        after.serialization,
    )


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()
