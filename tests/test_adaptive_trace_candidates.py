from __future__ import annotations

import csv
import json

from uboot.adaptive_trace_candidates import load_trace_candidates


def test_candidate_selection_preserves_four_strata_locators_and_source_rows(
    tmp_path,
) -> None:
    slices = [
        _slice("a", "0", "0|1", 10),
        _slice("b", "0", "0|1", 20),
        _slice("c", "", "2|22", 10),
        _slice("d", "", "3|22", 30),
        _slice("e", "7", "4|7", 10),
        _slice("f", "7", "5|7", 40),
        _slice("g", "", "6|8", 10),
        _slice("h", "", "8|9", 50),
    ]
    transformations = [
        _edge("a", "b", "MEMBERS_SAME_RELATION_DIFFERENT", "0|1", "0|1", "0|1"),
        _edge("c", "d", "BODY_SAME_NEIGHBOR_DIFFERENT", "22", "2|22", "3|22"),
        _edge("e", "f", "BODY_SAME_NEIGHBOR_DIFFERENT", "7", "4|7", "5|7"),
        _edge("g", "h", "ONE_MEMBER_OVERLAP_ROLE_CHANGED", "8", "6|8", "8|9"),
    ]
    _write_csv(tmp_path / "relation_slices.csv", slices)
    _write_csv(tmp_path / "slice_transformations.csv", transformations)
    _write_csv(tmp_path / "slice_transformations_diagnostic_sample.csv", transformations)
    (tmp_path / "observer_verification.json").write_text(
        json.dumps({"tracked_anchor_ids": [7]}), encoding="utf-8"
    )
    candidates = load_trace_candidates(
        tmp_path / "slice_transformations_diagnostic_sample.csv",
        candidate_count=4,
        seed=1,
    )
    assert {candidate.selection_group for candidate in candidates} == {
        "T3_SAME_RAW_PAIR",
        "NONTRACKED_RAW_REPEATED",
        "TRACKED_ANCHOR_NEIGHBOR_CHANGE",
        "ONE_MEMBER_ONLY_CONTROL",
    }
    assert all(len(set(candidate.coarse_locators)) == 2 for candidate in candidates)
    assert {candidate.source_row_number for candidate in candidates} == {2, 3, 4, 5}


def _slice(slice_id: str, anchor: str, members: str, locator: int) -> dict[str, str]:
    left, right = members.split("|")
    return {
        "slice_id": slice_id,
        "anchor_id": anchor,
        "left_raw_id": left,
        "right_raw_id": right,
        "observation_locator": json.dumps(
            [json.dumps({"atom_step": locator, "snapshot": locator})]
        ),
    }


def _edge(
    left: str,
    right: str,
    kind: str,
    shared: str,
    left_members: str,
    right_members: str,
) -> dict[str, str]:
    return {
        "slice_a_id": left,
        "slice_b_id": right,
        "transformation_type": kind,
        "shared_raw_ids": shared,
        "slice_a_raw_members": left_members,
        "slice_b_raw_members": right_members,
    }


def _write_csv(path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
