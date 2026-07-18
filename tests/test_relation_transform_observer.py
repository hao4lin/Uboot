from __future__ import annotations

from random import Random

from uboot.kernel import RawNetwork
from uboot.minimal_edge_experiment import (
    MinimalEdgeEngine,
    initial_network,
    network_hash,
    run_baseline_reference,
)
from uboot.minimal_edge_rules import BaselineRandomReplace
from uboot.relation_transform_observer import (
    BODY_SAME_NEIGHBOR_DIFFERENT,
    MEMBERS_SAME_RELATION_DIFFERENT,
    NEIGHBOR_SAME_BODY_DIFFERENT,
    ONE_MEMBER_OVERLAP_ROLE_CHANGED,
    RelationSliceIdentity,
    RelationTransformationObserver,
    SliceRecord,
    classify_transformation,
    transformation_rows,
)


def test_observer_does_not_change_baseline_state_or_main_rng() -> None:
    size, seed, sweeps = 20, 44, 30
    rng = Random(seed)
    engine = MinimalEdgeEngine(initial_network(size, rng), rng, BaselineRandomReplace())
    observer = RelationTransformationObserver(size, 99, 4)
    for sweep in (0, 10, 30):
        engine.run_to_sweep(sweep, "baseline_random_replace")
        observer.observe(engine.snapshot(), atom_step=engine.tick, snapshot=sweep)
    reference, reference_rng = run_baseline_reference(
        size=size, seed=seed, sweeps=sweeps
    )
    assert network_hash(engine.snapshot()) == network_hash(reference)
    assert engine.rng.getstate() == reference_rng


def test_larger_anchor_samples_extend_the_same_seeded_prefix() -> None:
    small = RelationTransformationObserver(100, 20260718, 3)
    large = RelationTransformationObserver(100, 20260718, 10)
    assert set(small.anchor_ids) < set(large.anchor_ids)


def test_same_relation_at_different_locators_reuses_slice_identity() -> None:
    observer = RelationTransformationObserver(5, 1, 1)
    observer.anchor_ids = (0,)
    observer.anchor_set = {0}
    network = _network_a()
    observer.observe(network, atom_step=1, snapshot=0)
    first_ids = set(observer.records)
    observer.observe(network, atom_step=999, snapshot=8)
    assert set(observer.records) == first_ids
    assert all(record.observation_count == 2 for record in observer.records.values())


def test_observation_locators_create_no_direction_fields_or_edge_orientation() -> None:
    first = _observer_with_manual_slices((1000, 1, 500, 3))
    second = _observer_with_manual_slices((3, 500, 1, 1000))
    first_rows = transformation_rows(first.transformations())
    second_rows = transformation_rows(second.transformations())
    assert first_rows == second_rows
    assert all(
        forbidden not in row
        for row in first_rows
        for forbidden in ("direction", "from_step", "to_step", "duration")
    )


def test_a_b_and_a_c_are_body_same_neighbor_different() -> None:
    item = classify_transformation(
        _record(RelationSliceIdentity("directed", (0,), 0, 1)),
        _record(RelationSliceIdentity("directed", (0,), 0, 2)),
    )
    assert item is not None
    assert item.transformation_type == BODY_SAME_NEIGHBOR_DIFFERENT
    assert item.shared_raw_ids == (0,)


def test_a_c_and_d_c_are_neighbor_same_body_different() -> None:
    item = classify_transformation(
        _record(RelationSliceIdentity("directed", (0,), 0, 2)),
        _record(RelationSliceIdentity("directed", (0,), 3, 2)),
    )
    assert item is not None
    assert item.transformation_type == NEIGHBOR_SAME_BODY_DIFFERENT
    assert item.shared_raw_ids == (2,)


def test_directed_and_reciprocal_same_members_change_relation() -> None:
    item = classify_transformation(
        _record(RelationSliceIdentity("directed", (0,), 0, 1)),
        _record(RelationSliceIdentity("reciprocal_same_slot", (0, 0), 0, 1)),
    )
    assert item is not None
    assert item.transformation_type == MEMBERS_SAME_RELATION_DIFFERENT


def test_reversed_directed_roles_with_same_members_change_relation() -> None:
    item = classify_transformation(
        _record(RelationSliceIdentity("directed", (0,), 0, 1)),
        _record(RelationSliceIdentity("directed", (0,), 1, 0)),
    )
    assert item is not None
    assert item.transformation_type == MEMBERS_SAME_RELATION_DIFFERENT


def test_nonadjacent_observation_locators_still_create_direct_edge() -> None:
    observer = RelationTransformationObserver(5, 2, 1)
    observer.anchor_ids = (0,)
    observer.anchor_set = {0}
    observer.observe(_network_a(), atom_step=1, snapshot=0)
    observer.observe(_network_b(), atom_step=1_000_000, snapshot=1000)
    kinds = {item.transformation_type for item in observer.transformations()}
    assert BODY_SAME_NEIGHBOR_DIFFERENT in kinds


def test_paths_depend_only_on_transformation_graph_not_locator_order() -> None:
    first = _observer_with_manual_slices((1, 3, 1000, 7))
    second = _observer_with_manual_slices((1000, 7, 1, 3))
    first_paths = first.paths(first.transformations())
    second_paths = second.paths(second.transformations())
    assert first_paths == second_paths
    assert any(row["path_length"] == 3 for row in first_paths)
    summary = first.anchor_summary(first.transformations(), first_paths)
    assert sum(
        summary[0][f"transformation_path_count_length_{length}"]
        for length in (2, 3, 4)
    ) == len(first_paths)


def test_role_mapping_preserves_neighbor_role_and_detects_role_change() -> None:
    neighbor_item = classify_transformation(
        _record(RelationSliceIdentity("directed", (1,), 0, 2)),
        _record(RelationSliceIdentity("directed", (1,), 3, 2)),
    )
    assert neighbor_item is not None
    assert neighbor_item.role_mapping == ((2, "neighbor", "neighbor"),)
    changed_item = classify_transformation(
        _record(RelationSliceIdentity("directed", (1,), 0, 2)),
        _record(RelationSliceIdentity("directed", (1,), 2, 4)),
    )
    assert changed_item is not None
    assert changed_item.transformation_type == ONE_MEMBER_OVERLAP_ROLE_CHANGED
    assert changed_item.role_mapping[0][0] == 2
    assert set(changed_item.role_mapping[0][1:]) == {"neighbor", "body"}


def test_raw_disjoint_canonical_matches_are_aggregated_not_pairwise_edges() -> None:
    observer = RelationTransformationObserver(6, 3, 1)
    observer._record(RelationSliceIdentity("directed", (0,), 0, 1), {0}, "locator-a")
    observer._record(RelationSliceIdentity("directed", (0,), 2, 3), {0}, "locator-b")
    assert observer.transformations() == ()
    group = next(
        row
        for row in observer.equivalence_groups()
        if row["canonical_signature"].startswith("directed;slots=0")
    )
    assert group["canonical_same_raw_disjoint_pair_count"] == 1


def test_same_relation_different_members_is_a_secondary_anchor_statistic() -> None:
    observer = RelationTransformationObserver(5, 3, 1)
    observer.anchor_ids = (0,)
    observer.anchor_set = {0}
    observer._record(RelationSliceIdentity("directed", (0,), 0, 1), {0}, "a")
    observer._record(RelationSliceIdentity("directed", (0,), 0, 2), {0}, "b")
    transformations = observer.transformations()
    summary = observer.anchor_summary(transformations, observer.paths(transformations))
    assert summary[0]["body_same_neighbor_different_count"] == 1
    assert summary[0]["relation_same_members_different_count"] == 1


def _record(identity: RelationSliceIdentity) -> SliceRecord:
    return SliceRecord("x" + str(hash(identity)), identity)


def _observer_with_manual_slices(
    locators: tuple[int, int, int, int],
) -> RelationTransformationObserver:
    observer = RelationTransformationObserver(5, 7, 1, 4)
    observer.anchor_ids = (0,)
    observer.anchor_set = {0}
    identities = (
        RelationSliceIdentity("directed", (0,), 0, 1),
        RelationSliceIdentity("directed", (0,), 0, 2),
        RelationSliceIdentity("directed", (0,), 3, 2),
        RelationSliceIdentity("directed", (0,), 3, 4),
    )
    for identity, locator in zip(identities, locators):
        observer._record(identity, {0}, f"locator-{locator}")
    return observer


def _network_a() -> RawNetwork:
    return RawNetwork(
        (
            (1, 2, 3),
            (2, 3, 4),
            (3, 4, 0),
            (4, 0, 1),
            (0, 1, 2),
        )
    )


def _network_b() -> RawNetwork:
    return RawNetwork(
        (
            (4, 2, 3),
            (2, 3, 4),
            (3, 4, 0),
            (4, 0, 1),
            (0, 1, 2),
        )
    )
