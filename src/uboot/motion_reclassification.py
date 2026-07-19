"""Conservative compatibility analysis for first-round motion CSV files."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def reclassify_existing_motion_candidates(
    motion_path: Path,
    localized_path: Path,
    generated_path: Path,
) -> tuple[dict[str, Any], ...]:
    """Reclassify only evidence actually saved by v1; never invent context."""
    motions = _read_csv(motion_path)
    localized = {
        (row["candidate_id"], row["commit_locator"]): row
        for row in _read_csv(localized_path)
    }
    generated = {
        (row["candidate_id"], row["commit_locator"], row["side"])
        for row in _read_csv(generated_path)
    }
    rows = []
    for motion in motions:
        key = (motion.get("candidate_id", ""), motion.get("commit_locator", ""))
        commit = localized.get(key)
        has_slices = all((*key, side) in generated for side in ("before", "after"))
        classification, reason = _classify_saved(motion, commit, has_slices)
        rows.append(
            {
                **motion,
                "v2_reclassification": classification,
                "v2_reclassification_reason": reason,
            }
        )
    return tuple(rows)


def write_reclassified_csv(path: Path, rows: tuple[dict[str, Any], ...]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if columns:
            writer.writeheader()
            writer.writerows(rows)


def _classify_saved(
    motion: dict[str, str],
    commit: dict[str, str] | None,
    has_slices: bool,
) -> tuple[str, str]:
    if commit is None or not has_slices:
        return "INSUFFICIENT_SAVED_CONTEXT", "missing localized commit or slice side"
    try:
        source = int(commit["source_raw_id"])
        changed_slot = int(commit["slot_semantic"])
        relations = tuple(
            json.loads(item)
            for item in motion.get("preserved_internal_relations", "").split("|")
            if item
        )
        preserved_members = {
            int(item)
            for item in motion.get("preserved_member_ids", "").split("|")
            if item
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return "INSUFFICIENT_SAVED_CONTEXT", "saved fields cannot be decoded"
    if not relations:
        if len(preserved_members) <= 1:
            return "INSUFFICIENT_SAVED_CONTEXT", "no saved third-party relation"
        return "INSUFFICIENT_SAVED_CONTEXT", "relation-free overlap is ambiguous"
    trivial = all(
        int(edge["source_raw_id"]) == source
        and int(edge["slot_semantic"]) != changed_slot
        for edge in relations
    )
    if trivial:
        return (
            "TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION",
            "all saved support is an unmodified sibling slot of the commit source",
        )
    independent_sources = {
        int(edge["source_raw_id"])
        for edge in relations
        if int(edge["source_raw_id"]) != source
    }
    if len(independent_sources) >= 2 or len(relations) >= 2:
        return "STRONG_RAW_SUPPORT", "saved raw relations meet multi-edge/source minimum"
    if independent_sources:
        return "WEAK_NONTRIVIAL", "one saved independent-source relation"
    return "INSUFFICIENT_SAVED_CONTEXT", "saved local slice cannot resolve support role"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
