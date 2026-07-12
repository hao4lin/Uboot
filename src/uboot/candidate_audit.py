"""Offline definitions and mutual-to-candidate diagnostics for M1."""

from __future__ import annotations

from collections import Counter
from typing import Any

from uboot.candidate_graph import direct_candidate_graph, incoming_index
from uboot.kernel import RawNetwork
from uboot.snapshot_lineage import same_meaning_mutual_pairs


def relation_sets(network: RawNetwork, node: int) -> dict[str, tuple[int, ...]]:
    incoming = incoming_index(network)
    direct_in = set().union(*(incoming[slot][node] for slot in range(3)))
    direct_out = set(network.targets[node])
    out2 = {
        target
        for neighbor in network.targets[node]
        for target in network.targets[neighbor]
        if target != node
    }
    persistent = set(direct_candidate_graph(network)[node])
    return {
        "direct_IN": tuple(direct_in),
        "direct_OUT": tuple(direct_out),
        "OUT2": tuple(out2),
        "persistent_candidate_relation": tuple(persistent),
        "selection_pool": tuple(persistent),
    }


def diagnose_mutual_candidates(
    network: RawNetwork, *, sample_limit: int = 20
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    graph = direct_candidate_graph(network)
    pairs = sorted(same_meaning_mutual_pairs(network))
    relations: Counter[str] = Counter()
    rows = []
    for left, right, left_slot in pairs:
        right_slot = next(
            slot
            for slot, target in enumerate(network.targets[right])
            if target == left and slot == left_slot
        )
        right_in_left = right in graph[left]
        left_in_right = left in graph[right]
        if right_in_left and left_in_right:
            relation = "two_cycle"
        elif right_in_left:
            relation = "one_way_A_to_B"
        elif left_in_right:
            relation = "one_way_B_to_A"
        else:
            relation = "disconnected"
        relations[relation] += 1
        if len(rows) < sample_limit:
            left_sets = relation_sets(network, left)
            right_sets = relation_sets(network, right)
            rows.append(
                {
                    "A": left,
                    "B": right,
                    "A_to_B_slot_meaning": left_slot + 1,
                    "B_to_A_slot_meaning": right_slot + 1,
                    **{f"{key}_A": _compact(value) for key, value in left_sets.items()},
                    **{f"{key}_B": _compact(value) for key, value in right_sets.items()},
                    "B_in_candidate_A": right_in_left,
                    "A_in_candidate_B": left_in_right,
                    "candidate_relation_type": relation,
                }
            )
    summary = {
        "mutual_pair_count": len(pairs),
        "mutual_pairs_forming_candidate_two_cycle": relations["two_cycle"],
        "mutual_pairs_forming_one_way_candidate": (
            relations["one_way_A_to_B"] + relations["one_way_B_to_A"]
        ),
        "mutual_pairs_candidate_disconnected": relations["disconnected"],
    }
    return rows, summary


def _compact(values: tuple[int, ...]) -> str:
    return "|".join(map(str, values))
