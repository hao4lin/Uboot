"""Independent runner and passive statistics for minimal reciprocal edge rules."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import pickle
from random import Random
from statistics import fmean
from typing import Any, Callable, Iterable, Sequence

from uboot.candidate_graph import strongly_connected_components
from uboot.kernel import SLOT_COUNT, RawNetwork
from uboot.minimal_edge_rules import MinimalEdgeRule, build_minimal_rule


ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True, slots=True)
class MinimalRunResult:
    policy: str
    seed: int
    size: int
    sweeps: int
    final_hash: str
    rng_state: object
    rng_state_hash: str
    summary: dict[str, Any]
    time_series: tuple[dict[str, Any], ...]
    structure_lifetimes: tuple[dict[str, Any], ...]


class MinimalEdgeEngine:
    """One random-node/random-slot update with no persistent policy state."""

    def __init__(self, network: RawNetwork, rng: Random, rule: MinimalEdgeRule) -> None:
        self.targets = [list(row) for row in network.targets]
        self.rng = rng
        self.rule = rule
        self.tick = 0
        self.relation_born = [[0] * SLOT_COUNT for _ in self.targets]
        self.completed_relation_ages: list[int] = []
        self.counters: Counter[str] = Counter()

    @property
    def size(self) -> int:
        return len(self.targets)

    def legal_candidates(self, node: int, slot: int) -> tuple[int, ...]:
        blocked = {
            node,
            *(
                target
                for index, target in enumerate(self.targets[node])
                if index != slot
            ),
        }
        return tuple(
            candidate for candidate in range(self.size) if candidate not in blocked
        )

    def step(self) -> None:
        node = self.rng.randrange(self.size)
        slot = self.rng.randrange(SLOT_COUNT)
        old_target = self.targets[node][slot]
        proposed, selected = self.rule.choose_target(
            self.targets,
            node,
            slot,
            self.legal_candidates(node, slot),
            self.rng,
        )
        affected = {(node, slot)}
        if self.targets[old_target][slot] == node:
            affected.add((old_target, slot))
        if self.targets[selected][slot] == node:
            affected.add((selected, slot))
        before = {relation: self._is_reciprocal(*relation) for relation in affected}
        self.counters["same_target_sampled_count"] += proposed == old_target
        if selected == old_target:
            self.counters["target_keep_count"] += 1
        else:
            self.counters["target_change_count"] += 1
            age = (self.tick + 1) - self.relation_born[node][slot]
            self.completed_relation_ages.append(age)
            self.targets[node][slot] = selected
            self.relation_born[node][slot] = self.tick + 1
        for relation, old_reciprocal in before.items():
            new_reciprocal = self._is_reciprocal(*relation)
            self.counters[
                f"transition_{'r' if old_reciprocal else 'n'}"
                f"{'r' if new_reciprocal else 'n'}"
            ] += 1
        self.tick += 1

    def _is_reciprocal(self, node: int, slot: int) -> bool:
        target = self.targets[node][slot]
        return self.targets[target][slot] == node

    def run_to_sweep(
        self,
        sweep: int,
        policy_name: str,
        progress: ProgressCallback | None = None,
        *,
        progress_check_every: int = 16_384,
    ) -> None:
        target_tick = sweep * SLOT_COUNT * self.size
        while self.tick < target_tick:
            self.step()
            if progress is not None and self.tick % progress_check_every == 0:
                progress(policy_name, self.tick, target_tick)
        if progress is not None:
            progress(policy_name, self.tick, target_tick)

    def snapshot(self) -> RawNetwork:
        return RawNetwork(tuple(tuple(row) for row in self.targets))  # type: ignore[arg-type]


class StructureLifetimeTracker:
    def __init__(self, policy: str, seed: int) -> None:
        self.policy = policy
        self.seed = seed
        self.active: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
        self.episode_counts: Counter[tuple[str, tuple[int, ...]]] = Counter()
        self.completed: list[dict[str, Any]] = []

    def observe(
        self, sweep: int, structures: Iterable[tuple[str, tuple[int, ...]]]
    ) -> None:
        current = set(structures)
        for key in set(self.active) - current:
            self.completed.append(self._row(self.active.pop(key), False))
        for key in current:
            if key not in self.active:
                self.episode_counts[key] += 1
                kind, members = key
                self.active[key] = {
                    "policy": self.policy,
                    "seed": self.seed,
                    "structure_type": kind,
                    "members": "|".join(map(str, members)),
                    "episode": self.episode_counts[key],
                    "birth_sweep": sweep,
                    "last_seen_sweep": sweep,
                }
            else:
                self.active[key]["last_seen_sweep"] = sweep

    def rows(self) -> tuple[dict[str, Any], ...]:
        rows = list(self.completed)
        rows.extend(self._row(item, True) for item in self.active.values())
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    row["structure_type"],
                    row["members"],
                    row["episode"],
                ),
            )
        )

    @staticmethod
    def _row(item: dict[str, Any], right_censored: bool) -> dict[str, Any]:
        row = dict(item)
        row["observed_lifetime"] = row["last_seen_sweep"] - row["birth_sweep"]
        row["right_censored"] = right_censored
        return row


def run_minimal_comparison(
    *,
    size: int,
    seed: int,
    sweeps: int,
    snapshot_sweeps: tuple[int, ...],
    policy_names: tuple[str, ...],
    progress: ProgressCallback | None = None,
) -> tuple[MinimalRunResult, ...]:
    if size < 4:
        raise ValueError("N must be at least four")
    checkpoints = tuple(
        sweep
        for sweep in sorted(set(snapshot_sweeps) | {0, sweeps})
        if 0 <= sweep <= sweeps
    )
    results = []
    for policy_name in policy_names:
        rng = Random(seed)
        network = initial_network(size, rng)
        engine = MinimalEdgeEngine(network, rng, build_minimal_rule(policy_name))
        tracker = StructureLifetimeTracker(policy_name, seed)
        rows = []
        for sweep in checkpoints:
            engine.run_to_sweep(sweep, policy_name, progress)
            row, structures = minimal_snapshot(policy_name, seed, sweep, engine)
            rows.append(row)
            tracker.observe(sweep, structures)
        final_network = engine.snapshot()
        results.append(
            MinimalRunResult(
                policy=policy_name,
                seed=seed,
                size=size,
                sweeps=sweeps,
                final_hash=network_hash(final_network),
                rng_state=engine.rng.getstate(),
                rng_state_hash=state_hash(engine.rng.getstate()),
                summary={**rows[-1], "sweeps": sweeps},
                time_series=tuple(rows),
                structure_lifetimes=tracker.rows(),
            )
        )
    return tuple(results)


def minimal_snapshot(
    policy: str, seed: int, sweep: int, engine: MinimalEdgeEngine
) -> tuple[dict[str, Any], tuple[tuple[str, tuple[int, ...]], ...]]:
    network = engine.snapshot()
    raw_graph = [tuple(row) for row in network.targets]
    raw_sccs = strongly_connected_components(raw_graph)
    q_graph = q_simple_graph(network)
    components = nontrivial_components(q_graph)
    structures = classify_structures(q_graph, components)
    reciprocal_slots = sum(
        network.targets[target][slot] == node
        for node, row in enumerate(network.targets)
        for slot, target in enumerate(row)
    )
    current_ages = [
        engine.tick - engine.relation_born[node][slot]
        for node in range(network.size)
        for slot in range(SLOT_COUNT)
    ]
    rr = engine.counters["transition_rr"]
    rn = engine.counters["transition_rn"]
    nn = engine.counters["transition_nn"]
    nr = engine.counters["transition_nr"]
    row: dict[str, Any] = {
        "policy": policy,
        "seed": seed,
        "N": network.size,
        "sweep": sweep,
        "tick": engine.tick,
        "R_scc_count": len(raw_sccs),
        "R_max_scc": max(map(len, raw_sccs), default=0),
        "R_g": max(map(len, raw_sccs), default=0) / network.size,
        "Q_edge_count": sum(map(len, q_graph)) // 2,
        "Q_node_coverage": sum(map(len, components)) / network.size,
        "Q_component_count": len(components),
        "Q_max_component": max((len(group) for group in components), default=1),
        "Q_pair_component_count": sum(kind == "pair" for kind, _ in structures),
        "Q_triangle_component_count": sum(kind == "triangle" for kind, _ in structures),
        "Q_larger_component_count": sum(kind == "larger" for kind, _ in structures),
        "reciprocal_slot_count": reciprocal_slots,
        "nonreciprocal_slot_count": SLOT_COUNT * network.size - reciprocal_slots,
        "transition_nn": nn,
        "transition_nr": nr,
        "transition_rr": rr,
        "transition_rn": rn,
        "reciprocal_keep_rate": _rate(rr, rr + rn),
        "nonreciprocal_to_reciprocal_rate": _rate(nr, nn + nr),
        "target_keep_count": engine.counters["target_keep_count"],
        "target_change_count": engine.counters["target_change_count"],
        "same_target_sampled_count": engine.counters["same_target_sampled_count"],
        "mean_current_relation_age": fmean(current_ages),
        "mean_completed_relation_age": (
            fmean(engine.completed_relation_ages)
            if engine.completed_relation_ages
            else "N/A"
        ),
        "right_censored_relation_count": SLOT_COUNT * network.size,
    }
    return row, structures


def initial_network(size: int, rng: Random) -> RawNetwork:
    if size < 4:
        raise ValueError("N must be at least four")
    rows = tuple(
        tuple(
            rng.sample(
                [candidate for candidate in range(size) if candidate != node],
                SLOT_COUNT,
            )
        )
        for node in range(size)
    )
    return RawNetwork(rows)  # type: ignore[arg-type]


def run_baseline_reference(
    *, size: int, seed: int, sweeps: int
) -> tuple[RawNetwork, object]:
    rng = Random(seed)
    targets = [list(row) for row in initial_network(size, rng).targets]
    for _ in range(sweeps * SLOT_COUNT * size):
        node = rng.randrange(size)
        slot = rng.randrange(SLOT_COUNT)
        blocked = {
            node,
            *(target for index, target in enumerate(targets[node]) if index != slot),
        }
        legal = tuple(
            candidate for candidate in range(size) if candidate not in blocked
        )
        targets[node][slot] = rng.choice(legal)
    network = RawNetwork(tuple(tuple(row) for row in targets))  # type: ignore[arg-type]
    return network, rng.getstate()


def q_simple_graph(network: RawNetwork) -> list[tuple[int, ...]]:
    adjacency = [set[int]() for _ in range(network.size)]
    for node, row in enumerate(network.targets):
        for slot, target in enumerate(row):
            if node < target and network.targets[target][slot] == node:
                adjacency[node].add(target)
                adjacency[target].add(node)
    return [tuple(sorted(row)) for row in adjacency]


def nontrivial_components(graph: Sequence[Sequence[int]]) -> list[set[int]]:
    seen: set[int] = set()
    components = []
    for root in range(len(graph)):
        if root in seen or not graph[root]:
            continue
        component = {root}
        seen.add(root)
        stack = [root]
        while stack:
            node = stack.pop()
            new = set(graph[node]) - seen
            seen.update(new)
            component.update(new)
            stack.extend(new)
        components.append(component)
    return components


def classify_structures(
    graph: Sequence[Sequence[int]], components: Iterable[set[int]]
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    structures = []
    for component in components:
        members = tuple(sorted(component))
        if len(members) == 2:
            kind = "pair"
        elif len(members) == 3 and all(
            right in graph[left]
            for left in members
            for right in members
            if left != right
        ):
            kind = "triangle"
        elif len(members) > 3:
            kind = "larger"
        else:
            kind = "other_three_node_component"
        structures.append((kind, members))
    return tuple(sorted(structures))


def network_hash(network: RawNetwork) -> str:
    return state_hash(network.targets)


def state_hash(value: object) -> str:
    return sha256(pickle.dumps(value, protocol=5)).hexdigest()


def _rate(numerator: int, denominator: int) -> float | str:
    return numerator / denominator if denominator else "N/A"
