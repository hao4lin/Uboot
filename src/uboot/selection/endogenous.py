"""Candidate targets derived only from the network's current relations."""

from uboot.kernel.raw_network import RawNetwork


def endogenous_candidates(network: RawNetwork, source: int) -> tuple[int, ...]:
    """Return distinct reverse-neighbors and two-hop forward neighbors.

    Object IDs order the returned tuple only to make seeded runs reproducible. They
    do not change membership or provide an object-visible relation.
    """

    if not 0 <= source < network.size:
        raise IndexError("source object is outside the network")

    incoming = {
        other
        for other, targets in enumerate(network.targets)
        if source in targets and other != source
    }
    two_hop = {
        target
        for neighbor in network.targets[source]
        for target in network.targets[neighbor]
        if target != source
    }
    return tuple(sorted(incoming | two_hop))
