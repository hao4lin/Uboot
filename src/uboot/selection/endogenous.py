"""Candidate targets derived only from the network's current relations."""

from uboot.kernel.raw_network import RawNetwork


def endogenous_candidates(network: RawNetwork, source: int) -> frozenset[int]:
    """Return distinct reverse-neighbors and two-hop forward neighbors."""

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
    return frozenset(incoming | two_hop)
