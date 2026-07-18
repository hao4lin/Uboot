"""Select bounded adaptive-replay candidates from coarse observer artifacts."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
from random import Random
from typing import Any, Iterable

from uboot.adaptive_relation_tracer import TraceCandidate


PRIORITY_NONANCHOR_RAW_IDS = (22, 38, 43, 55, 58, 89)


def load_trace_candidates(
    candidate_source: Path,
    *,
    candidate_count: int,
    seed: int,
) -> tuple[TraceCandidate, ...]:
    """Select at most five rows from each requested evidence stratum."""
    source = candidate_source.resolve()
    if source.is_dir():
        source = source / "slice_transformations_diagnostic_sample.csv"
    if not source.exists():
        alternative = source.parent / "slice_transformations_diagnostic_sample.csv"
        if alternative.exists():
            source = alternative
        else:
            raise FileNotFoundError(source)
    artifact_dir = source.parent
    diagnostic = _read_csv(source)
    slices = {
        row["slice_id"]: row
        for row in _read_csv(artifact_dir / "relation_slices.csv")
    }
    verification = json.loads(
        (artifact_dir / "observer_verification.json").read_text(encoding="utf-8")
    )
    anchors = frozenset(map(int, verification["tracked_anchor_ids"]))
    source_rows = {
        (row["slice_a_id"], row["slice_b_id"]): line_number
        for line_number, row in enumerate(
            _read_csv(artifact_dir / "slice_transformations.csv"), start=2
        )
    }
    unique = {}
    for row in diagnostic:
        unique.setdefault((row["slice_a_id"], row["slice_b_id"]), row)
    rows = list(unique.values())
    selected: list[tuple[str, dict[str, str], tuple[int, ...], str]] = []
    used: set[tuple[str, str]] = set()

    def add_group(
        group: str,
        candidates: Iterable[dict[str, str]],
        query_builder: Any,
        *,
        limit: int = 5,
        preserve_order: bool = False,
    ) -> None:
        available = [row for row in candidates if _key(row) not in used]
        if not preserve_order:
            Random(_derived_seed(seed, group)).shuffle(available)
        available.sort(
            key=lambda row: len(
                set(_coarse_locator_pair(slices[row["slice_a_id"]], slices[row["slice_b_id"]]))
            )
            < 2
        )
        for row in available[:limit]:
            query_ids, query_type = query_builder(row)
            selected.append((group, row, query_ids, query_type))
            used.add(_key(row))

    add_group(
        "T3_SAME_RAW_PAIR",
        (
            row
            for row in rows
            if row["transformation_type"] == "MEMBERS_SAME_RELATION_DIFFERENT"
        ),
        lambda row: (_raw_members(row, "slice_a_raw_members"), "pair"),
    )
    priority_rank = {raw_id: index for index, raw_id in enumerate(PRIORITY_NONANCHOR_RAW_IDS)}
    nonanchor = [
        row
        for row in rows
        if _ids(row["shared_raw_ids"]).isdisjoint(anchors)
        and _ids(row["shared_raw_ids"]) & set(PRIORITY_NONANCHOR_RAW_IDS)
    ]
    nonanchor.sort(
        key=lambda row: (
            min(priority_rank[item] for item in _ids(row["shared_raw_ids"]) if item in priority_rank),
            row["slice_a_id"],
            row["slice_b_id"],
        )
    )
    add_group(
        "NONTRACKED_RAW_REPEATED",
        nonanchor,
        lambda row: ((min(_ids(row["shared_raw_ids"])),), "anchor_neighborhood"),
        preserve_order=True,
    )
    add_group(
        "TRACKED_ANCHOR_NEIGHBOR_CHANGE",
        (
            row
            for row in rows
            if row["transformation_type"] == "BODY_SAME_NEIGHBOR_DIFFERENT"
            and _ids(row["shared_raw_ids"]) & anchors
        ),
        lambda row: (
            (min(_ids(row["shared_raw_ids"]) & anchors),),
            "anchor_neighborhood",
        ),
    )
    add_group(
        "ONE_MEMBER_ONLY_CONTROL",
        (
            row
            for row in rows
            if row["transformation_type"] == "ONE_MEMBER_OVERLAP_ROLE_CHANGED"
        ),
        lambda row: ((min(_ids(row["shared_raw_ids"])),), "anchor_neighborhood"),
    )

    candidates = []
    for index, (group, row, query_ids, query_type) in enumerate(
        selected[:candidate_count], start=1
    ):
        slice_a = slices[row["slice_a_id"]]
        slice_b = slices[row["slice_b_id"]]
        locators = _coarse_locator_pair(slice_a, slice_b)
        involved = tuple(
            sorted(
                set(_raw_members(row, "slice_a_raw_members"))
                | set(_raw_members(row, "slice_b_raw_members"))
            )
        )
        candidates.append(
            TraceCandidate(
                candidate_id=f"C{index:02d}",
                involved_raw_ids=involved,
                query_raw_ids=query_ids,
                coarse_locators=locators,
                query_type=query_type,
                expected_content_difference=row["transformation_type"],
                source_artifact=str(source),
                source_row_number=source_rows[_key(row)],
                slice_a_id=row["slice_a_id"],
                slice_b_id=row["slice_b_id"],
                selection_group=group,
            )
        )
    return tuple(candidates)


def _coarse_locator_pair(
    slice_a: dict[str, str], slice_b: dict[str, str]
) -> tuple[int, ...]:
    left = _observation_locators(slice_a["observation_locator"])
    right = _observation_locators(slice_b["observation_locator"])
    distinct = sorted(
        (
            (abs(a - b), min(a, b), max(a, b))
            for a in left
            for b in right
            if a != b
        )
    )
    if distinct:
        return distinct[0][1], distinct[0][2]
    common = sorted(set(left) | set(right))
    return (common[0], common[0]) if common else ()


def _observation_locators(value: str) -> tuple[int, ...]:
    decoded = json.loads(value)
    return tuple(
        sorted(
            {
                int(json.loads(item)["atom_step"])
                if isinstance(item, str)
                else int(item["atom_step"])
                for item in decoded
            }
        )
    )


def _raw_members(row: dict[str, str], field: str) -> tuple[int, ...]:
    return tuple(sorted(_ids(row[field])))


def _ids(value: str) -> frozenset[int]:
    return frozenset(int(item) for item in value.split("|") if item)


def _key(row: dict[str, str]) -> tuple[str, str]:
    return row["slice_a_id"], row["slice_b_id"]


def _derived_seed(seed: int, group: str) -> int:
    return int.from_bytes(
        sha256(f"{seed}:{group}".encode()).digest()[:8], "big"
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
