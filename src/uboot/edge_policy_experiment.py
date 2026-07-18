"""Independent edge-policy comparison runner and shared measurements."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from random import Random
from statistics import fmean
from typing import Any, Callable

from uboot.candidate_graph import strongly_connected_components
from uboot.edge_change_policies import (
    EdgeChangePolicy,
    LocalContext,
    POLICY_SEED_OFFSETS,
    PolicyParameters,
    RelationState,
    build_policy,
    strength_band,
    transition_band,
)
from uboot.edge_response import (
    EdgeResponseEngine,
    distinct_random_network,
    policy_profile,
)
from uboot.kernel import SLOT_COUNT, RawNetwork
from uboot.snapshot_lineage import same_meaning_mutual_pairs


@dataclass(frozen=True, slots=True)
class PolicyRunResult:
    policy: str
    seed: int
    size: int
    sweeps: int
    final_hash: str
    main_rng_state: object
    summary: dict[str, Any]
    time_series: tuple[dict[str, Any], ...]
    strength_histogram: tuple[dict[str, Any], ...]
    transitions: tuple[dict[str, Any], ...]
    component_stats: tuple[dict[str, Any], ...]
    internal_external_stats: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    config: dict[str, Any]


class PolicyComparisonEngine(EdgeResponseEngine):
    """Experimental engine; the default EdgeResponseEngine remains untouched."""

    def __init__(
        self,
        network: RawNetwork,
        main_rng: Random,
        edge_policy: EdgeChangePolicy,
        policy_rng: Random,
        *,
        write_events: bool = False,
    ) -> None:
        super().__init__(
            network,
            policy_profile("M2"),
            main_rng,
            worker_count=1,
            mode="M1",
            candidate_rule="endogenous_in_out2",
        )
        self.edge_policy = edge_policy
        self.policy_rng = policy_rng
        self.relation_states = [
            [RelationState(target) for target in row] for row in self.targets
        ]
        self.edge_counts: Counter[str] = Counter()
        self.transition_counts: Counter[tuple[str, str]] = Counter()
        self.completed_lifetimes: dict[str, list[int]] = {
            "internal": [],
            "external": [],
        }
        self.write_events = write_events
        self.edge_events: list[dict[str, Any]] = []

    def global_candidates(
        self,
        node: int,
        slot: int,
        *,
        additionally_excluded: int | None = None,
    ) -> tuple[int, ...]:
        blocked = {
            node,
            *(
                target
                for index, target in enumerate(self.targets[node])
                if index != slot
            ),
        }
        if additionally_excluded is not None:
            blocked.add(additionally_excluded)
        return tuple(
            target for target in range(len(self.targets)) if target not in blocked
        )

    def _active_update(self, node: int, slot: int) -> None:
        self.active_attempts[node][slot] += 1
        old = self.targets[node][slot]
        proposed = self.edge_policy.choose_candidate(
            self.global_candidates(node, slot), old, self.policy_rng
        )
        if proposed is None:
            self.counters["active_no_candidate"] += 1
            return
        self._apply_policy_decision(node, slot, proposed, "active")
        self.counters["active_updates"] += 1
        self._create_response(node, slot, old, self.targets[node][slot])

    def _advance_worker(self, worker_id: int) -> None:
        task_id = self.workers[worker_id]
        if task_id is None:
            return
        task = self.threads[task_id]
        if self.targets[task.incoming_source][task.incoming_slot] != task.current_node:
            self._finish_worker(worker_id, "invalidated")
            return
        slot = self._response_slot(task)
        if slot is None:
            self._finish_worker(worker_id, "absorbed")
            return
        source = task.current_node
        proposed = self.edge_policy.choose_candidate(
            self.global_candidates(
                source, slot, additionally_excluded=task.incoming_source
            ),
            self.targets[source][slot],
            self.policy_rng,
        )
        if proposed is None:
            self._finish_worker(worker_id, "absorbed")
            return
        self._apply_policy_decision(source, slot, proposed, "response")
        task.hop_count += 1
        task.chain_length += 1
        current_target = self.targets[source][slot]
        return_slots = tuple(
            index
            for index, value in enumerate(self.targets[current_target])
            if value == source
        )
        kind = self._return_class(slot, return_slots)
        if task.hop_count >= self.policy.response.max_chain_depth:
            self._finish_worker(worker_id, "max_hops")
            return
        if (
            not self.policy.response.allow_chain
            or self.rng.random() >= self._response_probability(kind)
        ):
            self._finish_worker(worker_id, "completed")
            return
        task.current_node = current_target
        task.incoming_source = source
        task.incoming_slot = slot
        task.return_slots = return_slots
        task.response_kind = kind

    def _apply_policy_decision(
        self, node: int, slot: int, proposed: int, event_kind: str
    ) -> None:
        state = self.relation_states[node][slot]
        old_target = state.target_id
        context = self.local_context(node, slot, old_target, event_kind)
        old_reciprocal = context.reciprocal
        old_strength = state.strength
        old_internal = state.internal
        self.edge_counts[f"{event_kind}_attempt_count"] += 1
        self.edge_counts[
            f"{'internal' if old_internal else 'external'}_slot_attempt_count"
        ] += 1
        other_internal = any(
            index != slot and item.internal
            for index, item in enumerate(self.relation_states[node])
        )
        if other_internal and not old_internal:
            self.edge_counts["same_node_external_attempt_after_internal"] += 1
        decision = (
            self.edge_policy.evaluate_active_change(
                state, proposed, context, self.policy_rng
            )
            if event_kind == "active"
            else self.edge_policy.evaluate_response_effect(
                state, proposed, context, self.policy_rng
            )
        )
        self.edge_counts[decision.action] += 1
        if proposed != old_target:
            self.edge_counts["new_target_proposed"] += 1
        if decision.accepted and decision.new_target != old_target:
            lifetime = self.tick - state.born_tick
            self.completed_lifetimes["internal" if old_internal else "external"].append(
                lifetime
            )
            super()._retarget(node, slot, int(decision.new_target))
            self.edge_counts[f"{event_kind}_change_accepted"] += 1
            self.edge_counts[
                f"{'internal' if old_internal else 'external'}_slot_change_count"
            ] += 1
            if other_internal and not old_internal:
                self.edge_counts["same_node_external_change_after_internal"] += 1
        updated = self.edge_policy.update_relation_strength(
            state,
            decision,
            context,
            self.tick,
        )
        if updated.target_id != self.targets[node][slot]:
            updated = replace(updated, target_id=self.targets[node][slot])
        new_context = self.local_context(
            node, slot, self.targets[node][slot], event_kind
        )
        updated = self._refresh_internal(updated, new_context)
        updated = self._refresh_hard_lock(updated, new_context)
        self.relation_states[node][slot] = updated
        self._record_strength_change(old_strength, updated.strength)
        self._record_identity_change(old_internal, updated)
        new_reciprocal = new_context.reciprocal
        if new_reciprocal and not old_reciprocal:
            self.edge_counts["reciprocity_formed"] += 1
        if old_reciprocal and not new_reciprocal:
            self.edge_counts["reciprocity_lost"] += 1
        self._sync_hard_reciprocal(node, slot, new_context)
        if self.write_events:
            self.edge_events.append(
                {
                    "tick": self.tick,
                    "event_kind": event_kind,
                    "node": node,
                    "slot": slot,
                    "old_target": old_target,
                    "proposed_target": proposed,
                    "final_target": self.targets[node][slot],
                    "action": decision.action,
                    "reason": decision.reason,
                    "strength_before": old_strength,
                    "strength_after": updated.strength,
                    "internal_before": old_internal,
                    "internal_after": updated.internal,
                }
            )

    def _refresh_internal(
        self, state: RelationState, context: LocalContext
    ) -> RelationState:
        params = self.edge_policy.parameters
        if state.internal and state.strength < params.demote_threshold:
            return replace(state, internal=False, hard_locked=False)
        if not state.internal and self.edge_policy.may_promote_internal(state, context):
            return replace(state, internal=True)
        return state

    def _refresh_hard_lock(
        self, state: RelationState, context: LocalContext
    ) -> RelationState:
        if self.edge_policy.name == "hard_reciprocal_lock" and context.reciprocal:
            return replace(state, internal=True, hard_locked=True)
        if (
            self.edge_policy.name == "certified_internal_hard_external_free"
            and state.internal
            and context.reciprocal
            and context.closure_support_level >= 2
        ):
            return replace(state, hard_locked=True)
        return state

    def _sync_hard_reciprocal(
        self, node: int, slot: int, context: LocalContext
    ) -> None:
        state = self.relation_states[node][slot]
        if not context.reciprocal or not state.hard_locked:
            return
        target = self.targets[node][slot]
        partner = self.relation_states[target][slot]
        self.relation_states[target][slot] = replace(
            partner, internal=True, hard_locked=True
        )

    def _record_strength_change(self, before: float, after: float) -> None:
        if after > before:
            self.edge_counts["strength_increased"] += 1
        elif after < before:
            self.edge_counts["strength_decreased"] += 1
        if before == 0 and after == 1:
            self.edge_counts["direct_zero_to_one_count"] += 1
        if before == 1 and after == 0:
            self.edge_counts["direct_one_to_zero_count"] += 1
        self.transition_counts[(transition_band(before), transition_band(after))] += 1

    def _record_identity_change(
        self, before_internal: bool, after: RelationState
    ) -> None:
        if after.internal and not before_internal:
            self.edge_counts["promoted_internal"] += 1
        if before_internal and not after.internal:
            self.edge_counts["demoted_external"] += 1
        if after.hard_locked:
            self.edge_counts["hard_locked_observation"] += 1

    def local_context(
        self, node: int, slot: int, target: int, event_kind: str
    ) -> LocalContext:
        reciprocal = self.targets[target][slot] == node
        level = 1 if reciprocal else 0
        if reciprocal and self._has_mutual_triangle_support(node, target):
            level = 2
        return LocalContext(reciprocal, level, event_kind)

    def _has_mutual_triangle_support(self, left: int, right: int) -> bool:
        left_neighbors = self._mutual_neighbors(left)
        right_neighbors = self._mutual_neighbors(right)
        return bool((left_neighbors & right_neighbors) - {left, right})

    def _mutual_neighbors(self, node: int) -> set[int]:
        return {target for target in self.targets[node] if node in self.targets[target]}


def run_policy_comparison(
    *,
    size: int,
    seed: int,
    sweeps: int,
    snapshot_sweeps: tuple[int, ...],
    policy_names: tuple[str, ...],
    policy_parameters: dict[str, PolicyParameters] | None = None,
    write_events: bool = False,
    progress: Callable[[str, int, int, int, int], None] | None = None,
) -> tuple[PolicyRunResult, ...]:
    parameters = policy_parameters or {}
    results = []
    for name in policy_names:
        main_rng = Random(seed)
        initial = distinct_random_network(size, main_rng)
        if name == "baseline_current":
            engine: EdgeResponseEngine = EdgeResponseEngine(
                initial,
                policy_profile("M2"),
                main_rng,
                worker_count=1,
                mode="M1",
                candidate_rule="endogenous_in_out2",
            )
            edge_policy = None
        else:
            edge_policy = build_policy(name, parameters.get(name))
            engine = PolicyComparisonEngine(
                initial,
                main_rng,
                edge_policy,
                Random(seed + POLICY_SEED_OFFSETS[name]),
                write_events=write_events,
            )
        rows = []
        for checkpoint in sorted(set(snapshot_sweeps) | {0, sweeps}):
            if checkpoint > sweeps:
                continue
            callback = None
            if progress is not None:

                def callback(
                    tick: int,
                    total: int,
                    responses: int,
                    queued: int,
                    policy_name: str = name,
                ) -> None:
                    progress(policy_name, tick, total, responses, queued)

            engine.run(checkpoint, progress=callback)
            rows.append(policy_snapshot(name, seed, engine, checkpoint))
        results.append(_result(name, seed, size, sweeps, engine, edge_policy, rows))
    return tuple(results)


def policy_snapshot(
    policy_name: str,
    seed: int,
    engine: EdgeResponseEngine,
    sweep: int,
) -> dict[str, Any]:
    network = engine.snapshot()
    q_graph = reciprocal_graph(network)
    candidate = graph_metrics(engine.candidate_graph())
    if isinstance(engine, PolicyComparisonEngine):
        threshold = engine.edge_policy.parameters.strength_graph_threshold
        s_graph = relation_state_graph(
            engine, lambda state: state.strength >= threshold
        )
        l_graph = relation_state_graph(engine, lambda state: state.internal)
        edge_counts = engine.edge_counts
        states = [state for row in engine.relation_states for state in row]
        uses_strength = engine.edge_policy.uses_strength
    else:
        s_graph = empty_graph(network.size)
        l_graph = empty_graph(network.size)
        edge_counts = Counter()
        states = []
        uses_strength = False
    q = graph_metrics(q_graph)
    s = graph_metrics(s_graph)
    internal = graph_metrics(l_graph)
    row: dict[str, Any] = {
        "policy": policy_name,
        "seed": seed,
        "N": network.size,
        "sweep": sweep,
        "tick": engine.tick,
        "actual_relation_count": SLOT_COUNT * network.size,
        "current_candidate_g": candidate["max_scc"] / max(1, network.size),
        **{f"C_{key}": value for key, value in candidate.items()},
        "reciprocal_relation_count": sum(map(len, q_graph)),
        "high_strength_relation_count": sum(map(len, s_graph)),
        "internal_relation_count": sum(map(len, l_graph)),
        **{f"Q_{key}": value for key, value in q.items()},
        **{f"S_{key}": value for key, value in s.items()},
        **{f"L_{key}": value for key, value in internal.items()},
        "internal_slot_change_rate": _rate(
            edge_counts["internal_slot_change_count"],
            edge_counts["internal_slot_attempt_count"],
        ),
        "external_slot_change_rate": _rate(
            edge_counts["external_slot_change_count"],
            edge_counts["external_slot_attempt_count"],
        ),
        "same_node_external_change_rate_after_internal": _rate(
            edge_counts["same_node_external_change_after_internal"],
            edge_counts["same_node_external_attempt_after_internal"],
        ),
        "strength_midrange_ratio": (
            sum(0.2 <= state.strength < 0.8 for state in states) / len(states)
            if states and uses_strength
            else "N/A"
        ),
        "promote_count": edge_counts["promoted_internal"],
        "demote_count": edge_counts["demoted_external"],
        "change_resisted_count": edge_counts["change_resisted"],
        "change_accepted_count": edge_counts["change_accepted"],
        "same_target_sampled_count": edge_counts["same_target_sampled"],
        "direct_zero_to_one_count": edge_counts["direct_zero_to_one_count"],
        "direct_one_to_zero_count": edge_counts["direct_one_to_zero_count"],
    }
    row.update(_scores(row))
    return row


def reciprocal_graph(network: RawNetwork) -> list[tuple[int, ...]]:
    adjacency = [set[int]() for _ in range(network.size)]
    for left, right, _ in same_meaning_mutual_pairs(network):
        adjacency[left].add(right)
        adjacency[right].add(left)
    return [tuple(sorted(row)) for row in adjacency]


def relation_state_graph(
    engine: PolicyComparisonEngine,
    predicate: Any,
) -> list[tuple[int, ...]]:
    return [
        tuple(state.target_id for state in row if predicate(state))
        for row in engine.relation_states
    ]


def empty_graph(size: int) -> list[tuple[int, ...]]:
    return [tuple() for _ in range(size)]


def graph_metrics(graph: list[tuple[int, ...]]) -> dict[str, int | float]:
    sccs = strongly_connected_components(graph)
    weak = weak_components(graph)
    nontrivial = [group for group in weak if len(group) >= 2]
    undirected = [set(row) for row in graph]
    for source, row in enumerate(graph):
        for target in row:
            undirected[target].add(source)
    motif_triangle_count = 0
    for left in range(len(graph)):
        for middle in (node for node in undirected[left] if node > left):
            motif_triangle_count += sum(
                right > middle for right in undirected[left] & undirected[middle]
            )
    triangle_count = sum(
        len(group) == 3
        and all(
            right in undirected[left]
            for left in group
            for right in group
            if left != right
        )
        for group in nontrivial
    )
    return {
        "scc_count": len(sccs),
        "max_scc": max(map(len, sccs), default=0),
        "component_count": len(nontrivial),
        "pair_count": sum(len(group) == 2 for group in nontrivial),
        "triangle_count": triangle_count,
        "triangle_motif_count": motif_triangle_count,
        "triad_component_count": sum(len(group) == 3 for group in nontrivial),
        "larger_component_count": sum(len(group) > 3 for group in nontrivial),
        "max_component": max(map(len, weak), default=0),
        "node_coverage": sum(map(len, nontrivial)) / max(1, len(graph)),
    }


def weak_components(graph: list[tuple[int, ...]]) -> list[set[int]]:
    undirected = [set(row) for row in graph]
    for source, row in enumerate(graph):
        for target in row:
            undirected[target].add(source)
    seen: set[int] = set()
    result = []
    for root in range(len(graph)):
        if root in seen:
            continue
        group = {root}
        seen.add(root)
        stack = [root]
        while stack:
            new = undirected[stack.pop()] - seen
            seen.update(new)
            group.update(new)
            stack.extend(new)
        result.append(group)
    return result


def _result(
    name: str,
    seed: int,
    size: int,
    sweeps: int,
    engine: EdgeResponseEngine,
    edge_policy: EdgeChangePolicy | None,
    rows: list[dict[str, Any]],
) -> PolicyRunResult:
    final = rows[-1]
    if isinstance(engine, PolicyComparisonEngine):
        states = [state for row in engine.relation_states for state in row]
        if edge_policy is not None and edge_policy.uses_strength:
            histogram = tuple(
                {
                    "policy": name,
                    "seed": seed,
                    "strength_band": band,
                    "count": sum(
                        strength_band(state.strength) == band for state in states
                    ),
                }
                for band in (
                    "[0.0,0.2)",
                    "[0.2,0.4)",
                    "[0.4,0.6)",
                    "[0.6,0.8)",
                    "[0.8,1.0]",
                )
            )
            transitions = tuple(
                {
                    "policy": name,
                    "seed": seed,
                    "from_band": source,
                    "to_band": target,
                    "count": engine.transition_counts[(source, target)],
                }
                for source in ("low", "medium", "high")
                for target in ("low", "medium", "high")
            )
        else:
            histogram = (
                {
                    "policy": name,
                    "seed": seed,
                    "strength_band": "N/A",
                    "count": "N/A",
                },
            )
            transitions = (
                {
                    "policy": name,
                    "seed": seed,
                    "from_band": "N/A",
                    "to_band": "N/A",
                    "count": "N/A",
                },
            )
        internal_lifetimes = engine.completed_lifetimes["internal"]
        external_lifetimes = engine.completed_lifetimes["external"]
        final.update(
            sweeps=sweeps,
            mean_internal_relation_lifetime=(
                fmean(internal_lifetimes) if internal_lifetimes else "N/A"
            ),
            mean_external_relation_lifetime=(
                fmean(external_lifetimes) if external_lifetimes else "N/A"
            ),
        )
        events = tuple(engine.edge_events)
        config = edge_policy.config() if hasattr(edge_policy, "config") else {}
    else:
        histogram = (
            {
                "policy": name,
                "seed": seed,
                "strength_band": "N/A",
                "count": "N/A",
            },
        )
        transitions = (
            {
                "policy": name,
                "seed": seed,
                "from_band": "N/A",
                "to_band": "N/A",
                "count": "N/A",
            },
        )
        final.update(
            sweeps=sweeps,
            mean_internal_relation_lifetime="N/A",
            mean_external_relation_lifetime="N/A",
        )
        events = tuple()
        config = {"name": "baseline_current", "relation_strength": "disabled"}
    component_rows = tuple(
        {
            key: value
            for key, value in row.items()
            if key in {"policy", "seed", "N", "sweep", "tick"}
            or key.startswith(("C_", "Q_", "S_", "L_"))
        }
        for row in rows
    )
    internal_external = tuple(
        {
            key: value
            for key, value in row.items()
            if key in {"policy", "seed", "N", "sweep", "tick"}
            or "change_rate" in key
            or key
            in {
                "actual_relation_count",
                "reciprocal_relation_count",
                "high_strength_relation_count",
                "internal_relation_count",
                "promote_count",
                "demote_count",
            }
        }
        for row in rows
    )
    return PolicyRunResult(
        name,
        seed,
        size,
        sweeps,
        _engine_hash(engine),
        engine.rng.getstate(),
        final,
        tuple(rows),
        histogram,
        transitions,
        component_rows,
        internal_external,
        events,
        config,
    )


def _engine_hash(engine: EdgeResponseEngine) -> str:
    from hashlib import sha256
    import pickle

    return sha256(pickle.dumps(engine.dynamics_state(), protocol=5)).hexdigest()


def _rate(numerator: int, denominator: int) -> float | str:
    return numerator / denominator if denominator else "N/A"


def _scores(row: dict[str, Any]) -> dict[str, float]:
    internal_count = float(row["internal_relation_count"])
    external_rate = row["external_slot_change_rate"]
    same_node_rate = row["same_node_external_change_rate_after_internal"]
    midrange = row["strength_midrange_ratio"]
    max_internal = float(row["L_max_component"])
    internal_stability = min(
        1.0, internal_count / max(1.0, row["actual_relation_count"] / 6)
    )
    external_mobility = (
        float(external_rate) if isinstance(external_rate, float) else 0.0
    )
    isolation = float(same_node_rate) if isinstance(same_node_rate, float) else 0.0
    semicontinuous = float(midrange) if isinstance(midrange, float) else 0.0
    overhard = max(0.0, internal_count / max(1, row["actual_relation_count"]) - 0.5)
    oversoft = 1.0 if internal_count == 0 and row["S_pair_count"] == 0 else 0.0
    overhard += max(0.0, max_internal / max(1, row["N"]) - 0.5)
    return {
        "internal_stability_score": internal_stability,
        "external_mobility_score": external_mobility,
        "internal_external_isolation_score": isolation,
        "semicontinuous_transition_score": semicontinuous,
        "overhard_penalty": overhard,
        "oversoft_penalty": oversoft,
        "total_score": (
            internal_stability
            + external_mobility
            + isolation
            + semicontinuous
            - overhard
            - oversoft
        ),
    }


def policy_config_from_mapping(mapping: dict[str, Any]) -> dict[str, PolicyParameters]:
    return {name: PolicyParameters(**values) for name, values in mapping.items()}


def policy_configs_json(results: tuple[PolicyRunResult, ...]) -> dict[str, Any]:
    return {result.policy: result.config for result in results}
