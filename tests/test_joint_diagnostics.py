import csv
from pathlib import Path
from random import Random

import pytest

from uboot.checkpoint import dynamics_state_hash
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.joint_continuation import GodSlice, SnapshotSeries, track_joint_continuation
from uboot.joint_diagnostics import (
    TouchObservation,
    TrackedTouchObserver,
    build_diagnostics,
    candidate_support_raw_slots,
    write_diagnostics,
)


def _slice(snapshot_id: int, *, pairs=(), triples=()) -> GodSlice:
    return GodSlice(snapshot_id, snapshot_id * 30, float(snapshot_id), pairs, triples)


def _diagnose(slices, *, touches=()):
    series = SnapshotSeries(slices)
    result = track_joint_continuation(
        series, start_snapshot_id=0, pair_ids=(1, 2), triangle_ids=(3, 4, 5)
    )
    indexed = {(item.direction, item.snapshot_id): item for item in touches}
    return build_diagnostics(series, result, indexed)["ascending"]


def _touch(snapshot_id: int, **overrides: int) -> TouchObservation:
    values = {
        "tracked_member_active_update_count": 0,
        "pair_support_touch_count": 0,
        "triangle_support_touch_count": 0,
        "incoming_relation_touch_count": 0,
        "any_structure_touch_count": 0,
    }
    values.update(overrides)
    return TouchObservation(
        "ascending_scan", snapshot_id, snapshot_id - 1, **values
    )


def test_static_static_is_jointly_valid_without_movement() -> None:
    row = _diagnose(
        [
            _slice(0, pairs=((1, 2),), triples=((3, 4, 5),)),
            _slice(1, pairs=((1, 2),), triples=((3, 4, 5),)),
        ],
        touches=(_touch(1),),
    )[0]

    assert row.jointly_present
    assert not row.pair_changed and not row.triangle_changed
    assert not row.movement_event
    assert row.diagnostic_class == "untouched"


@pytest.mark.parametrize(
    ("pairs", "triples", "expected"),
    [
        (((2, 6),), ((3, 4, 5),), "pair_moved_triangle_static"),
        (((1, 2),), ((4, 5, 6),), "pair_static_triangle_moved"),
        (((2, 6),), ((4, 5, 7),), "both_moved"),
    ],
)
def test_all_three_movement_codes_are_valid_joint_events(
    pairs, triples, expected
) -> None:
    row = _diagnose(
        [
            _slice(0, pairs=((1, 2),), triples=((3, 4, 5),)),
            _slice(1, pairs=pairs, triples=triples),
        ],
        touches=(_touch(1, any_structure_touch_count=1),),
    )[0]
    assert row.jointly_present and row.movement_event
    assert row.diagnostic_class == expected


def test_pair_only_does_not_advance_joint_state() -> None:
    rows = _diagnose(
        [
            _slice(0, pairs=((1, 2),), triples=((3, 4, 5),)),
            _slice(1, pairs=((2, 6),), triples=()),
            _slice(2, pairs=((1, 7),), triples=((3, 4, 5),)),
        ],
        touches=(_touch(1), _touch(2)),
    )

    assert rows[0].diagnostic_class == "pair_only_continuation"
    assert rows[0].pair_changed
    assert rows[0].collapsed_by_joint_requirement
    assert rows[1].current_pair == (1, 2)
    assert rows[1].jointly_present


def test_triangle_only_does_not_advance_joint_state() -> None:
    rows = _diagnose(
        [
            _slice(0, pairs=((1, 2),), triples=((3, 4, 5),)),
            _slice(1, pairs=(), triples=((4, 5, 6),)),
            _slice(2, pairs=((1, 2),), triples=((3, 4, 7),)),
        ],
        touches=(_touch(1), _touch(2)),
    )

    assert rows[0].diagnostic_class == "triangle_only_continuation"
    assert rows[0].triangle_changed
    assert rows[1].current_triangle == (3, 4, 5)
    assert rows[1].jointly_present


def test_touch_but_static_is_distinct_from_untouched() -> None:
    slices = [
        _slice(0, pairs=((1, 2),), triples=((3, 4, 5),)),
        _slice(1, pairs=((1, 2),), triples=((3, 4, 5),)),
    ]
    touched = _diagnose(
        slices,
        touches=(
            _touch(1, pair_support_touch_count=1, any_structure_touch_count=1),
        ),
    )[0]
    untouched = _diagnose(slices, touches=(_touch(1),))[0]

    assert touched.diagnostic_class == "touched_but_joint_state_unchanged"
    assert not touched.movement_event
    assert untouched.diagnostic_class == "untouched"


def test_support_change_without_successor_is_not_generic_no_match() -> None:
    row = _diagnose(
        [
            _slice(0, pairs=((1, 2),), triples=((3, 4, 5),)),
            _slice(1),
        ],
        touches=(
            _touch(1, pair_support_touch_count=2, any_structure_touch_count=2),
        ),
    )[0]

    assert row.diagnostic_class == "pair_changed_no_valid_pair"
    assert row.collapsed_by_joint_requirement


def test_movement_output_compresses_only_changed_joint_slices(tmp_path: Path) -> None:
    rows = _diagnose(
        [
            _slice(0, pairs=((1, 2),), triples=((3, 4, 5),)),
            _slice(1, pairs=((1, 2),), triples=((3, 4, 5),)),
            _slice(2, pairs=((1, 2),), triples=((3, 4, 5),)),
            _slice(3, pairs=((2, 6),), triples=((3, 4, 5),)),
            _slice(4, pairs=((2, 6),), triples=((3, 4, 5),)),
            _slice(5, pairs=((2, 6),), triples=((4, 5, 7),)),
            _slice(6, pairs=((2, 6),), triples=((4, 5, 7),)),
        ],
        touches=tuple(_touch(index) for index in range(1, 7)),
    )
    write_diagnostics({"ascending": rows, "descending": ()}, tmp_path)

    with (tmp_path / "joint_fixed_point_movement_events.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        events = list(csv.DictReader(handle))

    assert [row["movement_code"] for row in events] == ["10", "01"]
    assert len(rows) == 6


def test_touch_observer_does_not_change_dynamics() -> None:
    def engine(seed: int) -> EdgeResponseEngine:
        rng = Random(seed)
        return EdgeResponseEngine(
            distinct_random_network(20, rng),
            policy_profile("M2"),
            rng,
            mode="M1",
            candidate_rule="endogenous_in_out2",
        )

    plain, observed = engine(91), engine(91)
    observer = TrackedTouchObserver(observed.targets, (1, 2), (3, 4, 5))
    observed.update_observer = observer
    plain.run(10)
    observed.run(10)

    assert dynamics_state_hash(observed) == dynamics_state_hash(plain)


def test_candidate_support_slots_use_incoming_and_exact_two_hop_witnesses() -> None:
    targets = [
        [6, 6, 6],
        [0, 4, 5],
        [6, 6, 6],
        [6, 6, 6],
        [1, 6, 6],
        [6, 6, 6],
        [1, 6, 6],
    ]

    support = candidate_support_raw_slots(targets, (0, 1))

    assert (1, 0, 0) in support
    assert (0, 0, 6) in support
    assert (6, 0, 1) in support
