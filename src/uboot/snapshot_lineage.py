"""Bounded snapshots and offline closed-object lineage analysis."""

from __future__ import annotations

from collections import Counter
import csv
from hashlib import sha256
from pathlib import Path
from statistics import fmean
from typing import Any

from uboot.candidate_graph import candidate_objects, strongly_connected_components
from uboot.kernel import SLOT_COUNT, RawNetwork
from uboot.observables import mutual_pairs


def histogram_bucket(value: int) -> str:
    if value < 0:
        raise ValueError("histogram values cannot be negative")
    if value <= 5:
        return str(value)
    for lower, upper in (
        (6, 7),
        (8, 15),
        (16, 31),
        (32, 63),
        (64, 127),
        (128, 255),
    ):
        if value <= upper:
            return f"{lower}-{upper}"
    return "256+"


def continuity_metrics(
    source: frozenset[int], target: frozenset[int]
) -> dict[str, float | int]:
    shared = source & target
    return {
        "shared_member_count": len(shared),
        "member_jaccard": len(shared) / len(source | target),
        "source_member_retention": len(shared) / len(source),
        "target_member_inheritance": len(shared) / len(target),
    }


def same_meaning_mutual_pairs(network: RawNetwork) -> frozenset[tuple[int, int, int]]:
    return frozenset(
        (source, target, slot)
        for source, row in enumerate(network.targets)
        for slot, target in enumerate(row)
        if source < target and network.targets[target][slot] == source
    )


def layered_topology_stats(
    network: RawNetwork, candidate_graph: list[tuple[int, ...]]
) -> dict[str, int | float]:
    raw_pairs = mutual_pairs(network)
    same_pairs: set[tuple[int, int]] = set()
    fully_same = 0
    single_slot = 0
    single_slot_same = 0
    raw_slot_multiplicity = 0
    shared_mask_counts: Counter[int] = Counter()
    for left, right in raw_pairs:
        left_meanings = {
            slot for slot, target in enumerate(network.targets[left]) if target == right
        }
        right_meanings = {
            slot for slot, target in enumerate(network.targets[right]) if target == left
        }
        shared = left_meanings & right_meanings
        shared_mask = sum(1 << slot for slot in shared)
        shared_mask_counts[shared_mask] += 1
        raw_slot_multiplicity += len(left_meanings) + len(right_meanings)
        if shared:
            same_pairs.add((left, right))
        if left_meanings == right_meanings:
            fully_same += 1
        if len(left_meanings) == len(right_meanings) == 1:
            single_slot += 1
            single_slot_same += bool(shared)
    candidate_pairs = {
        (left, right)
        for left, targets in enumerate(candidate_graph)
        for right in targets
        if left < right and left in candidate_graph[right]
    }
    triangle_count, supported_triangles = _mutual_triangles(raw_pairs, same_pairs)
    slot_count = SLOT_COUNT * network.size
    result: dict[str, int | float] = {
        "all_mutual_pair_count": len(raw_pairs),
        "same_meaning_mutual_pair_count": len(same_pairs),
        "fully_same_meaning_mutual_pair_count": fully_same,
        "different_only_mutual_pair_count": len(raw_pairs) - len(same_pairs),
        "single_slot_mutual_pair_count": single_slot,
        "multi_slot_mutual_pair_count": len(raw_pairs) - single_slot,
        "single_slot_same_meaning_mutual_pair_count": single_slot_same,
        "raw_mutual_slot_multiplicity": raw_slot_multiplicity,
        "raw_mutual_triangle_count": triangle_count,
        "same_meaning_supported_triangle_count": supported_triangles,
        "mixed_meaning_triangle_count": triangle_count - supported_triangles,
        "candidate_directed_edge_count": sum(map(len, candidate_graph)),
        "candidate_mutual_pair_count": len(candidate_pairs),
        "raw_mutual_and_candidate_pair_count": len(raw_pairs & candidate_pairs),
        "all_mutual_pair_density_per_slot": len(raw_pairs) / slot_count,
        "same_meaning_mutual_pair_density_per_slot": len(same_pairs) / slot_count,
        "all_mutual_pairs_per_node": len(raw_pairs) / network.size,
        "same_meaning_mutual_pairs_per_node": len(same_pairs) / network.size,
        "candidate_edges_per_node": sum(map(len, candidate_graph)) / network.size,
    }
    for mask in range(8):
        result[f"shared_meaning_mask_{mask}_pair_count"] = shared_mask_counts[mask]
    return result


def _mutual_triangles(
    raw_pairs: frozenset[tuple[int, int]], same_pairs: set[tuple[int, int]]
) -> tuple[int, int]:
    if not raw_pairs:
        return 0, 0
    size = max(max(pair) for pair in raw_pairs) + 1
    adjacency = [set[int]() for _ in range(size)]
    for left, right in raw_pairs:
        adjacency[left].add(right)
        adjacency[right].add(left)
    total = 0
    supported = 0
    for left in range(size):
        for middle in (node for node in adjacency[left] if node > left):
            for right in adjacency[left] & adjacency[middle]:
                if right <= middle:
                    continue
                total += 1
                edges = {(left, middle), (left, right), (middle, right)}
                supported += edges <= same_pairs
    return total, supported


def tentacle_counts_by_meaning(
    network: RawNetwork, members: frozenset[int]
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    incoming = [0] * SLOT_COUNT
    outgoing = [0] * SLOT_COUNT
    for source, row in enumerate(network.targets):
        for slot, target in enumerate(row):
            if source in members and target not in members:
                outgoing[slot] += 1
            elif source not in members and target in members:
                incoming[slot] += 1
    return tuple(incoming), tuple(outgoing)  # type: ignore[return-value]


def tentacle_counts(
    network: RawNetwork, members: frozenset[int]
) -> tuple[int, int]:
    incoming, outgoing = tentacle_counts_by_meaning(network, members)
    return sum(incoming), sum(outgoing)


def closed_object_rows(
    network: RawNetwork,
    candidate_graph: list[tuple[int, ...]],
    snapshot_id: int,
    tick: int,
    *,
    member_inline_limit: int = 32,
    include_singletons: bool = False,
) -> list[dict[str, Any]]:
    all_pairs = mutual_pairs(network)
    same_pairs = {(left, right) for left, right, _ in same_meaning_mutual_pairs(network)}
    rows = []
    for index, item in enumerate(
        candidate_objects(candidate_graph, include_singletons=include_singletons)
    ):
        members = frozenset(item.members)
        incoming, outgoing = tentacle_counts_by_meaning(network, members)
        inline = len(members) <= member_inline_limit
        serialized = "|".join(map(str, item.members))
        internal_candidate_edges = {
            (source, target)
            for source in members
            for target in candidate_graph[source]
            if target in members
        }
        internal_raw_edges = {
            (left, right)
            for left, right in all_pairs
            if left in members and right in members
        }
        rows.append(
            {
                "snapshot_id": snapshot_id,
                "tick": tick,
                "sweep": tick / (SLOT_COUNT * network.size),
                "N": network.size,
                "object_snapshot_id": index,
                "object_class": item.object_class,
                "size": len(members),
                "member_ids": serialized if inline else "",
                "member_hash": "" if inline else sha256(serialized.encode()).hexdigest(),
                "internal_candidate_directed_edges": item.internal_candidate_edges,
                "internal_candidate_edges": _serialize_edges(internal_candidate_edges),
                "outgoing_candidate_edge_count": item.outgoing_candidate_edges,
                "incoming_raw_tentacles_meaning_1": incoming[0],
                "incoming_raw_tentacles_meaning_2": incoming[1],
                "incoming_raw_tentacles_meaning_3": incoming[2],
                "outgoing_raw_tentacles_meaning_1": outgoing[0],
                "outgoing_raw_tentacles_meaning_2": outgoing[1],
                "outgoing_raw_tentacles_meaning_3": outgoing[2],
                "candidate_two_cycle_count": sum(
                    left in members and right in members
                    for left, targets in enumerate(candidate_graph)
                    for right in targets
                    if left < right and left in candidate_graph[right]
                ),
                "internal_raw_mutual_pair_count": sum(
                    left in members and right in members for left, right in all_pairs
                ),
                "internal_raw_mutual_edges": _serialize_edges(internal_raw_edges),
                "internal_same_meaning_mutual_pair_count": sum(
                    left in members and right in members for left, right in same_pairs
                ),
            }
        )
    return rows


def system_snapshot(
    network: RawNetwork,
    candidate_graph: list[tuple[int, ...]],
    snapshot_id: int,
    tick: int,
    engine_summary: dict[str, Any],
    objects: list[dict[str, Any]],
) -> dict[str, Any]:
    sizes = [len(group) for group in candidate_graph]
    all_sccs = strongly_connected_components(candidate_graph)
    closed = candidate_objects(candidate_graph)
    classes = Counter(item.object_class for item in closed)
    nontrivial_count = sum(len(item.members) >= 2 for item in closed)
    topology = layered_topology_stats(network, candidate_graph)
    return {
        "snapshot_id": snapshot_id,
        "tick": tick,
        "sweep": tick / (SLOT_COUNT * network.size),
        "N": network.size,
        "active_worker_count": engine_summary["active_worker_count"],
        "pending_response_count": engine_summary["pending_response_count"],
        "responses_created_total": engine_summary.get("responses_created_total", 0),
        "responses_completed_total": engine_summary.get("responses_completed_total", 0),
        "responses_rejected_total": engine_summary.get("responses_rejected_total", 0),
        "mean_completed_chain_length": engine_summary["mean_completed_chain_length"],
        "mean_response_lifetime_ticks": engine_summary["mean_completed_lifetime_ticks"],
        **topology,
        "candidate_size_0": sum(size == 0 for size in sizes),
        "candidate_size_1": sum(size == 1 for size in sizes),
        "candidate_size_2": sum(size == 2 for size in sizes),
        "candidate_size_ge3": sum(size >= 3 for size in sizes),
        "candidate_edge_count": sum(sizes),
        "candidate_scc_count": len(all_sccs),
        "closed_candidate_scc_count": nontrivial_count,
        "candidate_isolated_count": classes["candidate_isolated"],
        "closed_self_loop_count": classes["closed_self_loop"],
        "closed_pair_count": classes["pair"],
        "closed_triple_count": classes["triple"],
        "closed_larger_count": classes["larger"],
        "max_candidate_scc_size": max(map(len, all_sccs), default=0),
        "max_closed_candidate_scc_size": max(
            (len(item.members) for item in closed if len(item.members) >= 2),
            default=0,
        ),
        "whole_network_object_present": any(
            len(item.members) == network.size for item in closed
        ),
        "closed_object_rows": len(objects),
    }


def graph_diagnostics(
    network: RawNetwork, candidate_graph: list[tuple[int, ...]]
) -> dict[str, int]:
    candidate_edges = sum(map(len, candidate_graph))
    candidate_sccs = strongly_connected_components(candidate_graph)
    raw = [tuple(set(row)) for row in network.targets]
    raw_sccs = strongly_connected_components(raw)
    mutual = same_meaning_mutual_pairs(network)
    adjacency = [set[int]() for _ in range(network.size)]
    for left, right, _ in mutual:
        adjacency[left].add(right)
        adjacency[right].add(left)
    component_sizes = _undirected_component_sizes(adjacency)
    assert candidate_edges == sum(len(targets) for targets in candidate_graph)
    return {
        "candidate_graph_edge_count": candidate_edges,
        "candidate_graph_scc_count": len(candidate_sccs),
        "candidate_graph_max_scc_size": max(map(len, candidate_sccs), default=0),
        "raw_slot_graph_edge_count": sum(map(len, raw)),
        "raw_slot_graph_scc_count": len(raw_sccs),
        "raw_slot_graph_max_scc_size": max(map(len, raw_sccs), default=0),
        "mutual_graph_edge_count": len(mutual),
        "mutual_graph_connected_component_count": len(component_sizes),
        "mutual_graph_max_component_size": max(component_sizes, default=0),
    }


def analyze_lineages(
    input_path: Path,
    output_dir: Path,
    *,
    min_jaccard: float = 0.25,
    min_containment: float = 0.5,
) -> dict[str, float | int | str]:
    rows = list(csv.DictReader(input_path.open(encoding="utf-8")))
    by_snapshot: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        snapshot_id = int(row["snapshot_id"])
        row["snapshot_id"] = snapshot_id
        row["object_snapshot_id"] = int(row["object_snapshot_id"])
        row["size"] = int(row["size"])
        row["members"] = (
            frozenset(map(int, row["member_ids"].split("|")))
            if row["member_ids"]
            else frozenset()
        )
        row["candidate_edges"] = _parse_edges(row.get("internal_candidate_edges", ""))
        row["raw_mutual_edges"] = _parse_edges(row.get("internal_raw_mutual_edges", ""))
        by_snapshot.setdefault(snapshot_id, []).append(row)
    lineage: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    transitions: Counter[tuple[str, str]] = Counter()
    ids = sorted(by_snapshot)
    for before_id, after_id in zip(ids, ids[1:]):
        before, after = by_snapshot[before_id], by_snapshot[after_id]
        candidates = []
        source_counts: Counter[int] = Counter()
        target_counts: Counter[int] = Counter()
        for source in before:
            for target in after:
                if not source["members"] or not target["members"]:
                    continue
                metrics = continuity_metrics(source["members"], target["members"])
                if not (
                    metrics["member_jaccard"] >= min_jaccard
                    or metrics["source_member_retention"] >= min_containment
                    or metrics["target_member_inheritance"] >= min_containment
                ):
                    continue
                source_counts[source["object_snapshot_id"]] += 1
                target_counts[target["object_snapshot_id"]] += 1
                candidates.append((source, target, metrics))
        primary_sources: set[int] = set()
        primary_targets: set[int] = set()
        for source in before:
            options = [item for item in candidates if item[0] is source]
            options.sort(
                key=lambda item: (
                    -item[2]["source_member_retention"],
                    -item[2]["member_jaccard"],
                    item[0]["object_class"] != item[1]["object_class"],
                    item[1]["object_snapshot_id"],
                )
            )
            if options:
                primary_sources.add(source["object_snapshot_id"])
                primary_targets.add(options[0][1]["object_snapshot_id"])
            for position, (src, dst, metrics) in enumerate(options):
                relation = _lineage_relation(src["members"], dst["members"])
                if position:
                    relation = "ambiguous"
                if source_counts[src["object_snapshot_id"]] > 1 and position:
                    relation = "split_candidate"
                if target_counts[dst["object_snapshot_id"]] > 1 and position:
                    relation = "merge_candidate"
                lineage.append(
                    {
                        "from_snapshot_id": before_id,
                        "to_snapshot_id": after_id,
                        "from_object_index": src["object_snapshot_id"],
                        "to_object_index": dst["object_snapshot_id"],
                        "from_type": src["object_class"],
                        "to_type": dst["object_class"],
                        **metrics,
                        "same_type": src["object_class"] == dst["object_class"],
                        "match_relation": relation,
                        "removed_member": _single_member(src["members"] - dst["members"]),
                        "added_member": _single_member(dst["members"] - src["members"]),
                        "shared_members": "|".join(map(str, sorted(src["members"] & dst["members"]))),
                        "candidate_edges_removed": len(src["candidate_edges"] - dst["candidate_edges"]),
                        "candidate_edges_added": len(dst["candidate_edges"] - src["candidate_edges"]),
                        "raw_mutual_edges_removed": len(src["raw_mutual_edges"] - dst["raw_mutual_edges"]),
                        "raw_mutual_edges_added": len(dst["raw_mutual_edges"] - src["raw_mutual_edges"]),
                    }
                )
                if position == 0:
                    transitions[(src["object_class"], dst["object_class"])] += 1
        deaths = [
            row for row in before if row["object_snapshot_id"] not in primary_sources
        ]
        births = [
            row for row in after if row["object_snapshot_id"] not in primary_targets
        ]
        for row in deaths:
            transitions[(row["object_class"], "none")] += 1
            lineage.append(_unmatched_lineage(before_id, after_id, row, "death"))
        for row in births:
            transitions[("none", row["object_class"])] += 1
            lineage.append(_unmatched_lineage(before_id, after_id, row, "birth"))
        current_classes = Counter(row["object_class"] for row in after)
        whole = any(row["size"] == int(row["N"]) for row in after)
        summaries.append(
            {
                "from_snapshot_id": before_id,
                "to_snapshot_id": after_id,
                "previous_object_count": len(before),
                "current_object_count": len(after),
                "pair_count": current_classes["pair"],
                "triple_count": current_classes["triple"],
                "larger_count": current_classes["larger"],
                "mean_object_size": fmean(row["size"] for row in after) if after else 0,
                "max_object_size": max((row["size"] for row in after), default=0),
                "whole_network_object_present": whole,
                "primary_matches": len(primary_sources),
                "unmatched_births": len(births),
                "unmatched_deaths": len(deaths),
                "ambiguous_source_count": sum(v > 1 for v in source_counts.values()),
                "ambiguous_target_count": sum(v > 1 for v in target_counts.values()),
                "split_candidate_count": sum(v > 1 for v in source_counts.values()),
                "merge_candidate_count": sum(v > 1 for v in target_counts.values()),
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "object_lineages.csv", lineage)
    _write_csv(output_dir / "snapshot_matching_summary.csv", summaries)
    _write_csv(
        output_dir / "object_transition_matrix.csv",
        [
            {"from_type": key[0], "to_type": key[1], "count": count}
            for key, count in sorted(transitions.items())
        ],
    )
    degenerate = any(row["whole_network_object_present"] for row in summaries)
    return {
        "snapshot_count": len(ids),
        "mean_objects_per_snapshot": fmean(map(len, by_snapshot.values()))
        if by_snapshot
        else 0,
        "primary_matches": sum(row["primary_matches"] for row in summaries),
        "unmatched_births": sum(row["unmatched_births"] for row in summaries),
        "unmatched_deaths": sum(row["unmatched_deaths"] for row in summaries),
        "split_candidates": sum(row["split_candidate_count"] for row in summaries),
        "merge_candidates": sum(row["merge_candidate_count"] for row in summaries),
        "lineage_validation_status": (
            "degenerate_whole_network_object" if degenerate else "eligible"
        ),
    }


def exact_object_lifetime_summary(input_path: Path) -> dict[str, float | int]:
    """Summarize exact-member persistence at snapshot resolution."""
    if not input_path.exists() or input_path.stat().st_size == 0:
        return {
            "completed_object_lifetimes": 0,
            "mean_completed_object_lifetime_ticks": 0,
            "max_completed_object_lifetime_ticks": 0,
            "active_object_count": 0,
            "mean_active_object_age_ticks": 0,
            "max_active_object_age_ticks": 0,
        }
    rows = list(csv.DictReader(input_path.open(encoding="utf-8")))
    by_tick: dict[int, set[str]] = {}
    for row in rows:
        key = row["member_ids"] or row["member_hash"]
        by_tick.setdefault(int(row["tick"]), set()).add(key)
    births: dict[str, int] = {}
    completed = []
    previous: set[str] = set()
    last_tick = 0
    for tick in sorted(by_tick):
        current = by_tick[tick]
        for key in current - previous:
            births[key] = tick
        for key in previous - current:
            completed.append(tick - births.pop(key))
        previous = current
        last_tick = tick
    active_ages = [last_tick - birth for birth in births.values()]
    return {
        "completed_object_lifetimes": len(completed),
        "mean_completed_object_lifetime_ticks": fmean(completed) if completed else 0,
        "max_completed_object_lifetime_ticks": max(completed, default=0),
        "active_object_count": len(active_ages),
        "mean_active_object_age_ticks": fmean(active_ages) if active_ages else 0,
        "max_active_object_age_ticks": max(active_ages, default=0),
    }


def _undirected_component_sizes(adjacency: list[set[int]]) -> list[int]:
    unseen = {node for node, neighbors in enumerate(adjacency) if neighbors}
    sizes = []
    while unseen:
        stack = [unseen.pop()]
        size = 0
        while stack:
            node = stack.pop()
            size += 1
            new = adjacency[node] & unseen
            unseen.difference_update(new)
            stack.extend(new)
        sizes.append(size)
    return sizes


def _serialize_edges(edges: set[tuple[int, int]]) -> str:
    return "|".join(f"{left}>{right}" for left, right in sorted(edges))


def _parse_edges(value: str) -> set[tuple[int, int]]:
    if not value:
        return set()
    return {tuple(map(int, edge.split(">"))) for edge in value.split("|")}  # type: ignore[misc]


def _lineage_relation(source: frozenset[int], target: frozenset[int]) -> str:
    if source == target:
        return "persist"
    if source < target:
        return "growth"
    if target < source:
        return "shrink"
    if len(source) == len(target) and len(source & target) == len(source) - 1:
        return "member_replacement"
    return "ambiguous"


def _single_member(values: frozenset[int]) -> int | str:
    return next(iter(values)) if len(values) == 1 else ""


def _unmatched_lineage(
    before_id: int, after_id: int, row: dict[str, Any], relation: str
) -> dict[str, Any]:
    source = relation == "death"
    return {
        "from_snapshot_id": before_id,
        "to_snapshot_id": after_id,
        "from_object_index": row["object_snapshot_id"] if source else "",
        "to_object_index": "" if source else row["object_snapshot_id"],
        "from_type": row["object_class"] if source else "none",
        "to_type": "none" if source else row["object_class"],
        "shared_member_count": 0,
        "member_jaccard": 0,
        "source_member_retention": 0,
        "target_member_inheritance": 0,
        "same_type": False,
        "match_relation": relation,
        "removed_member": "",
        "added_member": "",
        "shared_members": "",
        "candidate_edges_removed": len(row["candidate_edges"]) if source else 0,
        "candidate_edges_added": 0 if source else len(row["candidate_edges"]),
        "raw_mutual_edges_removed": len(row["raw_mutual_edges"]) if source else 0,
        "raw_mutual_edges_added": 0 if source else len(row["raw_mutual_edges"]),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
