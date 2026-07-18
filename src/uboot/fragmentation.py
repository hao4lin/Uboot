"""Checkpoint-only candidate fragmentation metrics."""

from __future__ import annotations

from math import ceil
from statistics import fmean, median
from typing import Any

from uboot.candidate_graph import candidate_objects, strongly_connected_components
from uboot.kernel import RawNetwork
from uboot.observables import mutual_pairs
from uboot.snapshot_lineage import same_meaning_mutual_pairs, tentacle_counts


def giant_state(fraction: float) -> str:
    if fraction > 0.8:
        return "whole_or_near_whole"
    if fraction > 0.5:
        return "giant"
    if fraction > 0.2:
        return "intermediate"
    return "fragmented"


def first_sustained_fragmented_index(fractions: list[float]) -> int | None:
    consecutive = 0
    for index, fraction in enumerate(fractions):
        consecutive = consecutive + 1 if fraction <= 0.2 else 0
        if consecutive >= 3:
            return index
    return None


def fragmentation_metrics(
    network: RawNetwork, graph: list[tuple[int, ...]]
) -> tuple[dict[str, Any], list[int], frozenset[int]]:
    components = strongly_connected_components(graph)
    components.sort(key=lambda members: (-len(members), tuple(sorted(members))))
    sizes = sorted(len(component) for component in components)
    giant = frozenset(components[0]) if components else frozenset()
    closed = candidate_objects(graph)
    closed_nontrivial = [item for item in closed if len(item.members) >= 2]
    closed_sizes = [len(item.members) for item in closed_nontrivial]
    candidate_sizes = sorted(map(len, graph))
    raw_pairs = mutual_pairs(network)
    same_pairs = {
        (left, right) for left, right, _ in same_meaning_mutual_pairs(network)
    }
    candidate_pairs = {
        (left, right)
        for left, targets in enumerate(graph)
        for right in targets
        if left < right and left in graph[right]
    }
    giant_edges = sum(
        target in giant for source in giant for target in graph[source]
    )
    giant_raw = sum(left in giant and right in giant for left, right in raw_pairs)
    giant_same = sum(left in giant and right in giant for left, right in same_pairs)
    incoming_tentacles, outgoing_tentacles = tentacle_counts(network, giant)
    largest = len(giant)
    largest_closed = max(closed_sizes, default=0)
    result = {
        "raw_target_uniqueness_violation_count": sum(
            len(set(row)) != len(row) for row in network.targets
        ),
        "duplicate_slot_ratio": sum(
            len(row) - len(set(row)) for row in network.targets
        )
        / (3 * network.size),
        "largest_candidate_scc_size": largest,
        "largest_candidate_scc_fraction": largest / network.size,
        "largest_closed_scc_size": largest_closed,
        "largest_closed_scc_fraction": largest_closed / network.size,
        "giant_state": giant_state(largest / network.size),
        "candidate_directed_edge_count": sum(candidate_sizes),
        "candidate_edges_per_node": sum(candidate_sizes) / network.size,
        "candidate_mean_out_degree": fmean(candidate_sizes),
        "candidate_median_out_degree": median(candidate_sizes),
        "candidate_p90_out_degree": _quantile(candidate_sizes, 0.9),
        "candidate_max_out_degree": max(candidate_sizes, default=0),
        "candidate_size_0": sum(size == 0 for size in candidate_sizes),
        "candidate_size_1": sum(size == 1 for size in candidate_sizes),
        "candidate_size_2": sum(size == 2 for size in candidate_sizes),
        "candidate_size_3_to_5": sum(3 <= size <= 5 for size in candidate_sizes),
        "candidate_size_6_to_10": sum(6 <= size <= 10 for size in candidate_sizes),
        "candidate_size_gt_10": sum(size > 10 for size in candidate_sizes),
        "candidate_scc_count": len(sizes),
        "candidate_scc_singleton_count": sum(size == 1 for size in sizes),
        "candidate_scc_size_2_count": sum(size == 2 for size in sizes),
        "candidate_scc_size_3_count": sum(size == 3 for size in sizes),
        "candidate_scc_size_4_to_10_count": sum(4 <= size <= 10 for size in sizes),
        "candidate_scc_size_11_to_100_count": sum(11 <= size <= 100 for size in sizes),
        "candidate_scc_size_gt_100_count": sum(size > 100 for size in sizes),
        "candidate_scc_size_p50": _quantile(sizes, 0.5),
        "candidate_scc_size_p90": _quantile(sizes, 0.9),
        "candidate_scc_size_p99": _quantile(sizes, 0.99),
        "closed_pair_count": sum(size == 2 for size in closed_sizes),
        "closed_triple_count": sum(size == 3 for size in closed_sizes),
        "closed_size_4_to_10_count": sum(4 <= size <= 10 for size in closed_sizes),
        "closed_size_11_to_100_count": sum(11 <= size <= 100 for size in closed_sizes),
        "closed_size_gt_100_count": sum(size > 100 for size in closed_sizes),
        "closed_object_node_fraction": sum(closed_sizes) / network.size,
        "all_raw_mutual_pair_count": len(raw_pairs),
        "same_meaning_mutual_pair_count": len(same_pairs),
        "candidate_two_cycle_count": len(candidate_pairs),
        "raw_mutual_and_candidate_two_cycle_count": len(raw_pairs & candidate_pairs),
        "raw_mutual_pairs_per_node": len(raw_pairs) / network.size,
        "candidate_two_cycles_per_node": len(candidate_pairs) / network.size,
        "giant_internal_candidate_edges": giant_edges,
        "giant_candidate_edges_per_node": giant_edges / max(1, largest),
        "giant_internal_raw_mutual_pairs": giant_raw,
        "giant_internal_same_meaning_mutual_pairs": giant_same,
        "giant_raw_mutual_pairs_per_node": giant_raw / max(1, largest),
        "giant_incoming_raw_tentacles": incoming_tentacles,
        "giant_outgoing_raw_tentacles": outgoing_tentacles,
    }
    return result, sorted(sizes, reverse=True)[:10], giant


def _quantile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    return values[ceil(quantile * len(values)) - 1]
