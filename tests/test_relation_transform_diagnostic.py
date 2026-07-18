from __future__ import annotations

import json

from uboot.relation_transform_diagnostic import build_diagnostic_sample


def test_diagnostic_groups_are_bounded_reproducible_and_content_joined() -> None:
    slices = [
        _slice("a", "0", 0, 10),
        _slice("b", "0", 0, 11),
        _slice("c", "1", 1, 10),
        _slice("d", "1", 1, 12, relation_kind="reciprocal_same_slot"),
    ]
    edges = [
        _edge("a", "b", "BODY_SAME_NEIGHBOR_DIFFERENT", "0", "10|11"),
        _edge("a", "c", "NEIGHBOR_SAME_BODY_DIFFERENT", "10", "0|1"),
        _edge("c", "d", "BODY_SAME_NEIGHBOR_DIFFERENT", "1", "10|12"),
        _edge("a", "d", "MEMBERS_SAME_RELATION_DIFFERENT", "10", "0|12"),
    ]
    first = build_diagnostic_sample(slices, edges, (0, 1), seed=9, per_group_limit=3)
    second = build_diagnostic_sample(slices, edges, (0, 1), seed=9, per_group_limit=3)
    assert first == second
    groups = _groups(first)
    assert all(len(rows) <= 3 for rows in groups.values())
    assert len(groups["3_shared_raw_excludes_tracked_anchors"]) == 2
    assert all(
        set(map(int, row["shared_raw_ids"].split("|"))).isdisjoint({0, 1})
        for row in groups["3_shared_raw_excludes_tracked_anchors"]
    )
    t3 = groups["4_members_same_relation_different_all"]
    assert len(t3) == 1
    assert t3[0]["slice_a_relation_kind"] == "directed"
    assert t3[0]["slice_b_relation_kind"] == "reciprocal_same_slot"
    roles = json.loads(t3[0]["shared_member_roles"])
    assert roles[0]["raw_id"] == 10


def test_complete_members_same_group_refuses_silent_truncation() -> None:
    slices = [
        _slice("a", "0", 0, 1),
        _slice("b", "0", 0, 2),
        _slice("c", "0", 0, 3),
    ]
    edges = [
        _edge("a", "b", "MEMBERS_SAME_RELATION_DIFFERENT", "0", "1|2"),
        _edge("a", "c", "MEMBERS_SAME_RELATION_DIFFERENT", "0", "1|3"),
    ]
    try:
        build_diagnostic_sample(slices, edges, (0,), seed=1, per_group_limit=1)
    except ValueError as error:
        assert "preserve the complete group" in str(error)
    else:
        raise AssertionError("complete T3 group was silently truncated")


def _slice(
    slice_id: str,
    anchor_id: str,
    left: int,
    right: int,
    *,
    relation_kind: str = "directed",
) -> dict[str, str]:
    return {
        "slice_id": slice_id,
        "anchor_id": anchor_id,
        "relation_kind": relation_kind,
        "slot_semantics": "0",
        "left_raw_id": str(left),
        "right_raw_id": str(right),
    }


def _edge(
    left: str,
    right: str,
    kind: str,
    shared: str,
    changed: str,
) -> dict[str, str]:
    shared_id = int(shared.split("|")[0])
    return {
        "slice_a_id": left,
        "slice_b_id": right,
        "transformation_type": kind,
        "shared_raw_ids": shared,
        "changed_raw_ids": changed,
        "role_mapping": json.dumps([[shared_id, "body", "body"]]),
    }


def _groups(rows: tuple[dict[str, object], ...]) -> dict[str, list[dict[str, object]]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(str(row["sample_group"]), []).append(row)
    return groups
