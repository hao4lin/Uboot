from __future__ import annotations

import csv
import json

from uboot.motion_reclassification import reclassify_existing_motion_candidates


def test_missing_old_context_is_marked_insufficient(tmp_path) -> None:
    _write(
        tmp_path / "motion_candidates.csv",
        [{"candidate_id": "C01", "commit_locator": "10", "preserved_internal_relations": ""}],
    )
    _write(tmp_path / "localized_commits.csv", [{"candidate_id": "C02", "commit_locator": "11"}])
    _write(
        tmp_path / "generated_three_member_slices.csv",
        [{"candidate_id": "C01", "commit_locator": "10", "side": "before"}],
    )
    rows = reclassify_existing_motion_candidates(
        tmp_path / "motion_candidates.csv",
        tmp_path / "localized_commits.csv",
        tmp_path / "generated_three_member_slices.csv",
    )
    assert rows[0]["v2_reclassification"] == "INSUFFICIENT_SAVED_CONTEXT"


def test_saved_source_sibling_is_reclassified_trivial(tmp_path) -> None:
    edge = json.dumps(
        {"source_raw_id": 0, "slot_semantic": 1, "target_raw_id": 3},
        sort_keys=True,
        separators=(",", ":"),
    )
    _write(
        tmp_path / "motion_candidates.csv",
        [{
            "candidate_id": "C01",
            "commit_locator": "10",
            "preserved_member_ids": "0|3",
            "preserved_internal_relations": edge,
        }],
    )
    _write(
        tmp_path / "localized_commits.csv",
        [{"candidate_id": "C01", "commit_locator": "10", "source_raw_id": "0", "slot_semantic": "0"}],
    )
    _write(
        tmp_path / "generated_three_member_slices.csv",
        [
            {"candidate_id": "C01", "commit_locator": "10", "side": "before"},
            {"candidate_id": "C01", "commit_locator": "10", "side": "after"},
        ],
    )
    rows = reclassify_existing_motion_candidates(
        tmp_path / "motion_candidates.csv",
        tmp_path / "localized_commits.csv",
        tmp_path / "generated_three_member_slices.csv",
    )
    assert rows[0]["v2_reclassification"] == "TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION"


def _write(path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
