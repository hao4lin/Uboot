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
    include_singletons: bool = True,
) -> list[dict[str, Any]]:
    pairs = same_meaning_mutual_pairs(network)
    rows = []
    for index, item in enumerate(
        candidate_objects(candidate_graph, include_singletons=include_singletons)
    ):
        members = frozenset(item.members)
        incoming, outgoing = tentacle_counts_by_meaning(network, members)
        inline = len(members) <= member_inline_limit
        serialized = "|".join(map(str, item.members))
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
                "internal_candidate_edge_count": item.internal_candidate_edges,
                "outgoing_candidate_edge_count": item.outgoing_candidate_edges,
                "incoming_raw_tentacles_meaning_1": incoming[0],
                "incoming_raw_tentacles_meaning_2": incoming[1],
                "incoming_raw_tentacles_meaning_3": incoming[2],
                "outgoing_raw_tentacles_meaning_1": outgoing[0],
                "outgoing_raw_tentacles_meaning_2": outgoing[1],
                "outgoing_raw_tentacles_meaning_3": outgoing[2],
                "internal_mutual_pair_count": sum(
                    left in members and right in members for left, right, _ in pairs
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
        "mean_completed_lifetime_ticks": engine_summary["mean_completed_lifetime_ticks"],
        "mutual_pair_count": engine_summary["mutual_pair_count"],
        "mutual_pair_density": engine_summary["mutual_pair_density"],
        "mutual_endpoint_count": engine_summary["mutual_endpoint_count"],
        "mutual_endpoint_density": engine_summary["mutual_endpoint_density"],
        "candidate_size_0": sum(size == 0 for size in sizes),
        "candidate_size_1": sum(size == 1 for size in sizes),
        "candidate_size_2": sum(size == 2 for size in sizes),
        "candidate_size_ge3": sum(size >= 3 for size in sizes),
        "candidate_edge_count": sum(sizes),
        "candidate_scc_count": len(all_sccs),
        "closed_candidate_scc_count": len(closed),
        "closed_singleton_count": classes["singleton"],
        "closed_pair_count": classes["pair"],
        "closed_triple_count": classes["triple"],
        "closed_larger_count": classes["larger"],
        "max_candidate_scc_size": max(map(len, all_sccs), default=0),
        "max_closed_candidate_scc_size": max(
            (len(item.members) for item in closed), default=0
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
                relation = "persist" if position == 0 else "ambiguous"
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
        for row in births:
            transitions[("none", row["object_class"])] += 1
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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
