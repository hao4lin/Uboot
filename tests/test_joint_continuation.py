import csv
from pathlib import Path

import pytest

from uboot.joint_continuation import (
    GodSlice,
    SnapshotSeries,
    track_joint_continuation,
)


def _slice(
    snapshot_id: int,
    *,
    pairs: tuple[tuple[int, int], ...] = (),
    triples: tuple[tuple[int, int, int], ...] = (),
) -> GodSlice:
    return GodSlice(snapshot_id, snapshot_id * 30, float(snapshot_id), pairs, triples)


def test_pair_and_triangle_retention_and_migration() -> None:
    series = SnapshotSeries(
        [
            _slice(0, pairs=((1, 2),), triples=((4, 5, 6),)),
            _slice(1, pairs=((1, 2),), triples=((4, 5, 6),)),
            _slice(2, pairs=((2, 3),), triples=((5, 6, 7),)),
        ]
    )
    result = track_joint_continuation(
        series, start_snapshot_id=0, pair_ids=(2, 1), triangle_ids=(6, 4, 5)
    )

    kept, moved = result.ascending.tracked_slices[1:]
    assert kept.pair_shared_ids == (1, 2)
    assert kept.triangle_shared_ids == (4, 5, 6)
    assert not kept.pair_changed and not kept.triangle_changed
    assert moved.pair_shared_ids == (2,)
    assert moved.triangle_shared_ids == (5, 6)
    assert moved.pair_changed and moved.triangle_changed


def test_nonmatching_intermediate_slices_are_collapsed_and_recursion_updates() -> None:
    series = SnapshotSeries(
        [
            _slice(0, pairs=((1, 2),), triples=((5, 6, 7),)),
            _slice(1, pairs=((1, 8),), triples=((20, 21, 22),)),
            _slice(2, pairs=((20, 21),), triples=((5, 6, 9),)),
            _slice(3, pairs=((2, 3),), triples=((6, 7, 8),)),
            _slice(4, pairs=((3, 4),), triples=((7, 8, 9),)),
        ]
    )
    result = track_joint_continuation(
        series, start_snapshot_id=0, pair_ids=(1, 2), triangle_ids=(5, 6, 7)
    )

    tracked = result.ascending.tracked_slices
    assert [item.snapshot_id for item in tracked] == [0, 3, 4]
    assert tracked[1].skipped_god_slices_since_previous == 2
    assert tracked[2].pair_shared_ids == (3,)
    assert tracked[2].triangle_shared_ids == (7, 8)


def test_pair_ambiguity_stops_direction() -> None:
    series = SnapshotSeries(
        [
            _slice(0, pairs=((1, 2),), triples=((5, 6, 7),)),
            _slice(
                1,
                pairs=((1, 3), (2, 4)),
                triples=((5, 6, 8),),
            ),
        ]
    )
    result = track_joint_continuation(
        series, start_snapshot_id=0, pair_ids=(1, 2), triangle_ids=(5, 6, 7)
    )

    assert result.ascending.stop_reason == "ambiguous_pair_match"
    assert result.ascending.ambiguous_pair_count == 1
    assert result.ascending.stop_snapshot_id == 1


def test_structural_conflict_stops_direction() -> None:
    series = SnapshotSeries(
        [
            _slice(0, pairs=((1, 2),), triples=((5, 6, 7),)),
            _slice(1, pairs=((2, 5),), triples=((5, 6, 8),)),
        ]
    )
    result = track_joint_continuation(
        series, start_snapshot_id=0, pair_ids=(1, 2), triangle_ids=(5, 6, 7)
    )

    assert result.ascending.stop_reason == "structural_conflict"
    assert result.ascending.structural_conflict_count == 1


def test_directions_are_initialized_independently() -> None:
    series = SnapshotSeries(
        [
            _slice(0, pairs=((1, 9),), triples=((4, 5, 8),)),
            _slice(1, pairs=((1, 2),), triples=((4, 5, 6),)),
            _slice(2, pairs=((2, 3),), triples=((5, 6, 7),)),
        ]
    )
    result = track_joint_continuation(
        series, start_snapshot_id=1, pair_ids=(1, 2), triangle_ids=(4, 5, 6)
    )

    assert result.ascending.tracked_slices[-1].pair_ids == (2, 3)
    assert result.descending.tracked_slices[-1].pair_ids == (1, 9)
    assert result.descending.tracked_slices[-1].triangle_shared_ids == (4, 5)


def test_invalid_start_is_rejected() -> None:
    series = SnapshotSeries(
        [_slice(0, pairs=((1, 2),), triples=((4, 5, 6),))]
    )
    with pytest.raises(ValueError, match="not a closed pair"):
        track_joint_continuation(
            series, start_snapshot_id=0, pair_ids=(1, 3), triangle_ids=(4, 5, 6)
        )


def test_csv_loader_keeps_empty_object_god_slice(tmp_path: Path) -> None:
    system = tmp_path / "system.csv"
    objects = tmp_path / "objects.csv"
    _write(
        system,
        [
            {"snapshot_id": 0, "tick": 0, "sweep": 0},
            {"snapshot_id": 1, "tick": 30, "sweep": 1},
        ],
    )
    _write(
        objects,
        [
            {"snapshot_id": 0, "object_class": "pair", "member_ids": "1|2"},
            {
                "snapshot_id": 0,
                "object_class": "triple",
                "member_ids": "4|5|6",
            },
        ],
    )

    series = SnapshotSeries.from_csv(system, objects)

    assert len(series.slices) == 2
    assert series.slices[1].pairs == ()
    assert series.slices[1].triples == ()


def test_chunk_loader_deduplicates_identical_boundary_slice(tmp_path: Path) -> None:
    paths = []
    for name, ids in (("left", (0, 1)), ("right", (1, 2))):
        directory = tmp_path / name
        directory.mkdir()
        system = directory / "system.csv"
        objects = directory / "objects.csv"
        _write(
            system,
            [
                {"snapshot_id": item, "tick": item * 30, "sweep": item}
                for item in ids
            ],
        )
        rows = []
        for item in ids:
            rows.extend(
                [
                    {
                        "snapshot_id": item,
                        "object_class": "pair",
                        "member_ids": "1|2",
                    },
                    {
                        "snapshot_id": item,
                        "object_class": "triple",
                        "member_ids": "4|5|6",
                    },
                ]
            )
        _write(objects, rows)
        paths.append((system, objects))

    series = SnapshotSeries.from_csv_pairs(paths)

    assert [item.snapshot_id for item in series.slices] == [0, 1, 2]


def _write(path: Path, rows: list[dict[str, int | str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
