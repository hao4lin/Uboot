"""Passive measurements that do not feed external labels into the dynamics."""

from uboot.kernel.raw_network import RawNetwork


def mutual_pairs(network: RawNetwork) -> frozenset[tuple[int, int]]:
    """Return unordered object pairs that point to each other through any slots."""

    pairs = set()
    for source, targets in enumerate(network.targets):
        for target in set(targets):
            if source < target and source in network.targets[target]:
                pairs.add((source, target))
    return frozenset(pairs)


def mutual_pair_count(network: RawNetwork) -> int:
    return len(mutual_pairs(network))
