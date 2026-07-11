from random import Random

import pytest

from uboot.dynamics.endogenous import rewrite_once, simulate
from uboot.kernel import RawNetwork, random_network
from uboot.observables import mutual_pair_count, mutual_pairs
from uboot.selection.endogenous import endogenous_candidates


def test_raw_network_requires_three_non_self_targets() -> None:
    with pytest.raises(ValueError, match="three slots"):
        RawNetwork(((1, 1), (0, 0)))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot point to themselves"):
        RawNetwork(((0, 1, 1), (0, 0, 0)))


def test_random_initialization_is_seeded_and_preserves_invariants() -> None:
    first = random_network(20, Random(7))
    second = random_network(20, Random(7))

    assert first == second
    assert all(len(slots) == 3 for slots in first.targets)
    assert all(source not in slots for source, slots in enumerate(first.targets))


def test_candidates_are_only_incoming_or_two_hop_relations() -> None:
    network = RawNetwork(
        (
            (1, 1, 1),
            (2, 2, 2),
            (0, 3, 3),
            (1, 1, 1),
        )
    )

    assert endogenous_candidates(network, 0) == (2,)


def test_rewrite_uses_endogenous_candidate_without_mutating_input() -> None:
    network = RawNetwork(
        (
            (1, 1, 1),
            (2, 2, 2),
            (0, 3, 3),
            (1, 1, 1),
        )
    )
    result = rewrite_once(network, Random(2))

    assert result.source == 0
    assert result.target == 2
    assert result.candidate_count == 1
    assert network.targets[0] == (1, 1, 1)
    assert result.network.targets[0][result.slot] == 2


def test_mutual_pairs_ignore_slot_labels_and_duplicate_slots() -> None:
    network = RawNetwork(
        (
            (1, 1, 2),
            (0, 2, 2),
            (3, 3, 3),
            (2, 2, 2),
        )
    )

    assert mutual_pairs(network) == frozenset({(0, 1), (2, 3)})
    assert mutual_pair_count(network) == 2


def test_simulation_samples_initial_and_final_state() -> None:
    initial = random_network(12, Random(11))
    final, samples = simulate(initial, 5, Random(12), sample_every=3)

    assert final.size == initial.size
    assert [sample.step for sample in samples] == [0, 3, 5]
    assert all(0 <= sample.mutual_slot_density <= 1 for sample in samples)


def test_indexed_simulation_matches_reference_rewrites() -> None:
    initial = random_network(15, Random(21))
    reference = initial
    reference_rng = Random(22)
    for _ in range(50):
        reference = rewrite_once(reference, reference_rng).network

    indexed, _ = simulate(initial, 50, Random(22), sample_every=50)

    assert indexed == reference


def test_simulation_reports_final_progress() -> None:
    initial = random_network(10, Random(31))
    reports: list[tuple[int, int, int]] = []

    simulate(
        initial,
        7,
        Random(32),
        sample_every=7,
        progress=lambda completed, total, sample: reports.append(
            (completed, total, sample.step)
        ),
        progress_check_every=3,
    )

    assert reports == [(3, 7, 0), (6, 7, 0), (7, 7, 7)]
