from __future__ import annotations

from random import Random
from types import SimpleNamespace

from uboot.kernel import RawNetwork
from uboot.support_graph_review import (
    V3_STRENGTH,
    generate_background_controls,
    review_support_graph,
)
from uboot.support_graph_review_runner import ReviewCase, ReviewInput, write_review_outputs


def test_scattered_multi_source_is_background() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 3: (0, 5, 6), 4: (1, 5, 6)},
        {0: (2, 5, 6), 3: (0, 5, 6), 4: (1, 5, 6)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.v3_classification == "BACKGROUND_MULTI_SOURCE_PRESERVATION"
    assert review.support_strength == V3_STRENGTH["WEAK_BACKGROUND"]


def test_multi_source_convergence_transfer() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 3: (1, 2, 5), 4: (1, 2, 6)},
        {0: (2, 5, 6), 3: (1, 2, 5), 4: (1, 2, 6)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.v3_classification == "MULTI_SOURCE_CONVERGENCE_TRANSFER"


def test_multi_target_divergence_transfer() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 1: (3, 4, 5), 2: (3, 4, 6)},
        {0: (2, 5, 6), 1: (3, 4, 5), 2: (3, 4, 6)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.v3_classification == "MULTI_TARGET_DIVERGENCE_TRANSFER"


def test_closed_triangle_role_is_preserved() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 1: (3, 5, 6), 2: (3, 5, 6), 3: (0, 5, 6)},
        {0: (2, 5, 6), 1: (3, 5, 6), 2: (3, 5, 6), 3: (0, 5, 6)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.v3_classification == "CLOSED_LOCAL_PATH_PRESERVED"
    assert review.closed_path_count >= 1


def test_shared_source_dual_role_transfer() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 3: (0, 1, 2)},
        {0: (2, 5, 6), 3: (0, 1, 2)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.v3_classification == "SHARED_SOURCE_DUAL_ROLE_TRANSFER"


def test_static_convergence_at_primary_source_is_weak_background() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 3: (0, 5, 6), 4: (0, 5, 6)},
        {0: (2, 5, 6), 3: (0, 5, 6), 4: (0, 5, 6)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.v3_classification == "STATIC_CONVERGENCE_BACKGROUND"
    assert review.support_strength == V3_STRENGTH["WEAK_BACKGROUND"]


def test_two_node_two_edge_canonical_shape_is_not_canonical_motif() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 1: (0, 5, 6), 2: (0, 5, 6)},
        {0: (2, 5, 6), 1: (0, 5, 6), 2: (0, 5, 6)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.v3_classification != "CANONICAL_MOTIF_PRESERVED"


def test_three_node_three_edge_canonical_motif() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 1: (3, 5, 6), 3: (0, 5, 6)},
        {0: (2, 5, 6), 2: (4, 5, 6), 4: (0, 5, 6)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.v3_classification == "CANONICAL_MOTIF_PRESERVED"
    assert review.mapping_ambiguity_count >= 1


def test_disconnected_closed_support_does_not_pass_primary_gate() -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 3: (4, 5, 6), 4: (5, 6, 3), 5: (3, 4, 6)},
        {0: (2, 5, 6), 3: (4, 5, 6), 4: (5, 6, 3), 5: (3, 4, 6)},
    )
    review = review_support_graph(before, after, commit).review
    assert review.support_strength < V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]


def test_control_rng_is_isolated_from_main_rng() -> None:
    network = _network({0: (1, 5, 6)})
    main_rng = Random(17)
    state = main_rng.getstate()
    first = generate_background_controls(
        network,
        real_source=0,
        real_old=1,
        real_new=2,
        slot_semantic=0,
        count=10,
        seed=20260718,
    )
    second = generate_background_controls(
        network,
        real_source=0,
        real_old=1,
        real_new=2,
        slot_semantic=0,
        count=10,
        seed=20260718,
    )
    assert main_rng.getstate() == state
    assert first == second


def test_review_outputs_are_byte_reproducible(tmp_path) -> None:
    before, after, commit = _transition(
        {0: (1, 5, 6), 3: (1, 2, 5), 4: (1, 2, 6)},
        {0: (2, 5, 6), 3: (1, 2, 5), 4: (1, 2, 6)},
    )
    reviewed = review_support_graph(before, after, commit)
    source = ReviewInput(
        "V2-001", "V2_STRONG_RAW", "C01", 10, 0, 0, 1, 2, "OLD", "COMPLETE"
    )
    cases = (ReviewCase(source, reviewed, (reviewed,), "ENRICHED"),)
    verification = {
        "baseline_final_network_hash_match": True,
        "baseline_final_rng_state_match": True,
    }
    outputs = []
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        write_review_outputs(
            root,
            cases,
            old_reclassification_rows=[],
            verification=verification,
            runtime_seconds=1.0,
        )
        outputs.append(root)
    for filename in (
        "support_graphs_v3.jsonl",
        "support_motifs_v3.csv",
        "motion_candidates_v3.csv",
    ):
        assert (outputs[0] / filename).read_bytes() == (outputs[1] / filename).read_bytes()


def _transition(
    before_overrides: dict[int, tuple[int, int, int]],
    after_overrides: dict[int, tuple[int, int, int]],
):
    return (
        _network(before_overrides),
        _network(after_overrides),
        SimpleNamespace(source_raw_id=0, slot_semantic=0, old_target=1, new_target=2),
    )


def _network(overrides: dict[int, tuple[int, int, int]]) -> RawNetwork:
    pool = tuple(range(5, 14))
    rows = [(11, 12, 13) for _ in range(5)]
    rows[1] = (5, 6, 7)
    rows[2] = (8, 9, 10)
    for source in pool:
        offset = pool.index(source)
        rows.append(
            tuple(pool[(offset + step) % len(pool)] for step in (1, 2, 3))
        )
    for source, targets in overrides.items():
        rows[source] = targets
    return RawNetwork(tuple(rows))
