import csv

import pytest

from uboot.kernel import RawNetwork
from uboot.snapshot_lineage import (
    analyze_lineages,
    continuity_metrics,
    histogram_bucket,
    tentacle_counts,
)


@pytest.mark.parametrize(
    ("value", "bucket"),
    [(0, "0"), (5, "5"), (6, "6-7"), (15, "8-15"), (255, "128-255"), (256, "256+")],
)
def test_histogram_bucket(value: int, bucket: str) -> None:
    assert histogram_bucket(value) == bucket


def test_pair_and_triple_tentacle_counts() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 2, 4),
            (0, 1, 3),
            (4, 0, 1),
            (3, 1, 2),
        )
    )

    assert tentacle_counts(network, frozenset({0, 1})) == (5, 4)
    assert tentacle_counts(network, frozenset({0, 1, 2})) == (4, 3)


def test_continuity_metrics_keep_independent_measures() -> None:
    metrics = continuity_metrics(frozenset({1, 2}), frozenset({2, 3, 4}))

    assert metrics["shared_member_count"] == 1
    assert metrics["member_jaccard"] == 0.25
    assert metrics["source_member_retention"] == 0.5
    assert metrics["target_member_inheritance"] == pytest.approx(1 / 3)


def test_lineage_preserves_split_merge_birth_and_death(tmp_path) -> None:
    path = tmp_path / "objects.csv"
    rows = [
        [0, 0, 0, "pair", 2, "1|2"],
        [0, 0, 1, "pair", 2, "8|9"],
        [0, 0, 2, "pair", 2, "10|11"],
        [1, 1, 0, "pair", 2, "1|3"],
        [1, 1, 1, "pair", 2, "2|3"],
        [1, 1, 2, "pair", 2, "2|8"],
        [1, 1, 3, "pair", 2, "6|7"],
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["snapshot_id", "sweep", "local_object_index", "object_type", "member_count", "member_ids"]
        )
        writer.writerows(rows)

    summary = analyze_lineages(path, tmp_path / "analysis")
    matches = list(csv.DictReader((tmp_path / "analysis" / "object_lineages.csv").open()))

    assert summary["split_candidates"] == 1
    assert summary["merge_candidates"] == 1
    assert summary["unmatched_births"] >= 1
    assert any(row["member_ids"] == "6|7" for row in csv.DictReader(path.open()))
    assert {row["match_relation"] for row in matches} >= {
        "primary_match",
        "split_candidate",
        "merge_candidate",
    }
