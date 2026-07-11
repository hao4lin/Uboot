import pytest

from uboot.kernel import RawNetwork
from uboot.reporting import heavy_stats, snapshot_stats


@pytest.fixture
def triangle_and_square() -> RawNetwork:
    return RawNetwork(
        (
            (1, 2, 1),
            (0, 2, 0),
            (0, 1, 0),
            (4, 6, 4),
            (3, 5, 3),
            (4, 6, 4),
            (5, 3, 5),
        )
    )


def test_snapshot_statistics_are_computed_from_one_network_state(
    triangle_and_square: RawNetwork,
) -> None:
    stats = snapshot_stats(triangle_and_square, 25)

    assert stats.step == 25
    assert stats.mutual_connection_count == 7
    assert stats.nodes_with_2_mutual == 7
    assert stats.mean_mutual_degree == 2
    assert stats.max_mutual_degree == 2
    assert stats.unique_directed_edges == 14
    assert stats.duplicate_slot_ratio == pytest.approx(1 / 3)


def test_heavy_statistics_count_components_and_canonical_cycles(
    triangle_and_square: RawNetwork,
) -> None:
    stats = heavy_stats(triangle_and_square, 25)

    assert stats.connected_components_excluding_isolates == 2
    assert stats.largest_mutual_component == 4
    assert stats.triangle_count == 1
    assert stats.square_count == 1
    assert stats.pentagon_count == 0
    assert stats.hexagon_count == 0
    assert stats.component_size_3 == 1
    assert stats.component_size_4_5 == 1
