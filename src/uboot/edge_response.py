"""Configurable fair-slot selection, response, and propagation experiments."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field, replace
from random import Random
from statistics import fmean
from math import ceil
from typing import Any, Callable

from uboot.kernel import SLOT_COUNT, RawNetwork
from uboot.observables import mutual_pair_count


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    p_incoming: float = 0.95
    p_same_slot_given_incoming: float = 1 / 3
    p_global_explore: float = 0.05
    prefer_new_consistency_weight: float = 1.0
    candidate_weight_mode: str = "uniform"
    candidate_mode: str = "incoming_global"

    def __post_init__(self) -> None:
        for value in (
            self.p_incoming,
            self.p_same_slot_given_incoming,
            self.p_global_explore,
        ):
            if not 0 <= value <= 1:
                raise ValueError("selection probabilities must be between zero and one")
        if abs(self.p_incoming + self.p_global_explore - 1) > 1e-9:
            raise ValueError("incoming and global probabilities must sum to one")
        if self.candidate_weight_mode != "uniform":
            raise NotImplementedError(
                f"candidate weight mode is reserved but not implemented: "
                f"{self.candidate_weight_mode}"
            )


@dataclass(frozen=True, slots=True)
class ResponsePolicy:
    p_no_return: float = 0.0
    p_same_return: float = 0.0
    p_other_return: float = 0.0
    p_multi_return: float = 0.0
    slot_no_return: str = "none"
    slot_same_return: str = "same_as_incoming"
    slot_other_return: str = "returning_slot"
    slot_multi_return: str = "random_slot"
    target_mode: str = "incoming_preferred"
    allow_chain: bool = False
    max_chain_depth: int = 100

    def __post_init__(self) -> None:
        for value in (
            self.p_no_return,
            self.p_same_return,
            self.p_other_return,
            self.p_multi_return,
        ):
            if not 0 <= value <= 1:
                raise ValueError("response probabilities must be between zero and one")
        if self.max_chain_depth < 0:
            raise ValueError("max chain depth cannot be negative")


@dataclass(frozen=True, slots=True)
class ExperimentPolicy:
    selection: SelectionPolicy = field(default_factory=SelectionPolicy)
    response: ResponsePolicy = field(default_factory=ResponsePolicy)
    scheduler_mode: str = "response_priority"
    p_process_response: float = 1.0


@dataclass(frozen=True, slots=True)
class CandidateGroups:
    incoming_same_slot: frozenset[int]
    incoming_other_slot: frozenset[int]
    incoming_any: frozenset[int]
    blocked_global: frozenset[int]


@dataclass(frozen=True, slots=True)
class EdgeEvent:
    event_id: int
    root_event_id: int
    thread_id: int
    source: int
    slot: int
    old_target: int
    new_target: int
    old_was_consistent: bool
    new_is_consistent: bool
    relation_to_new_target: str
    trigger_type: str
    parent_event_id: int | None
    depth: int
    return_class: str
    return_slots: tuple[int, ...]
    has_same_slot_return: bool
    response_generated: bool = False
    response_node: int | None = None
    response_slot_mode: str | None = None
    response_target_mode: str | None = None
    queue_length_after: int = 0
    background_sweep: int = 0


@dataclass(frozen=True, slots=True)
class ResponseRequest:
    node: int
    source_event_id: int
    root_event_id: int
    thread_id: int
    depth: int
    incoming_source: int
    incoming_slot: int
    return_slots_snapshot: tuple[int, ...]
    return_class: str


@dataclass(slots=True)
class ThreadState:
    length: int = 0
    visited_slot_states: set[tuple[int, int, int]] = field(default_factory=set)
    visited_node_slots: set[tuple[int, int]] = field(default_factory=set)
    visited_topologies: set[tuple[tuple[int, int, int], ...]] = field(
        default_factory=set
    )
    slot_sequence: list[int] = field(default_factory=list)
    closure: str | None = None


class EdgeResponseEngine:
    def __init__(
        self,
        network: RawNetwork,
        policy: ExperimentPolicy,
        rng: Random,
        worker_count: int = 1,
    ):
        if network.size < 4:
            raise ValueError("edge-response experiments require N >= 4")
        if any(len(set(row)) != SLOT_COUNT for row in network.targets):
            raise ValueError("each node must initially target three distinct nodes")
        self.targets = [list(row) for row in network.targets]
        self.incoming = [
            [set[int]() for _ in range(network.size)] for _ in range(SLOT_COUNT)
        ]
        for source, row in enumerate(self.targets):
            for slot, target in enumerate(row):
                self.incoming[slot][target].add(source)
        self.policy, self.rng = policy, rng
        self.queue: deque[ResponseRequest] = deque()
        self.counters: Counter[str] = Counter()
        self.active_attempts = [[0] * SLOT_COUNT for _ in self.targets]
        if worker_count < 1:
            raise ValueError("worker count must be positive")
        self.worker_count = worker_count
        self.threads: dict[int, ThreadState] = {}
        self.thread_length_histogram: Counter[int] = Counter()
        self._fair_ring: list[tuple[int, int]] = []
        self._fair_index = 0
        self._next_event = 1
        self._next_thread = 1
        self.background_sweep = 0
        response = policy.response
        self.responses_enabled = any(
            (
                response.p_no_return,
                response.p_same_return,
                response.p_other_return,
                response.p_multi_return,
            )
        )

    def run(
        self,
        background_sweeps: int,
        progress: Callable[[int, int, int, int], None] | None = None,
        *,
        progress_check_every: int = 1_024,
    ) -> None:
        target_attempts = background_sweeps * SLOT_COUNT * len(self.targets)
        scheduler_actions = 0
        while self.counters["active_attempts"] < target_attempts:
            if self.queue and (
                len(self.threads) >= self.worker_count
                or
                self.policy.scheduler_mode == "response_priority"
                or self.rng.random() < self.policy.p_process_response
            ):
                self._process_response(self.queue.popleft())
            else:
                node, slot = self._next_fair_slot()
                self._attempt(node, slot, "active", None)
            scheduler_actions += 1
            if progress is not None and scheduler_actions % progress_check_every == 0:
                progress(
                    self.counters["active_attempts"],
                    target_attempts,
                    self.counters["response_events"],
                    len(self.queue),
                )
        if self.policy.scheduler_mode == "response_priority":
            while self.queue:
                self._process_response(self.queue.popleft())
                scheduler_actions += 1
                if (
                    progress is not None
                    and scheduler_actions % progress_check_every == 0
                ):
                    progress(
                        self.counters["active_attempts"],
                        target_attempts,
                        self.counters["response_events"],
                        len(self.queue),
                    )
        if progress is not None:
            progress(
                self.counters["active_attempts"],
                target_attempts,
                self.counters["response_events"],
                len(self.queue),
            )

    def snapshot(self) -> RawNetwork:
        return RawNetwork(tuple(tuple(row) for row in self.targets))  # type: ignore[arg-type]

    def summary(self) -> dict[str, Any]:
        attempts = [value for row in self.active_attempts for value in row]
        length_histogram = Counter(self.thread_length_histogram)
        length_histogram.update(thread.length for thread in self.threads.values())
        length_count = sum(length_histogram.values())
        length_sum = sum(length * count for length, count in length_histogram.items())
        consistent = [self._consistent_count(slot) for slot in range(SLOT_COUNT)]
        result: dict[str, Any] = dict(self.counters)
        result.update(
            consistent_slot_0=consistent[0],
            consistent_slot_1=consistent[1],
            consistent_slot_2=consistent[2],
            total_consistent_count=sum(consistent),
            mutual_connection_count=mutual_pair_count(self.snapshot()),
            response_queue_max=self.counters["response_queue_max"],
            thread_count=self.counters["thread_count"],
            mean_thread_length=length_sum / max(1, length_count),
            median_thread_length=self._histogram_quantile(length_histogram, 0.5),
            max_thread_length=max(length_histogram, default=0),
            p90_thread_length=self._histogram_quantile(length_histogram, 0.9),
            closure_rate=(
                self.counters["closed_threads"]
                / max(1, self.counters["thread_count"])
            ),
            active_attempt_min=min(attempts),
            active_attempt_max=max(attempts),
            active_attempt_mean=fmean(attempts),
            response_queue_mean=(
                self.counters["queue_length_sum"]
                / max(1, self.counters["queue_length_samples"])
            ),
            same_slot_only_thread_count=self.counters["same_slot_only_thread_count"],
            slot_switch_thread_count=self.counters["slot_switch_thread_count"],
            active_worker_count=len(self.threads),
        )
        return result

    def _next_fair_slot(self) -> tuple[int, int]:
        if self._fair_index == len(self._fair_ring):
            self._fair_ring = [
                (node, slot)
                for node in range(len(self.targets))
                for slot in range(SLOT_COUNT)
            ]
            self.rng.shuffle(self._fair_ring)
            self._fair_index = 0
            self.background_sweep += 1
        item = self._fair_ring[self._fair_index]
        self._fair_index += 1
        return item

    def _attempt(
        self, node: int, slot: int, trigger: str, request: ResponseRequest | None
    ) -> EdgeEvent | None:
        if trigger == "active":
            self.active_attempts[node][slot] += 1
            self.counters["active_attempts"] += 1
        old = self.targets[node][slot]
        excluded = set(self.targets[node]) | {node}
        if request is not None:
            excluded.add(request.incoming_source)
        groups = self._groups(node, slot, old, excluded)
        mode = (
            self.policy.response.target_mode if request else self.policy.selection.candidate_mode
        )
        target, relation = self._select(node, slot, groups, mode)
        if target is None:
            self.counters["no_candidate"] += 1
            return None
        old_consistent = self.is_consistent(node, old, slot)
        self._retarget(node, slot, target)
        return_slots = tuple(
            index for index, value in enumerate(self.targets[target]) if value == node
        )
        return_class = self._return_class(slot, return_slots)
        new_consistent = self.is_consistent(node, target, slot)
        self.counters[f"{return_class.lower()}_event_count"] += 1
        self.counters[f"{relation}_selected"] += 1
        if old_consistent and new_consistent:
            self.counters["both_lost_and_created_same_event"] += 1
        elif old_consistent:
            self.counters["old_consistency_lost_statistically"] += 1
        elif new_consistent:
            self.counters["new_consistency_created_statistically"] += 1
        else:
            self.counters["neither_consistency_change"] += 1
        self.counters[f"{trigger}_events"] += 1
        if not self.responses_enabled:
            return None
        thread_id = request.thread_id if request else self._next_thread
        if request is None:
            self._next_thread += 1
        event_id = self._next_event
        self._next_event += 1
        event = EdgeEvent(
            event_id, request.root_event_id if request else event_id, thread_id,
            node, slot, old, target, old_consistent,
            new_consistent, relation, trigger,
            request.source_event_id if request else None,
            request.depth if request else 0, return_class, return_slots,
            slot in return_slots, background_sweep=self.background_sweep,
        )
        if request is not None:
            self._update_thread(event)
        generated = self._maybe_enqueue(event)
        if request is None and generated:
            self.threads[thread_id] = ThreadState()
            self.counters["thread_count"] += 1
            self._update_thread(event)
        event = replace(
            event,
            response_generated=generated,
            response_node=event.new_target if generated else None,
            response_slot_mode=(
                self._slot_mode_for(event.return_class) if generated else None
            ),
            response_target_mode=(
                self.policy.response.target_mode if generated else None
            ),
            queue_length_after=len(self.queue),
        )
        self.counters["queue_length_sum"] += len(self.queue)
        self.counters["queue_length_samples"] += 1
        return event

    def _groups(self, node: int, slot: int, old: int, excluded: set[int]) -> CandidateGroups:
        same = self.incoming[slot][node] - excluded
        any_in = set().union(*(self.incoming[s][node] for s in range(SLOT_COUNT)))
        any_in -= excluded
        other = any_in - same
        return CandidateGroups(frozenset(same), frozenset(other),
                               frozenset(any_in), frozenset(excluded | any_in))

    def _select(self, node: int, slot: int, groups: CandidateGroups, mode: str) -> tuple[int | None, str]:
        if mode == "legacy_endogenous":
            candidates = set(groups.incoming_any)
            for neighbor in self.targets[node]:
                candidates.update(self.targets[neighbor])
            candidates -= set(self.targets[node]) | {node}
            return self._choice(candidates), "legacy_endogenous"
        if mode == "escape_to_nonincoming" or mode == "uniform_global":
            return self._global_choice(groups.blocked_global), "global_nonincoming"
        prefer_same = mode == "same_slot_incoming_preferred"
        choose_incoming = self.rng.random() < self.policy.selection.p_incoming
        if choose_incoming:
            choose_same = prefer_same or self.rng.random() < self.policy.selection.p_same_slot_given_incoming
            first, second = ((groups.incoming_same_slot, groups.incoming_other_slot)
                             if choose_same else (groups.incoming_other_slot, groups.incoming_same_slot))
            if first or second:
                pool = first or second
                relation = "same_slot_incoming" if pool is groups.incoming_same_slot else "other_slot_incoming"
                return self._choice(pool), relation
        return self._global_choice(groups.blocked_global), "global_nonincoming"

    def _choice(self, values: Any) -> int | None:
        values = tuple(values)
        return self.rng.choice(values) if values else None

    def _global_choice(self, blocked: frozenset[int]) -> int | None:
        size = len(self.targets)
        if len(blocked) >= size:
            return None
        while True:
            candidate = self.rng.randrange(size)
            if candidate not in blocked:
                return candidate

    def _retarget(self, node: int, slot: int, target: int) -> None:
        old = self.targets[node][slot]
        self.incoming[slot][old].remove(node)
        self.incoming[slot][target].add(node)
        self.targets[node][slot] = target

    def is_consistent(self, a: int, b: int, slot: int) -> bool:
        return self.targets[a][slot] == b and self.targets[b][slot] == a

    def _consistent_count(self, slot: int) -> int:
        return sum(self.is_consistent(a, b, slot) for a, b in enumerate(row[slot] for row in self.targets) if a < b)

    def _return_class(self, slot: int, returns: tuple[int, ...]) -> str:
        if len(returns) > 1:
            return "MULTI_RETURN"
        if not returns:
            return "NO_RETURN"
        return "SAME_RETURN" if returns[0] == slot else "OTHER_RETURN"

    def _maybe_enqueue(self, event: EdgeEvent) -> bool:
        policy = self.policy.response
        probability = {
            "NO_RETURN": policy.p_no_return, "SAME_RETURN": policy.p_same_return,
            "OTHER_RETURN": policy.p_other_return, "MULTI_RETURN": policy.p_multi_return,
        }[event.return_class]
        if self.rng.random() >= probability or not policy.allow_chain and event.depth > 0:
            return False
        if event.depth >= policy.max_chain_depth:
            self.counters["terminated_max_depth"] += 1
            return False
        self.queue.append(ResponseRequest(event.new_target, event.event_id,
            event.root_event_id, event.thread_id, event.depth + 1, event.source,
            event.slot, event.return_slots, event.return_class))
        self.counters[f"responses_generated_{event.return_class.lower()}"] += 1
        self.counters["response_queue_max"] = max(self.counters["response_queue_max"], len(self.queue))
        return True

    def _process_response(self, request: ResponseRequest) -> None:
        if self.targets[request.incoming_source][request.incoming_slot] != request.node:
            self.counters["responses_obsolete"] += 1
            self._finalize_thread(request.thread_id, "obsolete")
            return
        slot = self._response_slot(request)
        if slot is None:
            self._finalize_thread(request.thread_id, "no_slot")
            return
        self.counters["responses_processed"] += 1
        event = self._attempt(request.node, slot, "response", request)
        if event is None or not event.response_generated:
            self._finalize_thread(request.thread_id, "complete")

    def _response_slot(self, request: ResponseRequest) -> int | None:
        mode = self._slot_mode_for(request.return_class)
        if mode == "none":
            return None
        if mode == "same_as_incoming":
            return request.incoming_slot
        if mode == "returning_slot":
            return (
                self.rng.choice(request.return_slots_snapshot)
                if request.return_slots_snapshot
                else None
            )
        if mode == "third_slot":
            remaining = set(range(SLOT_COUNT)) - {request.incoming_slot} - set(request.return_slots_snapshot)
            return self._choice(remaining)
        if mode == "random_slot":
            return self.rng.randrange(SLOT_COUNT)
        if mode == "weighted_slot":
            raise NotImplementedError("weighted response-slot mode is reserved")
        raise ValueError(f"unknown response slot mode: {mode}")

    def _slot_mode_for(self, return_class: str) -> str:
        p = self.policy.response
        return {
            "NO_RETURN": p.slot_no_return,
            "SAME_RETURN": p.slot_same_return,
            "OTHER_RETURN": p.slot_other_return,
            "MULTI_RETURN": p.slot_multi_return,
        }[return_class]

    def _update_thread(self, event: EdgeEvent) -> None:
        thread = self.threads[event.thread_id]
        state = (event.source, event.slot, event.new_target)
        node_slot = (event.source, event.slot)
        topology = self._local_topology_fingerprint(event.source, event.new_target)
        if state in thread.visited_slot_states:
            thread.closure = "slot_state_revisit"
        elif node_slot in thread.visited_node_slots:
            thread.closure = "node_slot_revisit"
        elif topology in thread.visited_topologies:
            thread.closure = "topology_revisit"
        thread.visited_slot_states.add(state)
        thread.visited_node_slots.add(node_slot)
        thread.visited_topologies.add(topology)
        thread.slot_sequence.append(event.slot)
        thread.length += 1

    def _finalize_thread(self, thread_id: int, reason: str) -> None:
        thread = self.threads.pop(thread_id, None)
        if thread is None:
            return
        self.thread_length_histogram[thread.length] += 1
        self.counters[f"terminated_{reason}"] += 1
        if thread.closure is not None:
            self.counters["closed_threads"] += 1
            self.counters[f"closure_{thread.closure}"] += 1
        if len(set(thread.slot_sequence)) <= 1:
            self.counters["same_slot_only_thread_count"] += 1
        else:
            self.counters["slot_switch_thread_count"] += 1

    def _histogram_quantile(self, histogram: Counter[int], quantile: float) -> int:
        total = sum(histogram.values())
        if total == 0:
            return 0
        target = ceil(quantile * total)
        cumulative = 0
        for value in sorted(histogram):
            cumulative += histogram[value]
            if cumulative >= target:
                return value
        return 0

    def _local_topology_fingerprint(
        self, source: int, target: int
    ) -> tuple[tuple[int, int, int], ...]:
        local = {source, target}
        local.update(self.targets[source])
        local.update(self.targets[target])
        consensus = {
            (min(node, other), slot, max(node, other))
            for node in local
            for slot, other in enumerate(self.targets[node])
            if other in local and self.is_consistent(node, other, slot)
        }
        return tuple(sorted(consensus))


def distinct_random_network(size: int, rng: Random) -> RawNetwork:
    if size < 4:
        raise ValueError("N must be at least 4 for three distinct non-self targets")
    rows = []
    for source in range(size):
        candidates = [node for node in range(size) if node != source]
        rows.append(tuple(rng.sample(candidates, SLOT_COUNT)))
    return RawNetwork(tuple(rows))  # type: ignore[arg-type]


def policy_profile(name: str, **overrides: Any) -> ExperimentPolicy:
    selection = SelectionPolicy(**overrides.get("selection", {}))
    base = dict(allow_chain=True, max_chain_depth=100)
    profiles: dict[str, dict[str, Any]] = {
        "M0": dict(allow_chain=False),
        "M1": dict(p_same_return=1, slot_same_return="same_as_incoming"),
        "M2": dict(p_same_return=1, slot_same_return="same_as_incoming",
                   target_mode="escape_to_nonincoming"),
        "M3": dict(p_other_return=1, slot_other_return="returning_slot"),
        "M4": dict(p_other_return=1, slot_other_return="third_slot"),
        "M5": dict(p_no_return=0.1, p_same_return=0.1, p_other_return=0.1,
                   slot_no_return="same_as_incoming"),
        "M6": {},
        "LEGACY": dict(allow_chain=False),
    }
    if name not in profiles:
        raise ValueError(f"unknown policy profile: {name}")
    base.update(profiles[name])
    base.update(overrides.get("response", {}))
    if name == "LEGACY":
        selection = SelectionPolicy(candidate_mode="legacy_endogenous")
    return ExperimentPolicy(selection, ResponsePolicy(**base),
                            overrides.get("scheduler_mode", "response_priority"),
                            overrides.get("p_process_response", 1.0))
