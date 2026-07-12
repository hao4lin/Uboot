import csv
from pathlib import Path
import subprocess
import sys

from uboot.candidate_graph import candidate_objects
from uboot.candidate_audit import diagnose_mutual_candidates
from uboot.kernel import RawNetwork
from uboot.snapshot_lineage import closed_object_rows, tentacle_counts_by_meaning


def test_candidate_graph_isolated_from_raw_slot_graph() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (2, 3, 4),
            (3, 4, 0),
            (4, 0, 1),
            (0, 1, 2),
        )
    )
    graph = [(1,), (0,), (3,), (4,), (2,)]

    rows = closed_object_rows(network, graph, 0, 0)

    assert [(row["object_class"], row["size"]) for row in rows] == [
        ("pair", 2),
        ("triple", 3),
    ]


def test_strong_component_with_outgoing_candidate_is_not_closed() -> None:
    graph = [(1,), (0, 2), tuple()]

    objects = candidate_objects(graph)

    assert not any(item.members == (0, 1) for item in objects)
    assert any(item.members == (2,) and item.closed for item in objects)
    assert next(item for item in objects if item.members == (2,)).object_class == (
        "candidate_isolated"
    )


def test_candidate_isolated_is_not_written_as_lineage_object() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 2, 3),
            (0, 1, 3),
            (0, 1, 2),
        )
    )
    graph = [tuple(), tuple(), tuple(), tuple()]

    assert closed_object_rows(network, graph, 0, 0) == []


def test_mutual_endpoints_are_removed_from_current_candidates() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 2, 3),
            (0, 1, 3),
            (0, 1, 2),
        )
    )

    rows, summary = diagnose_mutual_candidates(network)

    assert rows
    assert summary["mutual_pairs_forming_candidate_two_cycle"] == 0
    assert summary["mutual_pairs_forming_one_way_candidate"] == 0
    assert summary["mutual_pairs_candidate_disconnected"] == summary["mutual_pair_count"]


def test_tentacles_are_counted_once_and_by_meaning() -> None:
    network = RawNetwork(
        (
            (1, 2, 3),
            (0, 2, 4),
            (0, 1, 3),
            (4, 0, 1),
            (3, 1, 2),
        )
    )

    incoming, outgoing = tentacle_counts_by_meaning(network, frozenset({0, 1}))

    assert incoming == (1, 3, 1)
    assert outgoing == (0, 2, 2)


def test_final_snapshot_is_added_once_when_interval_does_not_divide_run(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    subprocess.run(
        [
            sys.executable,
            "experiments/edge_response.py",
            "--profile",
            "M1",
            "--N",
            "20",
            "--seed",
            "27",
            "--background-sweeps",
            "7",
            "--snapshot-interval-sweeps",
            "3",
            "--enable-snapshots",
            "--output-dir",
            str(output),
        ],
        check=True,
    )
    rows = list(csv.DictReader((output / "system_snapshots.csv").open()))
    ticks = [int(row["tick"]) for row in rows]

    assert ticks == [0, 180, 360, 420]
    assert len(ticks) == len(set(ticks))
