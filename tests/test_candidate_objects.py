import csv
from pathlib import Path
import subprocess
import sys

from uboot.candidate_graph import (
    candidate_objects,
    get_current_candidate_targets,
    incoming_index,
)
from uboot.candidate_audit import diagnose_mutual_candidates
from uboot.kernel import RawNetwork
from uboot.snapshot_lineage import (
    closed_object_rows,
    layered_topology_stats,
    tentacle_counts_by_meaning,
)


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


def test_endogenous_rule_includes_mutual_endpoints() -> None:
    network = RawNetwork(((1, 2, 3), (0, 2, 3), (0, 1, 3), (0, 1, 2)))
    incoming = incoming_index(network)

    assert 1 in get_current_candidate_targets(
        network.targets, incoming, 0, rule="endogenous_in_out2"
    )
    assert 0 in get_current_candidate_targets(
        network.targets, incoming, 1, rule="endogenous_in_out2"
    )


def test_out2_is_exactly_two_steps_without_transitive_expansion() -> None:
    network = RawNetwork(
        (
            (1, 4, 5),
            (2, 6, 7),
            (3, 6, 7),
            (1, 6, 7),
            (1, 6, 7),
            (1, 6, 7),
            (1, 4, 5),
            (1, 4, 5),
        )
    )
    candidates = get_current_candidate_targets(
        network.targets, incoming_index(network), 0, rule="endogenous_in_out2"
    )

    assert 2 in candidates
    assert 3 not in candidates


def test_mutual_meaning_layers_are_separate() -> None:
    rows = [tuple((node + offset) % 7 for offset in (1, 2, 3)) for node in range(7)]
    rows[1] = (2, 0, 4)
    network = RawNetwork(tuple(rows))  # type: ignore[arg-type]
    stats = layered_topology_stats(network, [tuple() for _ in rows])

    assert stats["all_mutual_pair_count"] == 1
    assert stats["same_meaning_mutual_pair_count"] == 0
    rows[1] = (0, 3, 4)
    same = layered_topology_stats(
        RawNetwork(tuple(rows)), [tuple() for _ in rows]  # type: ignore[arg-type]
    )
    assert same["same_meaning_mutual_pair_count"] == 1


def test_candidate_two_cycle_does_not_require_raw_mutual() -> None:
    rows = [tuple((node + offset) % 7 for offset in (1, 2, 3)) for node in range(7)]
    stats = layered_topology_stats(
        RawNetwork(tuple(rows)),  # type: ignore[arg-type]
        [(1,), (0,), tuple(), tuple(), tuple(), tuple(), tuple()],
    )

    assert stats["candidate_mutual_pair_count"] == 1
    assert stats["all_mutual_pair_count"] == 0


def test_raw_mutual_triangle_counted_once() -> None:
    rows = [tuple((node + offset) % 10 for offset in (1, 2, 3)) for node in range(10)]
    rows[0] = (1, 2, 3)
    rows[1] = (0, 2, 4)
    rows[2] = (0, 1, 5)
    stats = layered_topology_stats(
        RawNetwork(tuple(rows)), [tuple() for _ in rows]  # type: ignore[arg-type]
    )

    assert stats["raw_mutual_triangle_count"] == 1


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
