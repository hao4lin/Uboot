from copy import deepcopy
from random import Random

import pytest

from uboot.checkpoint import dynamics_state_hash
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.exposure import (
    ExposureStateBuilder,
    FixedPointIndex,
    FixedPointObject,
    compare_exposure_states,
)
from uboot.exposure_trace import (
    FixedPointInvariantViolation,
    run_exposure_trace,
    validate_fixed_point_invariant,
)


OBJECTS = (
    FixedPointObject("P000001", "pair", (0, 1)),
    FixedPointObject("P000002", "pair", (2, 3)),
    FixedPointObject("P000003", "pair", (4, 5)),
)


def _targets() -> list[list[int]]:
    return [[6, 7, 6] for _ in range(8)]


def _state(targets: list[list[int]]):
    return ExposureStateBuilder(
        targets, FixedPointIndex(OBJECTS), ("P000001",), object_radius=2
    ).state()


def test_stable_ids_follow_canonical_member_order() -> None:
    index = FixedPointIndex.from_candidate_graph(
        [(1,), (0,), (3,), (2,), (5,), (4,)]
    )

    assert [(item.object_id, item.members) for item in index.objects] == [
        ("P000001", (0, 1)),
        ("P000002", (2, 3)),
        ("P000003", (4, 5)),
    ]


def test_internal_static_and_exposure_fully_static() -> None:
    state = _state(_targets())
    delta = compare_exposure_states(state, state)

    assert delta.change_classes == ("exposure_static",)
    assert not delta.level1_micro_exposure_changed
    assert not delta.level2_object_link_changed
    assert not delta.level3_two_hop_changed


def test_raw_target_changes_within_same_target_object() -> None:
    before_targets = _targets()
    before_targets[0][0] = 2
    after_targets = deepcopy(before_targets)
    after_targets[0][0] = 3

    delta = compare_exposure_states(_state(before_targets), _state(after_targets))

    assert delta.level1_micro_exposure_changed
    assert not delta.level2_object_link_changed
    assert "raw_target_changed_same_object" in delta.change_classes


def test_source_slot_reassigned_with_same_object_neighbor() -> None:
    before_targets = _targets()
    before_targets[0][0] = 2
    after_targets = deepcopy(before_targets)
    after_targets[0][0] = 6
    after_targets[1][1] = 2

    delta = compare_exposure_states(_state(before_targets), _state(after_targets))

    assert delta.level1_micro_exposure_changed
    assert not delta.level2_object_link_changed
    assert "source_slot_reassigned_same_target_object" in delta.change_classes


def test_direct_object_neighbor_changes_without_internal_change() -> None:
    before_targets = _targets()
    before_targets[0][0] = 2
    after_targets = deepcopy(before_targets)
    after_targets[0][0] = 4

    delta = compare_exposure_states(_state(before_targets), _state(after_targets))

    assert delta.level1_micro_exposure_changed
    assert delta.level2_object_link_changed
    assert "direct_object_neighbor_changed" in delta.change_classes
    assert delta.direct_link_break_count == 1
    assert delta.direct_link_create_count == 1


def test_direct_object_multiplicity_changes() -> None:
    before_targets = _targets()
    before_targets[0][:2] = [2, 3]
    after_targets = deepcopy(before_targets)
    after_targets[0][1] = 6

    delta = compare_exposure_states(_state(before_targets), _state(after_targets))

    assert delta.level2_object_link_changed
    assert delta.multiplicity_change_count == 1
    assert "multiplicity_changed" in delta.change_classes


def test_two_hop_micro_path_changes_while_direct_aggregate_is_static() -> None:
    before_targets = _targets()
    before_targets[0][0] = 2
    before_targets[2][0] = 4
    after_targets = deepcopy(before_targets)
    after_targets[2][0] = 6
    after_targets[3][1] = 4

    delta = compare_exposure_states(_state(before_targets), _state(after_targets))

    assert not delta.level2_object_link_changed
    assert delta.level3_two_hop_changed
    assert "middle_exit_changed" in delta.change_classes


def test_gap_transfer_candidate_is_supported_by_break_and_create() -> None:
    before_targets = _targets()
    before_targets[0][0] = 2
    after_targets = deepcopy(before_targets)
    after_targets[0][0] = 6
    after_targets[2][0] = 4

    delta = compare_exposure_states(_state(before_targets), _state(after_targets))

    assert delta.gap_transfer_candidate
    assert "gap_transfer_candidate" in delta.change_classes


def _engine() -> EdgeResponseEngine:
    rng = Random(7)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng),
        policy_profile("M2"),
        rng,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )
    engine.run(1000)
    return engine


def _root_objects(engine: EdgeResponseEngine) -> tuple[tuple[int, ...], tuple[int, ...]]:
    index = FixedPointIndex.from_candidate_graph(engine.candidate_graph())
    pairs = [item.members for item in index.objects if item.object_type == "pair"]
    triangles = [
        item.members for item in index.objects if item.object_type == "triangle"
    ]
    return next(
        (pair, triangle)
        for pair in pairs
        for triangle in triangles
        if not set(pair) & set(triangle)
    )


def test_analysis_ids_do_not_change_dynamics_or_rng() -> None:
    direct, traced = _engine(), _engine()
    pair, triangle = _root_objects(traced)
    end_step = traced.tick + 10
    for _ in range(10):
        direct.step_tick()

    run_exposure_trace(
        traced,
        root_pair=pair,
        root_triangle=triangle,
        start_snapshot_id=0,
        end_snapshot_id=1,
        end_step=end_step,
    )

    assert dynamics_state_hash(traced) == dynamics_state_hash(direct)
    assert traced.rng.getstate() == direct.rng.getstate()


def test_incremental_exposure_matches_full_recompute() -> None:
    engine = _engine()
    pair, triangle = _root_objects(engine)

    result = run_exposure_trace(
        engine,
        root_pair=pair,
        root_triangle=triangle,
        start_snapshot_id=0,
        end_snapshot_id=1,
        end_step=engine.tick + 10,
        verification_full_recompute=True,
    )

    assert result.summary["full_recompute_verification_count"] == 10


def test_internal_fixed_point_violation_stops_analysis() -> None:
    graph = [(1,), (0,), (3,), (2,), (5,), (4,)]
    index = FixedPointIndex.from_candidate_graph(graph)
    broken = [(1, 2), (0,), (3,), (2,), (5,), (4,)]

    with pytest.raises(FixedPointInvariantViolation, match="invariant_violation"):
        validate_fixed_point_invariant(index, broken, step=1)
