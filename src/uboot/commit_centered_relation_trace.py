"""Commit-centered relation support diagnostics over deterministic replay.

Locators are recording positions used to restore exact states.  This module does
not interpret locator distance as object time, motion, speed, or causality.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from typing import Any, Iterable, Sequence

from uboot.adaptive_relation_tracer import (
    AnchorNeighborhoodQuery,
    PairSliceQuery,
    ThreeMemberSliceQuery,
    TraceCandidate,
    generate_slice,
)
from uboot.deterministic_replay import ReplayOracle, UpdateImpactRecord
from uboot.kernel import RawNetwork


SUPPORT_STRENGTH = {
    "NONE": 0,
    "TRIVIAL": 1,
    "WEAK_NONTRIVIAL": 2,
    "STRONG_RAW_SUPPORT": 3,
    "STRONG_CANONICAL_SUPPORT": 4,
}


@dataclass(frozen=True, slots=True)
class SupportAlignment:
    classification: str
    support_strength: int
    preserved_raw_members: tuple[int, ...] = ()
    replaced_raw_members: tuple[int, ...] = ()
    preserved_relations: tuple[str, ...] = ()
    transferred_relations: tuple[str, ...] = ()
    canonical_role_mapping: str = ""
    trivial_sibling_relation_count: int = 0
    independent_support_relation_count: int = 0
    independent_support_source_count: int = 0


@dataclass(frozen=True, slots=True)
class PrimaryChange:
    candidate_id: str
    commit_locator: int
    source_raw_id: int
    slot_semantic: int
    old_target: int
    new_target: int
    primary_change_type: str
    source_artifact: str


@dataclass(frozen=True, slots=True)
class CommitCenteredCase:
    primary: PrimaryChange
    alignment: SupportAlignment
    bundle: dict[str, Any]
    support_candidates: tuple[dict[str, Any], ...]
    support_changes: tuple[dict[str, Any], ...]
    local_window_used: int
    support_boundary_complete: bool
    resource_status: str
    replayed_atom_count: int
    probe_count: int


@dataclass(frozen=True, slots=True)
class CommitCenteredTraceResult:
    cases: tuple[CommitCenteredCase, ...]
    candidate_resource_status: tuple[tuple[str, str], ...]


def is_nontrivial_support(
    before: RawNetwork,
    after: RawNetwork,
    commit: Any,
    alignment: SupportAlignment | None = None,
) -> bool:
    """Return whether evidence exceeds mechanically preserved source siblings."""
    assessed = alignment or classify_nontrivial_support(before, after, commit)
    return assessed.support_strength >= SUPPORT_STRENGTH["WEAK_NONTRIVIAL"]


def classify_nontrivial_support(
    before: RawNetwork,
    after: RawNetwork,
    commit: Any,
) -> SupportAlignment:
    """Classify immediate before/after support for one real slot commit."""
    source = int(commit.source_raw_id)
    slot = int(commit.slot_semantic)
    old = int(commit.old_target)
    new_value = (
        commit.actual_new_target
        if hasattr(commit, "actual_new_target")
        else commit.new_target
    )
    new = int(new_value)
    actors = {source, old, new}
    before_edges = _edges(before)
    after_edges = _edges(after)
    preserved = before_edges & after_edges

    sibling = {
        edge
        for edge in preserved
        if edge[0] == source and edge[1] != slot and edge[2] not in {old, new}
    }
    independent = {
        edge
        for edge in preserved
        if edge not in sibling
        and edge != (source, slot, old)
        and edge != (source, slot, new)
        and edge[2] in actors
        and edge[0] != source
    }
    independent_sources = {edge[0] for edge in independent}

    raw_transfers = _raw_role_transfers(before, after, source, old, new)
    internal_preserved = {
        edge for edge in preserved if edge[0] in actors and edge[2] in actors
    }
    nontrivial_internal = {
        edge
        for edge in internal_preserved
        if not (edge[0] == source and edge[1] != slot)
    }
    canonical = _canonical_alignment(before, after, source, old, new)

    base = {
        "preserved_raw_members": tuple(sorted(actors)),
        "preserved_relations": tuple(sorted(map(_edge_json, independent))),
        "trivial_sibling_relation_count": len(sibling),
        "independent_support_relation_count": len(independent),
        "independent_support_source_count": len(independent_sources),
    }
    if canonical is not None:
        mapping, relations, replaced = canonical
        return SupportAlignment(
            "CANONICAL_LOCAL_ROLE_STRUCTURE_PRESERVED",
            SUPPORT_STRENGTH["STRONG_CANONICAL_SUPPORT"],
            replaced_raw_members=replaced,
            transferred_relations=relations,
            canonical_role_mapping=mapping,
            **base,
        )
    if len(independent_sources) >= 2:
        return SupportAlignment(
            "MULTI_SOURCE_SUPPORT_PRESERVED",
            SUPPORT_STRENGTH["STRONG_RAW_SUPPORT"],
            **base,
        )
    if len(nontrivial_internal) >= 2:
        return SupportAlignment(
            "THREE_MEMBER_MULTI_EDGE_SUPPORT_PRESERVED",
            SUPPORT_STRENGTH["STRONG_RAW_SUPPORT"],
            **base,
        )
    if raw_transfers:
        kinds = {kind for kind, _, _ in raw_transfers}
        if "NEIGHBOR_ROLE_TRANSFER_VIA_THIRD_MEMBER" in kinds:
            classification = "NEIGHBOR_ROLE_TRANSFER_VIA_THIRD_MEMBER"
        else:
            classification = "EXTERNAL_SUPPORT_TRANSFER_TO_NEW_NEIGHBOR"
        return SupportAlignment(
            classification,
            SUPPORT_STRENGTH["STRONG_RAW_SUPPORT"],
            transferred_relations=tuple(
                sorted(f"{_edge_json(left)}=>{_edge_json(right)}")
                for _, left, right in raw_transfers
            ),
            **base,
        )
    incoming = {edge for edge in independent if edge[2] == source}
    if incoming or independent:
        return SupportAlignment(
            "INDEPENDENT_INCOMING_SUPPORT_PRESERVED",
            SUPPORT_STRENGTH["WEAK_NONTRIVIAL"],
            **base,
        )
    if sibling:
        return SupportAlignment(
            "TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION",
            SUPPORT_STRENGTH["TRIVIAL"],
            preserved_relations=tuple(sorted(map(_edge_json, sibling))),
            trivial_sibling_relation_count=len(sibling),
            preserved_raw_members=(source,),
        )
    return SupportAlignment("ONE_MEMBER_ONLY", SUPPORT_STRENGTH["NONE"], (source,))


def locate_primary_changes(
    oracle: ReplayOracle,
    candidate: TraceCandidate,
    *,
    max_commits: int,
) -> tuple[tuple[PrimaryChange, ...], bool]:
    """Use the impact index to find relation commits without slice probing."""
    if oracle.impact_index.mode == "none":
        return (), True
    left, right = min(candidate.coarse_locators), max(candidate.coarse_locators)
    records = tuple(
        record
        for record in oracle.impact_index.records(left, right)
        if record.old_target != record.new_target
        and _is_primary_record(record, candidate)
    )
    selected = _evenly_spaced(records, max_commits)
    rows = tuple(
        PrimaryChange(
            candidate.candidate_id,
            record.commit_locator,
            record.source_raw_id,
            record.slot_semantic,
            record.old_target,
            record.new_target,
            _primary_change_type(record, candidate),
            candidate.source_artifact,
        )
        for record in selected
    )
    return rows, len(selected) < len(records)


def trace_commit_centered(
    oracle: ReplayOracle,
    candidates: Sequence[TraceCandidate],
    *,
    max_primary_commits_per_candidate: int = 5,
    local_windows: tuple[int, ...] = (8, 32, 128),
    max_support_candidates_per_commit: int = 32,
    max_support_boundary_probes: int = 128,
    max_total_replay_atoms: int = 50_000_000,
    progress: Any | None = None,
) -> CommitCenteredTraceResult:
    if not local_windows or tuple(sorted(set(local_windows))) != local_windows:
        raise ValueError("local windows must be unique and increasing")
    cases: list[CommitCenteredCase] = []
    plans: list[tuple[TraceCandidate, tuple[PrimaryChange, ...], bool]] = []
    candidate_status: list[tuple[str, str]] = []
    replay_start = oracle.replayed_atom_count
    for candidate in candidates:
        primaries, primary_incomplete = locate_primary_changes(
            oracle, candidate, max_commits=max_primary_commits_per_candidate
        )
        status = (
            "INCOMPLETE_RESOURCE_LIMIT_PRIMARY_COMMITS"
            if primary_incomplete
            else "COMPLETE"
        )
        plans.append((candidate, primaries, primary_incomplete))
        candidate_status.append((candidate.candidate_id, status))

    all_primaries = tuple(
        primary for _, primaries, _ in plans for primary in primaries
    )
    projected = oracle.replay_cost_for_commits(
        primary.commit_locator for primary in all_primaries
    )
    if oracle.replayed_atom_count - replay_start + projected > max_total_replay_atoms:
        limited = tuple(
            (candidate.candidate_id, "INCOMPLETE_RESOURCE_LIMIT_TOTAL_REPLAY")
            for candidate in candidates
        )
        return CommitCenteredTraceResult((), limited)
    batch_start = oracle.replayed_atom_count
    transitions = oracle.inspect_commit_sequence(
        (primary.commit_locator for primary in all_primaries),
        PairSliceQuery(0, 1),
    )
    transitions_by_locator = {
        transition.details.commit_locator: transition for transition in transitions
    }
    batch_cost = oracle.replayed_atom_count - batch_start
    base_cost, extra_cost = divmod(batch_cost, max(1, len(all_primaries)))
    global_case_index = 0

    for index, (candidate, primaries, primary_incomplete) in enumerate(
        plans, start=1
    ):
        for primary in primaries:
            transition = transitions_by_locator[primary.commit_locator]
            support_rows, support_incomplete = _support_candidate_rows(
                transition.before_state.network,
                transition.after_state.network,
                primary,
                max_support_candidates_per_commit,
            )
            alignment = classify_nontrivial_support(
                transition.before_state.network,
                transition.after_state.network,
                transition.details,
            )
            window, changes, boundary_incomplete = _support_boundaries(
                oracle,
                primary,
                support_rows,
                local_windows,
                max_support_boundary_probes,
            )
            resource_parts = []
            if primary_incomplete:
                resource_parts.append("PRIMARY_COMMITS")
            if support_incomplete:
                resource_parts.append("SUPPORT_CANDIDATES")
            if boundary_incomplete:
                resource_parts.append("BOUNDARY_REFINEMENT")
            resource_status = (
                "INCOMPLETE_RESOURCE_LIMIT_" + "+".join(resource_parts)
                if resource_parts
                else "COMPLETE"
            )
            bundle = _commit_bundle(
                transition.before_state.network,
                transition.after_state.network,
                primary,
                support_rows,
                changes,
            )
            cases.append(
                CommitCenteredCase(
                    primary,
                    alignment,
                    bundle,
                    support_rows,
                    changes,
                    window,
                    not boundary_incomplete,
                    resource_status,
                    base_cost + (global_case_index < extra_cost),
                    1 + len(changes),
                )
            )
            global_case_index += 1
        if progress is not None:
            progress(
                index,
                len(candidates),
                candidate.candidate_id,
                candidate_status[index - 1][1],
            )
    return CommitCenteredTraceResult(tuple(cases), tuple(candidate_status))


def motion_v2_row(case: CommitCenteredCase) -> dict[str, Any]:
    primary = case.primary
    alignment = case.alignment
    return {
        "candidate_id": primary.candidate_id,
        "primary_commit_locator": primary.commit_locator,
        "primary_change_type": primary.primary_change_type,
        "source_raw_id": primary.source_raw_id,
        "old_neighbor": primary.old_target,
        "new_neighbor": primary.new_target,
        "support_strength": alignment.support_strength,
        "support_classification": alignment.classification,
        "preserved_raw_members": _join(alignment.preserved_raw_members),
        "replaced_raw_members": _join(alignment.replaced_raw_members),
        "preserved_relations": _join(alignment.preserved_relations),
        "transferred_relations": _join(alignment.transferred_relations),
        "canonical_role_mapping": alignment.canonical_role_mapping,
        "trivial_sibling_relation_count": alignment.trivial_sibling_relation_count,
        "independent_support_relation_count": alignment.independent_support_relation_count,
        "independent_support_source_count": alignment.independent_support_source_count,
        "local_window_used": case.local_window_used,
        "support_boundary_complete": case.support_boundary_complete,
        "resource_status": case.resource_status,
        "replayed_atom_count": case.replayed_atom_count,
        "probe_count": case.probe_count,
    }


def report_v2(
    result: CommitCenteredTraceResult,
    *,
    verification: dict[str, Any],
    old_reclassification_counts: Counter[str],
    runtime_seconds: float,
) -> str:
    rows = [motion_v2_row(case) for case in result.cases]
    classes = Counter(row["support_classification"] for row in rows)
    strengths = Counter(row["support_strength"] for row in rows)
    incomplete = sum(row["resource_status"] != "COMPLETE" for row in rows)
    mean_replay = sum(row["replayed_atom_count"] for row in rows) / max(1, len(rows))
    mean_probes = sum(row["probe_count"] for row in rows) / max(1, len(rows))
    nontrivial = sum(row["support_strength"] >= 2 for row in rows)
    lines = [
        "# Commit-centered adaptive relation trace v2",
        "",
        "Locators are deterministic recording positions only; locator distance is a search diagnostic, not object time or speed.",
        "",
        f"- Baseline final network hash match: {verification['baseline_final_network_hash_match']}",
        f"- Baseline final RNG match: {verification['baseline_final_rng_state_match']}",
        f"- Primary change commits analyzed: {len(rows)}",
        f"- Trivial source-sibling preservation: {classes['TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION']}",
        f"- Remaining after trivial-only exclusion: {nontrivial}",
        f"- Independent incoming support: {classes['INDEPENDENT_INCOMING_SUPPORT_PRESERVED']}",
        f"- Neighbor role transfer: {classes['NEIGHBOR_ROLE_TRANSFER_VIA_THIRD_MEMBER']}",
        f"- Multi-source support: {classes['MULTI_SOURCE_SUPPORT_PRESERVED']}",
        f"- Three-member multi-edge support: {classes['THREE_MEMBER_MULTI_EDGE_SUPPORT_PRESERVED']}",
        f"- Canonical role structure support: {classes['CANONICAL_LOCAL_ROLE_STRUCTURE_PRESERVED']}",
        f"- ONE_MEMBER_ONLY: {classes['ONE_MEMBER_ONLY']}",
        f"- NO_LOCAL_SUPPORT: {classes['NO_LOCAL_SUPPORT']}",
        f"- Strength counts (0 none, 1 mechanical, 2 weak, 3 raw, 4 canonical): {dict(sorted(strengths.items()))}",
        f"- Mean replay atoms/case: {mean_replay:.2f}",
        f"- Mean probes/case: {mean_probes:.2f}",
        f"- Resource-limited cases: {incomplete}",
        f"- Runtime seconds: {runtime_seconds:.2f}",
        f"- Old motion reclassification: {dict(old_reclassification_counts)}",
    ]
    return "\n".join(lines) + "\n"


def _is_primary_record(record: UpdateImpactRecord, candidate: TraceCandidate) -> bool:
    query = set(candidate.query_raw_ids)
    if candidate.query_type == "pair" and len(query) == 2:
        left, right = sorted(query)
        return (
            record.source_raw_id == left
            and (record.old_target == right or record.new_target == right)
        ) or (
            record.source_raw_id == right
            and (record.old_target == left or record.new_target == left)
        )
    anchor = candidate.query_raw_ids[0]
    return (
        record.source_raw_id == anchor
        or record.old_target == anchor
        or record.new_target == anchor
    )


def _primary_change_type(
    record: UpdateImpactRecord, candidate: TraceCandidate
) -> str:
    if candidate.query_type == "anchor_neighborhood" and record.source_raw_id == candidate.query_raw_ids[0]:
        return "ANCHOR_NEIGHBOR_REPLACED"
    if record.old_target in candidate.query_raw_ids and record.new_target not in candidate.query_raw_ids:
        return "PAIR_BROKEN"
    if record.new_target in candidate.query_raw_ids and record.old_target not in candidate.query_raw_ids:
        return "PAIR_APPEARED"
    return "RELATED_SLOT_CHANGED"


def _support_candidate_rows(
    before: RawNetwork,
    after: RawNetwork,
    primary: PrimaryChange,
    limit: int,
) -> tuple[tuple[dict[str, Any], ...], bool]:
    a, b, c = primary.source_raw_id, primary.old_target, primary.new_target
    groups: dict[int, set[str]] = {}

    def add(raw_id: int, group: str) -> None:
        if raw_id not in {a, b, c}:
            groups.setdefault(raw_id, set()).add(group)

    for network in (before, after):
        for source, targets in enumerate(network.targets):
            for target in targets:
                if target == a:
                    add(source, "G1_INCOMING_TO_SOURCE")
                if target == b:
                    add(source, "G2_INCOMING_TO_OLD_NEIGHBOR")
                if target == c:
                    add(source, "G3_INCOMING_TO_NEW_NEIGHBOR")
        for target in network.targets[b]:
            add(target, "G4_OLD_NEIGHBOR_OUTGOING")
        for target in network.targets[c]:
            add(target, "G5_NEW_NEIGHBOR_OUTGOING")
        for slot, target in enumerate(network.targets[a]):
            if slot != primary.slot_semantic:
                add(target, "G7_SOURCE_SIBLING")
    for raw_id in tuple(groups):
        related = sum(
            _related(network, raw_id, actor)
            for network in (before, after)
            for actor in (a, b, c)
        )
        if related >= 2:
            groups[raw_id].add("G6_MULTI_ACTOR_RELATED")
    priority = {
        "G6_MULTI_ACTOR_RELATED": 0,
        "G1_INCOMING_TO_SOURCE": 1,
        "G2_INCOMING_TO_OLD_NEIGHBOR": 1,
        "G3_INCOMING_TO_NEW_NEIGHBOR": 1,
        "G4_OLD_NEIGHBOR_OUTGOING": 2,
        "G5_NEW_NEIGHBOR_OUTGOING": 2,
        "G7_SOURCE_SIBLING": 3,
    }
    ordered = sorted(
        groups,
        key=lambda raw_id: (
            min(priority[group] for group in groups[raw_id]), raw_id
        ),
    )
    rows = tuple(
        {
            "candidate_id": primary.candidate_id,
            "primary_commit_locator": primary.commit_locator,
            "third_raw_id": raw_id,
            "candidate_groups": _join(sorted(groups[raw_id])),
            "source_sibling_only": groups[raw_id] == {"G7_SOURCE_SIBLING"},
        }
        for raw_id in ordered[:limit]
    )
    return rows, len(ordered) > limit


def _support_boundaries(
    oracle: ReplayOracle,
    primary: PrimaryChange,
    support_rows: tuple[dict[str, Any], ...],
    windows: tuple[int, ...],
    limit: int,
) -> tuple[int, tuple[dict[str, Any], ...], bool]:
    members = {primary.source_raw_id, primary.old_target, primary.new_target}
    members.update(int(row["third_raw_id"]) for row in support_rows)
    maximum = windows[-1]
    left = max(0, primary.commit_locator - maximum)
    right = primary.commit_locator + maximum
    relevant = tuple(
        record
        for record in oracle.impact_index.records(left, right)
        if record.commit_locator != primary.commit_locator
        and record.source_raw_id in members
        and (record.old_target in members or record.new_target in members)
    )
    window = windows[0]
    for candidate_window in windows[1:]:
        if any(
            window < abs(record.commit_locator - primary.commit_locator) <= candidate_window
            for record in relevant
        ):
            window = candidate_window
        else:
            break
    inside = tuple(
        record
        for record in relevant
        if abs(record.commit_locator - primary.commit_locator) <= window
    )
    chosen = inside[:limit]
    rows = tuple(
        {
            "candidate_id": primary.candidate_id,
            "primary_commit_locator": primary.commit_locator,
            "support_commit_locator": record.commit_locator,
            "support_relation_before": _edge_json(
                (record.source_raw_id, record.slot_semantic, record.old_target)
            ),
            "support_relation_after": _edge_json(
                (record.source_raw_id, record.slot_semantic, record.new_target)
            ),
            "shared_raw_members": _join(
                sorted(
                    {record.source_raw_id, record.old_target, record.new_target}
                    & members
                )
            ),
            "role_mapping": "",
            "distance_in_locator": record.commit_locator - primary.commit_locator,
        }
        for record in chosen
    )
    return window, rows, len(inside) > limit


def _commit_bundle(
    before: RawNetwork,
    after: RawNetwork,
    primary: PrimaryChange,
    support_rows: tuple[dict[str, Any], ...],
    changes: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    a, b, c = primary.source_raw_id, primary.old_target, primary.new_target

    def serialized(network: RawNetwork, query: Any) -> dict[str, Any]:
        item = generate_slice(network, query)
        return {"slice_hash": item.slice_hash, "slice": item.payload}

    thirds = tuple(int(row["third_raw_id"]) for row in support_rows)
    return {
        "candidate_id": primary.candidate_id,
        "primary_commit_locator": primary.commit_locator,
        "primary_pair_before": serialized(before, PairSliceQuery(a, b)),
        "primary_pair_after": serialized(after, PairSliceQuery(a, c)),
        "source_neighborhood_before": serialized(before, AnchorNeighborhoodQuery(a)),
        "source_neighborhood_after": serialized(after, AnchorNeighborhoodQuery(a)),
        "old_neighbor_neighborhood_before": serialized(before, AnchorNeighborhoodQuery(b)),
        "old_neighbor_neighborhood_after": serialized(after, AnchorNeighborhoodQuery(b)),
        "new_neighbor_neighborhood_before": serialized(before, AnchorNeighborhoodQuery(c)),
        "new_neighbor_neighborhood_after": serialized(after, AnchorNeighborhoodQuery(c)),
        "candidate_three_member_slices_before": [
            serialized(before, ThreeMemberSliceQuery((a, b, third)))
            for third in thirds
        ],
        "candidate_three_member_slices_after": [
            serialized(after, ThreeMemberSliceQuery((a, c, third)))
            for third in thirds
        ],
        "support_change_boundaries": list(changes),
    }


def _raw_role_transfers(
    before: RawNetwork, after: RawNetwork, source: int, old: int, new: int
) -> tuple[tuple[str, tuple[int, int, int], tuple[int, int, int]], ...]:
    rows = []
    excluded = {source, old, new}
    for third in range(before.size):
        if third in excluded:
            continue
        for slot in range(3):
            if (
                before.targets[old][slot] == third
                and after.targets[new][slot] == third
                and before.targets[new][slot] != third
                and after.targets[old][slot] != third
            ):
                rows.append(
                    ("NEIGHBOR_ROLE_TRANSFER_VIA_THIRD_MEMBER", (old, slot, third), (new, slot, third))
                )
            if before.targets[third][slot] == old and after.targets[third][slot] == new:
                rows.append(
                    ("EXTERNAL_SUPPORT_TRANSFER_TO_NEW_NEIGHBOR", (third, slot, old), (third, slot, new))
                )
    return tuple(rows)


def _canonical_alignment(
    before: RawNetwork, after: RawNetwork, source: int, old: int, new: int
) -> tuple[str, tuple[str, ...], tuple[int, ...]] | None:
    before_thirds = _local_thirds(before, source, old) - {new}
    after_thirds = _local_thirds(after, source, new) - {old}
    matches = []
    for before_third in sorted(before_thirds):
        before_roles = _canonical_edges(before, source, old, before_third)
        for after_third in sorted(after_thirds):
            if before_third == after_third:
                continue
            shared = before_roles & _canonical_edges(after, source, new, after_third)
            shared = {edge for edge in shared if not (edge[0] == "A" and edge[2] == "N")}
            if len(shared) >= 2:
                matches.append((before_third, after_third, tuple(sorted(shared))))
    if len(matches) != 1:
        return None
    before_third, after_third, relations = matches[0]
    mapping = json.dumps(
        {"A": [source, source], "N": [old, new], "T": [before_third, after_third]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return mapping, tuple(map(str, relations)), (old, new, before_third, after_third)


def _canonical_edges(
    network: RawNetwork, source: int, neighbor: int, third: int
) -> set[tuple[str, int, str]]:
    raw_to_role = {source: "A", neighbor: "N", third: "T"}
    return {
        (raw_to_role[left], slot, raw_to_role[right])
        for left in raw_to_role
        for slot, right in enumerate(network.targets[left])
        if right in raw_to_role
    }


def _local_thirds(network: RawNetwork, left: int, right: int) -> set[int]:
    candidates = set(network.targets[left]) | set(network.targets[right])
    candidates.update(
        source
        for source, targets in enumerate(network.targets)
        if left in targets or right in targets
    )
    return candidates - {left, right}


def _related(network: RawNetwork, left: int, right: int) -> bool:
    return right in network.targets[left] or left in network.targets[right]


def _edges(network: RawNetwork) -> set[tuple[int, int, int]]:
    return {
        (source, slot, target)
        for source, targets in enumerate(network.targets)
        for slot, target in enumerate(targets)
    }


def _edge_json(edge: tuple[int, int, int]) -> str:
    source, slot, target = edge
    return json.dumps(
        {"source_raw_id": source, "slot_semantic": slot, "target_raw_id": target},
        sort_keys=True,
        separators=(",", ":"),
    )


def _evenly_spaced(
    records: Sequence[UpdateImpactRecord], capacity: int
) -> tuple[UpdateImpactRecord, ...]:
    if capacity <= 0 or not records:
        return ()
    if len(records) <= capacity:
        return tuple(records)
    if capacity == 1:
        return (records[len(records) // 2],)
    indices = {
        round(index * (len(records) - 1) / (capacity - 1))
        for index in range(capacity)
    }
    return tuple(records[index] for index in sorted(indices))


def primary_row(item: PrimaryChange) -> dict[str, Any]:
    return asdict(item) | {"commit_locator": item.commit_locator}


def _join(values: Iterable[object]) -> str:
    return "|".join(map(str, values))
