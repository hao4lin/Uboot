"""Radius-one support graphs and relation-level motif review.

Network edges are factual commit-before/after observations.  Role transfers are
alignments between the old-neighbor and new-neighbor views; they do not imply
that the aligned support edges changed at the primary commit.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
import json
from random import Random
from typing import Any

from uboot.kernel import RawNetwork, SLOT_COUNT


V3_STRENGTH = {
    "NONE": 0,
    "TRIVIAL": 1,
    "WEAK_BACKGROUND": 2,
    "STRUCTURED_RAW_SUPPORT": 3,
    "STRUCTURED_CANONICAL_SUPPORT": 4,
}


@dataclass(frozen=True, slots=True)
class SupportEdge:
    source: int
    slot_semantic: int
    target: int
    before_present: bool
    after_present: bool
    raw_preserved: bool
    canonical_preserved: bool
    primary_change_edge: bool
    source_sibling_edge: bool


@dataclass(frozen=True, slots=True)
class SupportGraph:
    primary_source: int
    old_neighbor: int
    new_neighbor: int
    slot_semantic: int
    nodes: tuple[int, ...]
    before_edges: tuple[SupportEdge, ...]
    after_edges: tuple[SupportEdge, ...]
    aligned_edges: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class MotifReview:
    v3_classification: str
    support_strength: int
    raw_motif_before: str
    raw_motif_after: str
    canonical_motif_before: str
    canonical_motif_after: str
    support_connected_to_primary: bool
    involves_old_neighbor: bool
    involves_new_neighbor: bool
    crosses_old_to_new_role: bool
    preserved_nontrivial_edge_count: int
    preserved_independent_source_count: int
    transferred_edge_count: int
    closed_path_count: int
    role_mapping: str
    mapping_ambiguity_count: int
    evidence_tags: tuple[str, ...]
    resource_status: str = "COMPLETE"
    evidence_complete: bool = True


@dataclass(frozen=True, slots=True)
class ReviewedSupport:
    graph: SupportGraph
    review: MotifReview


@dataclass(frozen=True, slots=True)
class ControlSummary:
    real_classification: str
    control_classifications: tuple[str, ...]


def review_support_graph(
    before: RawNetwork,
    after: RawNetwork,
    commit: Any,
    *,
    max_local_radius: int = 1,
    require_primary_role_participation: bool = True,
) -> ReviewedSupport:
    if max_local_radius != 1:
        raise ValueError("v3 review supports radius one only")
    source = int(commit.source_raw_id)
    slot = int(commit.slot_semantic)
    old = int(commit.old_target)
    new = int(
        commit.actual_new_target
        if hasattr(commit, "actual_new_target")
        else commit.new_target
    )
    graph = build_support_graph(before, after, source, old, new, slot)
    review = _classify(
        before,
        after,
        graph,
        require_primary_role_participation=require_primary_role_participation,
    )
    return ReviewedSupport(graph, review)


def build_support_graph(
    before: RawNetwork,
    after: RawNetwork,
    source: int,
    old: int,
    new: int,
    slot: int,
) -> SupportGraph:
    actors = {source, old, new}
    nodes = set(actors)
    for network in (before, after):
        for actor in actors:
            nodes.update(network.targets[actor])
        nodes.update(
            raw_id
            for raw_id, targets in enumerate(network.targets)
            if actors & set(targets)
        )
    before_raw = _edge_set(before, nodes)
    after_raw = _edge_set(after, nodes)
    all_edges = sorted(before_raw | after_raw)
    aligned = _aligned_edges(before, after, source, old, new)
    canonically_aligned = {
        (
            int(edge["source"]),
            int(edge["slot_semantic"]),
            int(edge["target"]),
        )
        for row in aligned
        for edge in (row["before_edge"], row["after_edge"])
    }
    rows = tuple(
        SupportEdge(
            edge[0],
            edge[1],
            edge[2],
            edge in before_raw,
            edge in after_raw,
            edge in before_raw and edge in after_raw,
            edge in canonically_aligned,
            edge in {(source, slot, old), (source, slot, new)},
            edge[0] == source and edge[1] != slot,
        )
        for edge in all_edges
    )
    before_edges = tuple(row for row in rows if row.before_present)
    after_edges = tuple(row for row in rows if row.after_present)
    return SupportGraph(
        source,
        old,
        new,
        slot,
        tuple(sorted(nodes)),
        before_edges,
        after_edges,
        aligned,
    )


def generate_background_controls(
    network: RawNetwork,
    *,
    real_source: int,
    real_old: int,
    real_new: int,
    slot_semantic: int,
    count: int,
    seed: int,
) -> tuple[tuple[int, int, int], ...]:
    """Choose deterministic same-state controls without touching the main RNG."""
    indegree = _indegrees(network)
    old_degree = indegree[real_old]
    new_degree = indegree[real_new]
    candidates = []
    for source in range(network.size):
        if source == real_source:
            continue
        old = network.targets[source][slot_semantic]
        blocked = {source, *network.targets[source]}
        for new in range(network.size):
            if new in blocked or new == real_new:
                continue
            distance = abs(indegree[old] - old_degree) + abs(
                indegree[new] - new_degree
            )
            candidates.append((distance, source, old, new))
    rng = Random(seed)
    rng.shuffle(candidates)
    candidates.sort(key=lambda item: item[0])
    return tuple((source, old, new) for _, source, old, new in candidates[:count])


def graph_payload(reviewed: ReviewedSupport) -> dict[str, Any]:
    return {
        "graph": {
            **asdict(reviewed.graph),
            "before_support_graph": [
                asdict(edge) for edge in reviewed.graph.before_edges
            ],
            "after_support_graph": [
                asdict(edge) for edge in reviewed.graph.after_edges
            ],
            "aligned_support_graph": list(reviewed.graph.aligned_edges),
        },
        "review": asdict(reviewed.review),
    }


def _classify(
    before: RawNetwork,
    after: RawNetwork,
    graph: SupportGraph,
    *,
    require_primary_role_participation: bool,
) -> MotifReview:
    a, b, c, slot = (
        graph.primary_source,
        graph.old_neighbor,
        graph.new_neighbor,
        graph.slot_semantic,
    )
    before_edges = _all_edges(before)
    after_edges = _all_edges(after)
    preserved = before_edges & after_edges
    siblings = {
        edge
        for edge in preserved
        if edge[0] == a and edge[1] != slot and edge[2] not in {b, c}
    }
    nontrivial = {
        edge
        for edge in preserved
        if edge not in siblings
        and edge not in {(a, slot, b), (a, slot, c)}
        and (edge[0] in graph.nodes and edge[2] in graph.nodes)
    }
    independent_sources = {edge[0] for edge in nontrivial if edge[0] != a}

    incoming_transfers = _incoming_role_transfers(before, after, b, c, {a})
    outgoing_transfers = _outgoing_role_transfers(before, after, b, c, {a})
    all_transfers = incoming_transfers + outgoing_transfers
    dual_sources = tuple(
        third
        for third, _, _ in incoming_transfers
        if any(before.targets[third][support_slot] == a for support_slot in range(SLOT_COUNT))
        and any(after.targets[third][support_slot] == a for support_slot in range(SLOT_COUNT))
    )
    closed = _closed_supports(before, after, a, b, c)
    relational_scaffolds = _three_member_scaffolds(
        before, after, a, b, c, slot
    )
    canonical_matches = _canonical_motif_matches(before, after, a, b, c, slot)
    canonical_acceptable = 0 < len(canonical_matches) <= 8
    static_to_a = {
        edge for edge in nontrivial if edge[2] == a and edge[0] not in {a, b, c}
    }

    before_motifs = _motif_labels(before, a, b)
    after_motifs = _motif_labels(after, a, c)
    raw_before = _raw_motif_signature(before, a, b)
    raw_after = _raw_motif_signature(after, a, c)
    canonical_before = _canonical_motif_signature(before, a, b)
    canonical_after = _canonical_motif_signature(after, a, c)
    connected = _support_connected(graph, nontrivial)
    involves_old = any(b in (edge[0], edge[2]) for edge in nontrivial) or bool(
        all_transfers
    )
    involves_new = any(c in (edge[0], edge[2]) for edge in nontrivial) or bool(
        all_transfers
    )
    crosses = bool(all_transfers or closed or canonical_matches)
    participation = (
        involves_old
        or involves_new
        or crosses
        or bool(closed)
        or bool(dual_sources)
    )
    connected = connected or bool(
        canonical_acceptable or all_transfers or closed or relational_scaffolds
    )
    canonical_gate = canonical_acceptable
    gate = (len(nontrivial) >= 2 or canonical_gate) and connected and (
        participation or not require_primary_role_participation
    )

    classification = "NO_LOCAL_SUPPORT"
    strength = V3_STRENGTH["NONE"]
    tags = []
    mapping = ""
    ambiguity = len(canonical_matches)
    if gate and canonical_acceptable:
        classification = "CANONICAL_MOTIF_PRESERVED"
        strength = V3_STRENGTH["STRUCTURED_CANONICAL_SUPPORT"]
        mapping = json.dumps(canonical_matches, sort_keys=True, separators=(",", ":"))
        tags.append("R7")
    elif gate and closed:
        classification = "CLOSED_LOCAL_PATH_PRESERVED"
        strength = V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]
        tags.append("R2")
    elif gate and len(incoming_transfers) >= 2:
        classification = "MULTI_SOURCE_CONVERGENCE_TRANSFER"
        strength = V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]
        tags.append("R3")
    elif gate and len(outgoing_transfers) >= 2:
        classification = "MULTI_TARGET_DIVERGENCE_TRANSFER"
        strength = V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]
        tags.append("R4")
    elif gate and all_transfers and not dual_sources:
        classification = "SHARED_THIRD_MEMBER_ROLE_TRANSFER"
        strength = V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]
        tags.append("R1")
    elif gate and dual_sources:
        classification = "SHARED_SOURCE_DUAL_ROLE_TRANSFER"
        strength = V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]
        tags.append("R5")
    elif gate and relational_scaffolds:
        classification = "THREE_MEMBER_RELATIONAL_SCAFFOLD_PRESERVED"
        strength = V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]
        tags.append("R6")
    elif len(static_to_a) >= 2 and not (all_transfers or closed or canonical_acceptable):
        classification = "STATIC_CONVERGENCE_BACKGROUND"
        strength = V3_STRENGTH["WEAK_BACKGROUND"]
    elif len(independent_sources) >= 2:
        classification = "BACKGROUND_MULTI_SOURCE_PRESERVATION"
        strength = V3_STRENGTH["WEAK_BACKGROUND"]
    elif len(independent_sources) == 1:
        classification = "WEAK_SINGLE_INDEPENDENT_SUPPORT"
        strength = V3_STRENGTH["WEAK_BACKGROUND"]
    elif siblings:
        classification = "TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION"
        strength = V3_STRENGTH["TRIVIAL"]
    elif not nontrivial:
        classification = "ONE_MEMBER_ONLY"

    return MotifReview(
        classification,
        strength,
        raw_before,
        raw_after,
        canonical_before,
        canonical_after,
        connected,
        involves_old,
        involves_new,
        crosses,
        len(nontrivial),
        len(independent_sources),
        len(all_transfers),
        len(closed),
        mapping,
        ambiguity,
        tuple(tags + sorted(before_motifs | after_motifs)),
    )


def _aligned_edges(
    before: RawNetwork, after: RawNetwork, a: int, b: int, c: int
) -> tuple[dict[str, Any], ...]:
    rows = []
    for kind, transfers in (
        ("INCOMING_ROLE", _incoming_role_transfers(before, after, b, c, {a})),
        ("OUTGOING_ROLE", _outgoing_role_transfers(before, after, b, c, {a})),
    ):
        for third, before_edge, after_edge in transfers:
            rows.append(
                {
                    "alignment_kind": kind,
                    "support_raw_id": third,
                    "before_edge": _edge_payload(before_edge),
                    "after_edge": _edge_payload(after_edge),
                }
            )
    return tuple(
        sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
    )


def _incoming_role_transfers(
    before: RawNetwork,
    after: RawNetwork,
    old: int,
    new: int,
    excluded: set[int],
) -> tuple[tuple[int, tuple[int, int, int], tuple[int, int, int]], ...]:
    rows = []
    for third in range(before.size):
        if third in excluded | {old, new}:
            continue
        for old_slot in range(SLOT_COUNT):
            if before.targets[third][old_slot] != old:
                continue
            for new_slot in range(SLOT_COUNT):
                if after.targets[third][new_slot] == new:
                    rows.append((third, (third, old_slot, old), (third, new_slot, new)))
    return tuple(rows)


def _outgoing_role_transfers(
    before: RawNetwork,
    after: RawNetwork,
    old: int,
    new: int,
    excluded: set[int],
) -> tuple[tuple[int, tuple[int, int, int], tuple[int, int, int]], ...]:
    rows = []
    for old_slot, third in enumerate(before.targets[old]):
        if third in excluded | {old, new}:
            continue
        for new_slot, target in enumerate(after.targets[new]):
            if target == third:
                rows.append((third, (old, old_slot, third), (new, new_slot, third)))
    return tuple(rows)


def _closed_supports(
    before: RawNetwork, after: RawNetwork, a: int, b: int, c: int
) -> tuple[int, ...]:
    rows = []
    for third in range(before.size):
        if third in {a, b, c}:
            continue
        before_triangle = _directed_closed_triangle(before, a, b, third)
        after_triangle = _directed_closed_triangle(after, a, c, third)
        if before_triangle and after_triangle:
            rows.append(third)
    return tuple(rows)


def _directed_closed_triangle(
    network: RawNetwork, source: int, neighbor: int, third: int
) -> bool:
    return (
        neighbor in network.targets[source]
        and third in network.targets[neighbor]
        and source in network.targets[third]
    )


def _three_member_scaffolds(
    before: RawNetwork, after: RawNetwork, a: int, b: int, c: int, slot: int
) -> tuple[int, ...]:
    rows = []
    for third in range(before.size):
        if third in {a, b, c}:
            continue
        before_roles = _role_edges(before, a, b, third)
        after_roles = _role_edges(after, a, c, third)
        common = {
            edge
            for edge in before_roles & after_roles
            if edge not in {("A", slot, "N")}
            and not (edge[0] == "A" and edge[2] == "S")
        }
        if len(common) >= 2 and len({edge[0] for edge in common}) >= 2:
            rows.append(third)
    return tuple(rows)


def _canonical_motif_matches(
    before: RawNetwork, after: RawNetwork, a: int, b: int, c: int, slot: int
) -> tuple[dict[str, Any], ...]:
    matches = []
    before_support = _radius_one_nodes(before, {a, b}) - {a, b, c}
    after_support = _radius_one_nodes(after, {a, c}) - {a, b, c}
    for old_support in sorted(before_support):
        before_roles = _role_edges(before, a, b, old_support)
        for new_support in sorted(after_support):
            if old_support == new_support:
                continue
            after_roles = _role_edges(after, a, c, new_support)
            common = before_roles & after_roles
            nonprimary = {
                edge
                for edge in common
                if edge != ("A", slot, "N")
                and not (edge[0] == "A" and edge[2] == "S")
            }
            if len(common) >= 3 and len(nonprimary) >= 2:
                matches.append(
                    {
                        "A": [a, a],
                        "N": [b, c],
                        "S": [old_support, new_support],
                        "relations": sorted(map(str, common)),
                    }
                )
    return tuple(matches)


def _motif_labels(network: RawNetwork, a: int, neighbor: int) -> set[str]:
    labels = set()
    for third in sorted(_radius_one_nodes(network, {a, neighbor}) - {a, neighbor}):
        edges = _role_edges(network, a, neighbor, third)
        if len(edges) < 2:
            continue
        pairs = {frozenset((edge[0], edge[2])) for edge in edges}
        sources = Counter(edge[0] for edge in edges)
        targets = Counter(edge[2] for edge in edges)
        if len(pairs) == 3:
            labels.add("CLOSED_TRIANGLE")
        elif any(count >= 2 for count in targets.values()):
            labels.add("CONVERGENCE")
            labels.add("SHARED_TARGET_FORK")
        elif any(count >= 2 for count in sources.values()):
            labels.add("DIVERGENCE")
            labels.add("SHARED_SOURCE_FORK")
        elif len(edges) == 2:
            labels.add("OPEN_CHAIN")
        else:
            labels.add("MIXED_THREE_NODE_MOTIF")
        if any(
            left[0] == right[2] and left[2] == right[0]
            for left in edges
            for right in edges
        ):
            labels.add("RECIPROCAL_PAIR_WITH_SUPPORT")
    if not labels:
        labels.add("DISCONNECTED_BACKGROUND")
    return labels


def _raw_motif_signature(network: RawNetwork, a: int, neighbor: int) -> str:
    payload = []
    for third in sorted(_radius_one_nodes(network, {a, neighbor}) - {a, neighbor}):
        roles = _role_edges(network, a, neighbor, third)
        if len(roles) >= 2:
            payload.append({"support_raw_id": third, "edges": sorted(map(str, roles))})
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _canonical_motif_signature(network: RawNetwork, a: int, neighbor: int) -> str:
    signatures = [
        sorted(map(str, _role_edges(network, a, neighbor, third)))
        for third in _radius_one_nodes(network, {a, neighbor}) - {a, neighbor}
        if len(_role_edges(network, a, neighbor, third)) >= 2
    ]
    return json.dumps(sorted(signatures), separators=(",", ":"))


def _role_edges(
    network: RawNetwork, a: int, neighbor: int, support: int
) -> set[tuple[str, int, str]]:
    roles = {a: "A", neighbor: "N", support: "S"}
    return {
        (roles[source], slot, roles[target])
        for source in roles
        for slot, target in enumerate(network.targets[source])
        if target in roles
    }


def _support_connected(
    graph: SupportGraph, support_edges: set[tuple[int, int, int]]
) -> bool:
    if not support_edges:
        return False
    adjacency: dict[int, set[int]] = defaultdict(set)
    for source, _, target in support_edges | {
        (graph.primary_source, graph.slot_semantic, graph.old_neighbor),
        (graph.primary_source, graph.slot_semantic, graph.new_neighbor),
    }:
        adjacency[source].add(target)
        adjacency[target].add(source)
    reached = {graph.primary_source}
    queue = deque(reached)
    while queue:
        node = queue.popleft()
        for target in adjacency[node] - reached:
            reached.add(target)
            queue.append(target)
    return any(edge[0] in reached and edge[2] in reached for edge in support_edges)


def _radius_one_nodes(network: RawNetwork, roots: set[int]) -> set[int]:
    nodes = set(roots)
    for root in roots:
        nodes.update(network.targets[root])
    nodes.update(
        source
        for source, targets in enumerate(network.targets)
        if roots & set(targets)
    )
    return nodes


def _edge_set(network: RawNetwork, nodes: set[int]) -> set[tuple[int, int, int]]:
    return {
        (source, slot, target)
        for source in nodes
        for slot, target in enumerate(network.targets[source])
        if target in nodes
    }


def _all_edges(network: RawNetwork) -> set[tuple[int, int, int]]:
    return {
        (source, slot, target)
        for source, targets in enumerate(network.targets)
        for slot, target in enumerate(targets)
    }


def _indegrees(network: RawNetwork) -> tuple[int, ...]:
    counts = [0] * network.size
    for targets in network.targets:
        for target in targets:
            counts[target] += 1
    return tuple(counts)


def _edge_payload(edge: tuple[int, int, int]) -> dict[str, int]:
    return {"source": edge[0], "slot_semantic": edge[1], "target": edge[2]}
