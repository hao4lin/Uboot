"""Snapshot-only statistics; no counters are added to the rewrite loop."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from statistics import fmean, median

from uboot.kernel.raw_network import SLOT_COUNT, RawNetwork
from uboot.observables import mutual_pairs


@dataclass(frozen=True, slots=True)
class SnapshotStats:
    step: int
    N: int
    mutual_connection_count: int
    mutual_density: float
    nodes_with_0_mutual: int
    nodes_with_1_mutual: int
    nodes_with_2_mutual: int
    nodes_with_3_mutual: int
    nodes_with_gt3_mutual: int
    mean_mutual_degree: float
    max_mutual_degree: int
    unique_directed_edges: int
    duplicate_slot_ratio: float

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HeavyStats:
    step: int
    connected_components_excluding_isolates: int
    largest_mutual_component: int
    triangle_count: int
    square_count: int
    pentagon_count: int
    hexagon_count: int
    component_size_2: int
    component_size_3: int
    component_size_4_5: int
    component_size_6_10: int
    component_size_11_50: int
    component_size_gt50: int
    mean_candidate_size: float
    median_candidate_size: float
    p90_candidate_size: int
    max_candidate_size: int
    candidate_size_0: int
    candidate_size_1: int
    candidate_size_2: int
    candidate_size_3: int
    candidate_size_4_5: int
    candidate_size_6_10: int
    candidate_size_gt10: int
    closed_candidate_scc_count: int
    largest_closed_candidate_scc: int
    closed_candidate_scc_node_ratio: float
    closed_candidate_scc_size_1: int
    closed_candidate_scc_size_2: int
    closed_candidate_scc_size_3: int
    closed_candidate_scc_size_4: int
    closed_candidate_scc_size_5: int
    closed_candidate_scc_size_6_plus: int

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


def snapshot_stats(network: RawNetwork, step: int) -> SnapshotStats:
    """Compute ordinary statistics in one stage-boundary network scan."""

    pairs = mutual_pairs(network)
    degrees = [0] * network.size
    for left, right in pairs:
        degrees[left] += 1
        degrees[right] += 1

    degree_counts = [0, 0, 0, 0]
    greater_than_three = 0
    for degree in degrees:
        if degree <= 3:
            degree_counts[degree] += 1
        else:
            greater_than_three += 1

    unique_edges = sum(len(set(targets)) for targets in network.targets)
    pair_count = len(pairs)
    slot_count = SLOT_COUNT * network.size
    return SnapshotStats(
        step=step,
        N=network.size,
        mutual_connection_count=pair_count,
        mutual_density=2 * pair_count / slot_count,
        nodes_with_0_mutual=degree_counts[0],
        nodes_with_1_mutual=degree_counts[1],
        nodes_with_2_mutual=degree_counts[2],
        nodes_with_3_mutual=degree_counts[3],
        nodes_with_gt3_mutual=greater_than_three,
        mean_mutual_degree=2 * pair_count / network.size,
        max_mutual_degree=max(degrees, default=0),
        unique_directed_edges=unique_edges,
        duplicate_slot_ratio=(slot_count - unique_edges) / slot_count,
    )


def heavy_stats(network: RawNetwork, step: int) -> HeavyStats:
    """Compute components and simple cycles only at an explicit heavy boundary."""

    adjacency = [set[int]() for _ in range(network.size)]
    for left, right in mutual_pairs(network):
        adjacency[left].add(right)
        adjacency[right].add(left)

    sizes = _component_sizes(adjacency)
    candidates = _candidate_graph(network)
    candidate_sizes = sorted(len(targets) for targets in candidates)
    closed_sccs = [
        component
        for component in _strongly_connected_components(candidates)
        if all(candidates[node] <= component for node in component)
    ]
    closed_sizes = [len(component) for component in closed_sccs]
    closed_nodes = sum(closed_sizes)
    return HeavyStats(
        step=step,
        connected_components_excluding_isolates=len(sizes),
        largest_mutual_component=max(sizes, default=0),
        triangle_count=_simple_cycle_count(adjacency, 3),
        square_count=_simple_cycle_count(adjacency, 4),
        pentagon_count=_simple_cycle_count(adjacency, 5),
        hexagon_count=_simple_cycle_count(adjacency, 6),
        component_size_2=sum(size == 2 for size in sizes),
        component_size_3=sum(size == 3 for size in sizes),
        component_size_4_5=sum(4 <= size <= 5 for size in sizes),
        component_size_6_10=sum(6 <= size <= 10 for size in sizes),
        component_size_11_50=sum(11 <= size <= 50 for size in sizes),
        component_size_gt50=sum(size > 50 for size in sizes),
        mean_candidate_size=fmean(candidate_sizes),
        median_candidate_size=median(candidate_sizes),
        p90_candidate_size=candidate_sizes[ceil(0.9 * network.size) - 1],
        max_candidate_size=candidate_sizes[-1],
        candidate_size_0=sum(size == 0 for size in candidate_sizes),
        candidate_size_1=sum(size == 1 for size in candidate_sizes),
        candidate_size_2=sum(size == 2 for size in candidate_sizes),
        candidate_size_3=sum(size == 3 for size in candidate_sizes),
        candidate_size_4_5=sum(4 <= size <= 5 for size in candidate_sizes),
        candidate_size_6_10=sum(6 <= size <= 10 for size in candidate_sizes),
        candidate_size_gt10=sum(size > 10 for size in candidate_sizes),
        closed_candidate_scc_count=len(closed_sccs),
        largest_closed_candidate_scc=max(closed_sizes, default=0),
        closed_candidate_scc_node_ratio=closed_nodes / network.size,
        closed_candidate_scc_size_1=sum(size == 1 for size in closed_sizes),
        closed_candidate_scc_size_2=sum(size == 2 for size in closed_sizes),
        closed_candidate_scc_size_3=sum(size == 3 for size in closed_sizes),
        closed_candidate_scc_size_4=sum(size == 4 for size in closed_sizes),
        closed_candidate_scc_size_5=sum(size == 5 for size in closed_sizes),
        closed_candidate_scc_size_6_plus=sum(size >= 6 for size in closed_sizes),
    )


def _candidate_graph(network: RawNetwork) -> list[set[int]]:
    incoming = [set[int]() for _ in range(network.size)]
    for source, targets in enumerate(network.targets):
        for target in targets:
            incoming[target].add(source)

    candidates = []
    for source, targets in enumerate(network.targets):
        current = set(incoming[source])
        for neighbor in targets:
            current.update(network.targets[neighbor])
        current.discard(source)
        candidates.append(current)
    return candidates


def _strongly_connected_components(
    adjacency: list[set[int]],
) -> list[set[int]]:
    ordered_adjacency = [tuple(neighbors) for neighbors in adjacency]
    seen: set[int] = set()
    finish_order: list[int] = []
    for root in range(len(adjacency)):
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, 0)]
        while stack:
            node, index = stack[-1]
            if index < len(ordered_adjacency[node]):
                neighbor = ordered_adjacency[node][index]
                stack[-1] = (node, index + 1)
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append((neighbor, 0))
            else:
                finish_order.append(node)
                stack.pop()

    reverse = [set[int]() for _ in adjacency]
    for source, targets in enumerate(adjacency):
        for target in targets:
            reverse[target].add(source)

    assigned: set[int] = set()
    components = []
    for root in reversed(finish_order):
        if root in assigned:
            continue
        component = {root}
        assigned.add(root)
        stack = [root]
        while stack:
            node = stack.pop()
            new = reverse[node] - assigned
            assigned.update(new)
            component.update(new)
            stack.extend(new)
        components.append(component)
    return components


def _component_sizes(adjacency: list[set[int]]) -> list[int]:
    unseen = {node for node, neighbors in enumerate(adjacency) if neighbors}
    sizes = []
    while unseen:
        stack = [unseen.pop()]
        size = 0
        while stack:
            node = stack.pop()
            size += 1
            new = adjacency[node] & unseen
            unseen.difference_update(new)
            stack.extend(new)
        sizes.append(size)
    return sizes


def _simple_cycle_count(adjacency: list[set[int]], length: int) -> int:
    count = 0
    for start, neighbors in enumerate(adjacency):
        if len(neighbors) < 2:
            continue
        stack = [(neighbor, (start, neighbor)) for neighbor in neighbors if neighbor > start]
        while stack:
            node, path = stack.pop()
            if len(path) == length:
                count += start in adjacency[node]
                continue
            stack.extend(
                (neighbor, (*path, neighbor))
                for neighbor in adjacency[node]
                if neighbor > start and neighbor not in path
            )
    return count // 2
