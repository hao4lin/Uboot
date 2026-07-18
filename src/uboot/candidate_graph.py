"""Direct M1 candidate graph and strict closed-SCC analysis."""

from __future__ import annotations

from dataclasses import dataclass

from uboot.kernel import SLOT_COUNT, RawNetwork


def incoming_index(network: RawNetwork) -> list[list[set[int]]]:
    index = [[set[int]() for _ in range(network.size)] for _ in range(SLOT_COUNT)]
    for source, row in enumerate(network.targets):
        for slot, target in enumerate(row):
            index[slot][target].add(source)
    return index


def get_current_candidate_targets(
    targets: list[list[int]] | tuple[tuple[int, int, int], ...],
    incoming: list[list[set[int]]],
    node_id: int,
    *,
    rule: str = "incoming_excluding_out",
) -> tuple[int, ...]:
    """Return the current candidate support relation for one configured rule."""

    direct_in: set[int] = set()
    for slot in range(SLOT_COUNT):
        direct_in.update(incoming[slot][node_id])
    if rule == "incoming_excluding_out":
        direct_in.difference_update(targets[node_id])
        direct_in.discard(node_id)
        return tuple(direct_in)
    if rule == "endogenous_in_out2":
        out2 = {
            second_target
            for first_target in targets[node_id]
            for second_target in targets[first_target]
        }
        candidates = direct_in | out2
        candidates.discard(node_id)
        return tuple(candidates)
    raise ValueError(f"unknown candidate rule: {rule}")


def direct_candidate_graph(
    network: RawNetwork, *, rule: str = "incoming_excluding_out"
) -> list[tuple[int, ...]]:
    incoming = incoming_index(network)
    return [
        get_current_candidate_targets(network.targets, incoming, node, rule=rule)
        for node in range(network.size)
    ]


@dataclass(frozen=True, slots=True)
class CandidateObject:
    members: tuple[int, ...]
    closed: bool
    internal_candidate_edges: int
    outgoing_candidate_edges: int

    @property
    def object_class(self) -> str:
        size = len(self.members)
        if size == 1:
            return "closed_self_loop" if self.internal_candidate_edges else "candidate_isolated"
        if size == 2:
            return "pair"
        if size == 3:
            return "triple"
        return "larger"


def candidate_objects(
    graph: list[tuple[int, ...]], *, include_singletons: bool = True
) -> list[CandidateObject]:
    edge_count = sum(map(len, graph))
    components = strongly_connected_components(graph)
    objects = []
    for component in components:
        members = tuple(sorted(component))
        internal = sum(target in component for node in component for target in graph[node])
        outgoing = sum(target not in component for node in component for target in graph[node])
        item = CandidateObject(members, outgoing == 0, internal, outgoing)
        if item.closed and (include_singletons or len(members) > 1):
            objects.append(item)
    assert edge_count == sum(map(len, graph))
    objects.sort(key=lambda item: item.members)
    return objects


def strongly_connected_components(
    adjacency: list[tuple[int, ...]],
) -> list[set[int]]:
    seen: set[int] = set()
    finish_order: list[int] = []
    for root in range(len(adjacency)):
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, 0)]
        while stack:
            node, index = stack[-1]
            if index < len(adjacency[node]):
                neighbor = adjacency[node][index]
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
