"""Reproducible diagnostic samples from relation-transformation CSV rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from random import Random
from typing import Any, Iterable


DIAGNOSTIC_FIELDNAMES = (
    "sample_group",
    "sample_reason",
    "group_population_count",
    "stratum_population_count",
    "random_seed",
    "focus_slice_id",
    "focus_slice_degree",
    "slice_a_id",
    "slice_b_id",
    "anchor_id",
    "transformation_type",
    "slice_a_relation_kind",
    "slice_b_relation_kind",
    "slice_a_slot_semantics",
    "slice_b_slot_semantics",
    "slice_a_raw_members",
    "slice_b_raw_members",
    "shared_raw_ids",
    "shared_member_roles",
    "changed_raw_ids",
)


def build_diagnostic_sample(
    slice_rows: Iterable[dict[str, str]],
    transformation_rows: Iterable[dict[str, str]],
    tracked_anchor_ids: Iterable[int],
    *,
    seed: int,
    per_group_limit: int = 30,
) -> tuple[dict[str, Any], ...]:
    """Build four bounded groups without using CSV row order as evidence."""
    if per_group_limit < 1:
        raise ValueError("per-group limit must be positive")
    slices = {row["slice_id"]: row for row in slice_rows}
    edges = sorted(transformation_rows, key=_edge_key)
    anchors = frozenset(tracked_anchor_ids)
    for edge in edges:
        for slice_id in (edge["slice_a_id"], edge["slice_b_id"]):
            if slice_id not in slices:
                raise ValueError(f"transformation references unknown slice {slice_id}")

    output = []
    output.extend(_anchor_type_sample(slices, edges, anchors, seed, per_group_limit))
    output.extend(_high_degree_sample(slices, edges, seed, per_group_limit))
    output.extend(_nonanchor_shared_sample(slices, edges, anchors, seed, per_group_limit))
    output.extend(_members_same_all(slices, edges, per_group_limit))
    return tuple(output)


def _anchor_type_sample(
    slices: dict[str, dict[str, str]],
    edges: list[dict[str, str]],
    anchors: frozenset[int],
    seed: int,
    limit: int,
) -> list[dict[str, Any]]:
    strata: dict[tuple[int, str], list[dict[str, str]]] = defaultdict(list)
    for edge in edges:
        for anchor in sorted(_edge_anchor_ids(edge, slices) & anchors):
            strata[(anchor, edge["transformation_type"])].append(edge)
    group_population = sum(map(len, strata.values()))
    queues = {}
    for key, candidates in sorted(strata.items()):
        queue = list(candidates)
        Random(_derived_seed(seed, "anchor-type", *key)).shuffle(queue)
        queues[key] = queue
    selected: list[dict[str, Any]] = []
    round_index = 0
    while len(selected) < limit:
        added = False
        for key in sorted(queues):
            if round_index >= len(queues[key]):
                continue
            anchor, transformation_type = key
            selected.append(
                _enrich(
                    queues[key][round_index],
                    slices,
                    sample_group="1_anchor_type_random",
                    sample_reason=(
                        f"edge touches anchor {anchor}; seeded random within "
                        f"{transformation_type} stratum"
                    ),
                    group_population_count=group_population,
                    stratum_population_count=len(queues[key]),
                    random_seed=seed,
                    anchor_id=str(anchor),
                )
            )
            added = True
            if len(selected) == limit:
                break
        if not added:
            break
        round_index += 1
    return selected


def _high_degree_sample(
    slices: dict[str, dict[str, str]],
    edges: list[dict[str, str]],
    seed: int,
    limit: int,
) -> list[dict[str, Any]]:
    degree: Counter[str] = Counter()
    incident: dict[str, list[dict[str, str]]] = defaultdict(list)
    for edge in edges:
        for slice_id in (edge["slice_a_id"], edge["slice_b_id"]):
            degree[slice_id] += 1
            incident[slice_id].append(edge)
    top_slices = sorted(degree, key=lambda item: (-degree[item], item))[:10]
    queues = {}
    for slice_id in top_slices:
        queue = sorted(incident[slice_id], key=_edge_key)
        Random(_derived_seed(seed, "high-degree", slice_id)).shuffle(queue)
        queues[slice_id] = queue
    group_population = sum(len(queue) for queue in queues.values())
    selected: list[dict[str, Any]] = []
    round_index = 0
    while len(selected) < limit:
        added = False
        for slice_id in top_slices:
            queue = queues[slice_id]
            if round_index >= len(queue):
                continue
            selected.append(
                _enrich(
                    queue[round_index],
                    slices,
                    sample_group="2_high_degree_slice_edges",
                    sample_reason="seeded neighbor edge from a top-10 degree slice",
                    group_population_count=group_population,
                    stratum_population_count=len(queue),
                    random_seed=seed,
                    focus_slice_id=slice_id,
                    focus_slice_degree=degree[slice_id],
                )
            )
            added = True
            if len(selected) == limit:
                break
        if not added:
            break
        round_index += 1
    return selected


def _nonanchor_shared_sample(
    slices: dict[str, dict[str, str]],
    edges: list[dict[str, str]],
    anchors: frozenset[int],
    seed: int,
    limit: int,
) -> list[dict[str, Any]]:
    candidates = [
        edge for edge in edges if _ids(edge["shared_raw_ids"]).isdisjoint(anchors)
    ]
    selected = list(candidates)
    if len(selected) > limit:
        selected = Random(_derived_seed(seed, "nonanchor-shared")).sample(
            selected, limit
        )
        selected.sort(key=_edge_key)
    return [
        _enrich(
            edge,
            slices,
            sample_group="3_shared_raw_excludes_tracked_anchors",
            sample_reason=(
                "shared_raw_ids contains no tracked anchor; bounded seeded sample"
                if len(candidates) > limit
                else "shared_raw_ids contains no tracked anchor; complete group"
            ),
            group_population_count=len(candidates),
            stratum_population_count=len(candidates),
            random_seed=seed,
        )
        for edge in selected
    ]


def _members_same_all(
    slices: dict[str, dict[str, str]],
    edges: list[dict[str, str]],
    limit: int,
) -> list[dict[str, Any]]:
    candidates = [
        edge
        for edge in edges
        if edge["transformation_type"] == "MEMBERS_SAME_RELATION_DIFFERENT"
    ]
    if len(candidates) > limit:
        raise ValueError(
            "MEMBERS_SAME_RELATION_DIFFERENT group exceeds the requested limit; "
            "increase --per-group-limit to preserve the complete group"
        )
    return [
        _enrich(
            edge,
            slices,
            sample_group="4_members_same_relation_different_all",
            sample_reason="complete MEMBERS_SAME_RELATION_DIFFERENT group",
            group_population_count=len(candidates),
            stratum_population_count=len(candidates),
            random_seed="",
        )
        for edge in candidates
    ]


def _enrich(
    edge: dict[str, str],
    slices: dict[str, dict[str, str]],
    *,
    sample_group: str,
    sample_reason: str,
    group_population_count: int,
    stratum_population_count: int,
    random_seed: int | str,
    anchor_id: str | None = None,
    focus_slice_id: str = "",
    focus_slice_degree: int | str = "",
) -> dict[str, Any]:
    slice_a = slices[edge["slice_a_id"]]
    slice_b = slices[edge["slice_b_id"]]
    edge_anchors = _edge_anchor_ids(edge, slices)
    role_mapping = json.loads(edge["role_mapping"])
    member_roles = [
        {
            "raw_id": raw_id,
            "slice_a_role": role_a,
            "slice_b_role": role_b,
        }
        for raw_id, role_a, role_b in role_mapping
    ]
    return {
        "sample_group": sample_group,
        "sample_reason": sample_reason,
        "group_population_count": group_population_count,
        "stratum_population_count": stratum_population_count,
        "random_seed": random_seed,
        "focus_slice_id": focus_slice_id,
        "focus_slice_degree": focus_slice_degree,
        "slice_a_id": edge["slice_a_id"],
        "slice_b_id": edge["slice_b_id"],
        "anchor_id": anchor_id
        if anchor_id is not None
        else "|".join(map(str, sorted(edge_anchors))),
        "transformation_type": edge["transformation_type"],
        "slice_a_relation_kind": slice_a["relation_kind"],
        "slice_b_relation_kind": slice_b["relation_kind"],
        "slice_a_slot_semantics": slice_a["slot_semantics"],
        "slice_b_slot_semantics": slice_b["slot_semantics"],
        "slice_a_raw_members": _raw_members(slice_a),
        "slice_b_raw_members": _raw_members(slice_b),
        "shared_raw_ids": edge["shared_raw_ids"],
        "shared_member_roles": json.dumps(member_roles, separators=(",", ":")),
        "changed_raw_ids": edge["changed_raw_ids"],
    }


def _edge_anchor_ids(
    edge: dict[str, str], slices: dict[str, dict[str, str]]
) -> frozenset[int]:
    return _ids(slices[edge["slice_a_id"]]["anchor_id"]) | _ids(
        slices[edge["slice_b_id"]]["anchor_id"]
    )


def _raw_members(row: dict[str, str]) -> str:
    return "|".join(
        map(str, sorted((int(row["left_raw_id"]), int(row["right_raw_id"]))))
    )


def _ids(value: str) -> frozenset[int]:
    return frozenset(int(item) for item in value.split("|") if item)


def _edge_key(edge: dict[str, str]) -> tuple[str, str]:
    return edge["slice_a_id"], edge["slice_b_id"]


def _derived_seed(seed: int, *parts: object) -> int:
    payload = json.dumps((seed, *parts), separators=(",", ":"))
    return int.from_bytes(sha256(payload.encode()).digest()[:8], "big")
