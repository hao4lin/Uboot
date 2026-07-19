from __future__ import annotations

import json
from types import SimpleNamespace

from uboot.adaptive_relation_tracer import TraceCandidate
from uboot.commit_centered_relation_trace import (
    PrimaryChange,
    SUPPORT_STRENGTH,
    _support_boundaries,
    classify_nontrivial_support,
    is_nontrivial_support,
    trace_commit_centered,
)
from uboot.deterministic_replay import ImpactIndex, UpdateImpactRecord, build_replay_oracle
from uboot.kernel import RawNetwork
from uboot.minimal_edge_experiment import network_hash, run_baseline_reference


def test_source_sibling_is_trivial_not_nontrivial() -> None:
    before = _network({0: (1, 3, 4)})
    after = _network({0: (2, 3, 4)})
    commit = UpdateImpactRecord(10, 0, 0, 1, 2)
    result = classify_nontrivial_support(before, after, commit)
    assert result.classification == "TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION"
    assert result.support_strength == SUPPORT_STRENGTH["TRIVIAL"]
    assert not is_nontrivial_support(before, after, commit, result)


def test_independent_incoming_support_is_weak_nontrivial() -> None:
    before = _network({0: (1, 3, 4), 5: (0, 6, 7)})
    after = _network({0: (2, 3, 4), 5: (0, 6, 7)})
    result = classify_nontrivial_support(
        before, after, UpdateImpactRecord(10, 0, 0, 1, 2)
    )
    assert result.classification == "INDEPENDENT_INCOMING_SUPPORT_PRESERVED"
    assert result.support_strength == SUPPORT_STRENGTH["WEAK_NONTRIVIAL"]


def test_neighbor_role_transfer_via_same_raw_third_is_strong() -> None:
    before = _network({0: (1, 3, 4), 1: (5, 6, 7), 2: (3, 6, 7)})
    after = _network({0: (2, 3, 4), 1: (3, 6, 7), 2: (5, 6, 7)})
    result = classify_nontrivial_support(
        before, after, UpdateImpactRecord(10, 0, 0, 1, 2)
    )
    assert result.classification == "NEIGHBOR_ROLE_TRANSFER_VIA_THIRD_MEMBER"
    assert result.support_strength == SUPPORT_STRENGTH["STRONG_RAW_SUPPORT"]


def test_two_independent_sources_are_strong_raw_support() -> None:
    before = _network({0: (1, 3, 4), 5: (0, 6, 7), 6: (1, 3, 4)})
    after = _network({0: (2, 3, 4), 5: (0, 6, 7), 6: (1, 3, 4)})
    result = classify_nontrivial_support(
        before, after, UpdateImpactRecord(10, 0, 0, 1, 2)
    )
    assert result.classification == "MULTI_SOURCE_SUPPORT_PRESERVED"
    assert result.support_strength == SUPPORT_STRENGTH["STRONG_RAW_SUPPORT"]


def test_canonical_role_structure_with_replaced_third_is_strongest() -> None:
    before = _network({0: (1, 3, 4), 1: (5, 6, 7), 5: (0, 3, 4)})
    after = _network({0: (2, 3, 4), 2: (6, 5, 7), 6: (0, 3, 4)})
    result = classify_nontrivial_support(
        before, after, UpdateImpactRecord(10, 0, 0, 1, 2)
    )
    assert result.classification == "CANONICAL_LOCAL_ROLE_STRUCTURE_PRESERVED"
    assert result.support_strength == SUPPORT_STRENGTH["STRONG_CANONICAL_SUPPORT"]
    assert json.loads(result.canonical_role_mapping)["N"] == [1, 2]


def test_only_source_in_common_is_one_member_only() -> None:
    before = _network({0: (1, 3, 4)})
    after = _network({0: (2, 5, 6)})
    result = classify_nontrivial_support(
        before, after, UpdateImpactRecord(10, 0, 0, 1, 2)
    )
    assert result.classification == "ONE_MEMBER_ONLY"
    assert result.support_strength == SUPPORT_STRENGTH["NONE"]


def test_support_boundary_expands_from_eight_to_thirty_two() -> None:
    index = ImpactIndex("all-commits")
    index.append(UpdateImpactRecord(120, 5, 0, 0, 1))
    oracle = SimpleNamespace(impact_index=index)
    primary = PrimaryChange("C01", 100, 0, 0, 1, 2, "PAIR_BROKEN", "x")
    window, rows, incomplete = _support_boundaries(
        oracle,
        primary,
        ({"third_raw_id": 5},),
        (8, 32, 128),
        128,
    )
    assert window == 32
    assert rows[0]["distance_in_locator"] == 20
    assert not incomplete


def test_v2_bundle_is_byte_reproducible_and_dynamics_unchanged() -> None:
    oracle = build_replay_oracle(
        size=8,
        seed=17,
        max_locator=288,
        checkpoint_locators=(0, 100, 288),
        impact_index_mode="all-commits",
    )
    record = next(r for r in oracle.impact_index.records() if r.old_target != r.new_target)
    candidate = TraceCandidate(
        "C01",
        (record.source_raw_id, record.old_target, record.new_target),
        (record.source_raw_id,),
        (record.commit_locator - 1, record.commit_locator),
        "anchor_neighborhood",
        "test",
        "test.csv",
        2,
        "a",
        "b",
        "ONE_MEMBER_ONLY_CONTROL",
    )
    first = trace_commit_centered(oracle, (candidate,))
    second = trace_commit_centered(oracle, (candidate,))
    encoded_first = json.dumps(first.cases[0].bundle, sort_keys=True, separators=(",", ":"))
    encoded_second = json.dumps(second.cases[0].bundle, sort_keys=True, separators=(",", ":"))
    assert encoded_first == encoded_second
    direct_network, direct_rng = run_baseline_reference(size=8, seed=17, sweeps=12)
    final = oracle.checkpoints[288]
    assert final.network_hash == network_hash(direct_network)
    assert final.rng_state == direct_rng


def _network(overrides: dict[int, tuple[int, int, int]]) -> RawNetwork:
    rows = [
        (1, 2, 3),
        (3, 4, 5),
        (3, 4, 5),
        (4, 5, 6),
        (3, 5, 6),
        (3, 4, 6),
        (3, 4, 5),
        (3, 4, 5),
    ]
    rows[1] = (3, 4, 5)
    rows[2] = (3, 4, 5)
    rows[3] = (4, 5, 6)
    rows[4] = (3, 5, 6)
    rows[5] = (3, 4, 6)
    rows[6] = (3, 4, 5)
    rows[7] = (3, 4, 5)
    for source, targets in overrides.items():
        rows[source] = targets
    return RawNetwork(tuple(rows))
