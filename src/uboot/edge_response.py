"""M0/M1 slot dynamics with bounded cross-tick response workers."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from math import ceil
from random import Random
from typing import Any, Callable

from uboot.candidate_graph import get_current_candidate_targets
from uboot.kernel import SLOT_COUNT, RawNetwork
from uboot.snapshot_lineage import histogram_bucket, layered_topology_stats

M2_BASELINE_MODEL_NAME = "M2_distinct_target_candidate_baseline"
M2_BASELINE_MODEL_VERSION = "2.0.0"
M2_BASELINE_GIT_TAG = "m2-distinct-target-baseline-v2"


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    p_incoming: float = 0.95
    p_same_slot_given_incoming: float = 1 / 3
    p_global_explore: float = 0.05
    prefer_new_consistency_weight: float = 1.0
    candidate_weight_mode: str = "uniform"
    candidate_mode: str = "incoming_global"

    def __post_init__(self) -> None:
        for value in (self.p_incoming, self.p_same_slot_given_incoming, self.p_global_explore):
            if not 0 <= value <= 1:
                raise ValueError("selection probabilities must be between zero and one")
        if abs(self.p_incoming + self.p_global_explore - 1) > 1e-9:
            raise ValueError("incoming and global probabilities must sum to one")
        if self.candidate_weight_mode != "uniform":
            raise NotImplementedError(
                "candidate weight mode is reserved but not implemented: "
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
    scheduler_mode: str = "tick"
    p_process_response: float = 1.0


@dataclass(slots=True)
class ResponseTask:
    task_id: int
    start_tick: int
    current_node: int
    incoming_source: int
    incoming_slot: int
    return_slots: tuple[int, ...]
    response_kind: str
    chain_length: int = 1
    hop_count: int = 0
    source_candidate_size: int = 0
    target_candidate_size: int = 0
    source_has_mutual: bool = False
    target_points_back: bool = False
    target_same_meaning: bool = False


class EdgeResponseEngine:
    """One active-slot update per tick; workers advance first, once per tick."""

    def __init__(
        self,
        network: RawNetwork,
        policy: ExperimentPolicy,
        rng: Random,
        worker_count: int = 1,
        response_queue_capacity: int = 1024,
        response_queue_policy: str = "reject",
        enable_worker_stats: bool = False,
        enable_response_histograms: bool = False,
        mode: str | None = None,
        candidate_rule: str = "incoming_excluding_out",
    ):
        if network.size < 4:
            raise ValueError("edge-response experiments require N >= 4")
        if any(len(set(row)) != SLOT_COUNT for row in network.targets):
            raise ValueError("each node must initially target three distinct nodes")
        if worker_count < 0 or response_queue_capacity < 0:
            raise ValueError("worker and queue capacities cannot be negative")
        if response_queue_policy not in {"reject", "drop_oldest"}:
            raise ValueError("response queue policy must be reject or drop_oldest")
        self.targets = [list(row) for row in network.targets]
        self.incoming = [[set[int]() for _ in self.targets] for _ in range(SLOT_COUNT)]
        for source, row in enumerate(self.targets):
            for slot, target in enumerate(row):
                self.incoming[slot][target].add(source)
        self.policy = policy
        self.rng = rng
        self.mode = mode or ("M0" if not _response_probability(policy.response) else "M1")
        self.candidate_rule = candidate_rule
        self.worker_count = worker_count
        self.response_queue_capacity = response_queue_capacity
        self.response_queue_policy = response_queue_policy
        self.enable_worker_stats = enable_worker_stats
        self.enable_response_histograms = enable_response_histograms
        self.workers: list[int | None] = [None] * worker_count
        self.queue: deque[int] = deque()
        self.threads: dict[int, ResponseTask] = {}
        self.counters: Counter[str] = Counter()
        self.response_aggregates: Counter[tuple[str, str, str, str, str]] = Counter()
        self.active_attempts = [[0] * SLOT_COUNT for _ in self.targets]
        self.completed_chain_histogram: Counter[int] = Counter()
        self.completed_lifetime_histogram: Counter[int] = Counter()
        self._fair_ring: list[tuple[int, int]] = []
        self._fair_index = 0
        self._next_task = 1
        self.tick = 0
        self.background_sweep = 0

    def run(
        self,
        background_sweeps: int,
        progress: Callable[[int, int, int, int], None] | None = None,
        *,
        progress_check_every: int = 1_024,
        tick_callback: Callable[[int], None] | None = None,
    ) -> None:
        total_ticks = background_sweeps * SLOT_COUNT * len(self.targets)
        while self.tick < total_ticks:
            self.step_tick()
            if tick_callback is not None:
                tick_callback(self.tick)
            if progress is not None and self.tick % progress_check_every == 0:
                progress(
                    self.tick,
                    total_ticks,
                    self.counters["responses_created_total"],
                    len(self.queue),
                )
        if progress is not None:
            progress(
                self.tick,
                total_ticks,
                self.counters["responses_created_total"],
                len(self.queue),
            )

    def step_tick(self) -> None:
        if self.mode == "M1":
            for worker_id in range(self.worker_count):
                if self.workers[worker_id] is not None:
                    self._advance_worker(worker_id)
            self._assign_waiting_tasks()
        node, slot = self._next_fair_slot()
        self._active_update(node, slot)
        if self.mode == "M1":
            self._assign_waiting_tasks()
            self._sample_queue_depth()
        self.tick += 1

    def direct_candidates(self, node: int) -> tuple[int, ...]:
        return get_current_candidate_targets(
            self.targets, self.incoming, node, rule=self.candidate_rule
        )

    def executable_candidates(
        self,
        node: int,
        slot: int,
        *,
        additionally_excluded: int | None = None,
    ) -> tuple[int, ...]:
        """Filter the relation pool for one slot without forcing its old target out."""

        occupied_elsewhere = {
            target for index, target in enumerate(self.targets[node]) if index != slot
        }
        if additionally_excluded is not None:
            occupied_elsewhere.add(additionally_excluded)
        return tuple(
            target
            for target in self.direct_candidates(node)
            if target not in occupied_elsewhere
        )

    def candidate_graph(self) -> list[tuple[int, ...]]:
        if self.mode == "M0":
            return [tuple() for _ in self.targets]
        return [self.direct_candidates(node) for node in range(len(self.targets))]

    def snapshot(self) -> RawNetwork:
        return RawNetwork(tuple(tuple(row) for row in self.targets))  # type: ignore[arg-type]

    def dynamics_state(self) -> tuple[Any, ...]:
        tasks = tuple(
            (
                task_id,
                task.start_tick,
                task.current_node,
                task.incoming_source,
                task.incoming_slot,
                task.return_slots,
                task.response_kind,
                task.chain_length,
                task.hop_count,
            )
            for task_id, task in sorted(self.threads.items())
        )
        return (
            M2_BASELINE_MODEL_VERSION,
            self.candidate_rule,
            tuple(tuple(row) for row in self.targets),
            tuple(self.workers),
            tuple(self.queue),
            tasks,
            self._next_task,
            tuple(self._fair_ring),
            self._fair_index,
            self.background_sweep,
            self.tick,
            self.rng.getstate(),
        )

    def summary(self) -> dict[str, Any]:
        consistent = [len(self._consistent_pairs(slot)) for slot in range(SLOT_COUNT)]
        topology = layered_topology_stats(self.snapshot(), self.candidate_graph())
        completed = self.counters["responses_completed_total"]
        result: dict[str, Any] = dict(self.counters)
        result.update(
            tick=self.tick,
            active_attempts=self.tick,
            **topology,
            consistent_slot_0=consistent[0],
            consistent_slot_1=consistent[1],
            consistent_slot_2=consistent[2],
            active_worker_count=sum(task is not None for task in self.workers),
            worker_count=self.worker_count,
            pending_response_count=len(self.queue),
            max_concurrent_workers=self.counters["max_concurrent_workers"],
            max_queue_depth=self.counters["max_queue_depth"],
            mean_queue_depth=self.counters["queue_depth_sum"]
            / max(1, self.counters["queue_depth_samples"]),
            mean_completed_chain_length=self.counters["completed_chain_length_sum"]
            / max(1, completed),
            mean_completed_lifetime_ticks=self.counters["completed_lifetime_ticks_sum"]
            / max(1, completed),
            p50_lifetime_ticks=self._histogram_quantile(self.completed_lifetime_histogram, 0.5),
            p90_lifetime_ticks=self._histogram_quantile(self.completed_lifetime_histogram, 0.9),
            p99_lifetime_ticks=self._histogram_quantile(self.completed_lifetime_histogram, 0.99),
        )
        return result

    def response_histogram_rows(self) -> list[dict[str, Any]]:
        rows = []
        groups = {key[:4] for key in self.response_aggregates}
        for termination, kind, context, chain_bin in sorted(groups):
            prefix = (termination, kind, context, chain_bin)
            for lifetime_bin in sorted(
                key[4] for key in self.response_aggregates if key[:4] == prefix
            ):
                rows.append(
                    {
                        "termination_state": termination,
                        "response_kind": kind,
                        "start_tentacle_context": context,
                        "chain_length_bin": chain_bin,
                        "lifetime_tick_bin": lifetime_bin,
                        "count": self.response_aggregates[prefix + (lifetime_bin,)],
                    }
                )
        return rows

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

    def _active_update(self, node: int, slot: int) -> None:
        self.active_attempts[node][slot] += 1
        if self.mode == "M0":
            target = self._m0_target(node, slot)
        else:
            target = self._choice(self.executable_candidates(node, slot))
        if target is None:
            self.counters["active_no_candidate"] += 1
            return
        old = self.targets[node][slot]
        self._retarget(node, slot, target)
        self.counters["active_updates"] += 1
        if self.mode == "M1":
            self._create_response(node, slot, old, target)

    def _m0_target(self, node: int, slot: int) -> int | None:
        excluded = set(self.targets[node]) | {node}
        same = self.incoming[slot][node] - excluded
        other = set().union(*(self.incoming[index][node] for index in range(SLOT_COUNT)))
        other.difference_update(excluded | same)
        if self.rng.random() < self.policy.selection.p_incoming and (same or other):
            prefer_same = self.rng.random() < self.policy.selection.p_same_slot_given_incoming
            return self._choice((same or other) if prefer_same else (other or same))
        return self._global_choice(excluded)

    def _create_response(self, source: int, slot: int, old: int, target: int) -> None:
        return_slots = tuple(
            index for index, value in enumerate(self.targets[target]) if value == source
        )
        kind = self._return_class(slot, return_slots)
        probability = self._response_probability(kind)
        if self.rng.random() >= probability:
            return
        task = ResponseTask(
            task_id=self._next_task,
            start_tick=self.tick,
            current_node=target,
            incoming_source=source,
            incoming_slot=slot,
            return_slots=return_slots,
            response_kind=kind,
            source_candidate_size=len(self.direct_candidates(source)),
            target_candidate_size=len(self.direct_candidates(target)),
            source_has_mutual=any(source in self.targets[value] for value in self.targets[source]),
            target_points_back=bool(return_slots),
            target_same_meaning=slot in return_slots,
        )
        self._next_task += 1
        self.counters["responses_created_total"] += 1
        self._submit_task(task)

    def _submit_task(self, task: ResponseTask) -> None:
        self.threads[task.task_id] = task
        for worker_id, current in enumerate(self.workers):
            if current is None:
                self.workers[worker_id] = task.task_id
                active = sum(item is not None for item in self.workers)
                self.counters["max_concurrent_workers"] = max(
                    self.counters["max_concurrent_workers"], active
                )
                return
        if len(self.queue) < self.response_queue_capacity:
            self.queue.append(task.task_id)
            self.counters["max_queue_depth"] = max(
                self.counters["max_queue_depth"], len(self.queue)
            )
            return
        if self.response_queue_policy == "drop_oldest" and self.queue:
            dropped = self.queue.popleft()
            self._finish_task(dropped, "queue_rejected", self.tick)
            self.queue.append(task.task_id)
            return
        self._finish_task(task.task_id, "queue_rejected", self.tick)

    def _assign_waiting_tasks(self) -> None:
        for worker_id, current in enumerate(self.workers):
            if current is None and self.queue:
                self.workers[worker_id] = self.queue.popleft()
        active = sum(task is not None for task in self.workers)
        self.counters["max_concurrent_workers"] = max(
            self.counters["max_concurrent_workers"], active
        )

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
        candidates = self.executable_candidates(
            task.current_node,
            slot,
            additionally_excluded=task.incoming_source,
        )
        target = self._choice(candidates)
        if target is None:
            self._finish_worker(worker_id, "absorbed")
            return
        source = task.current_node
        self._retarget(source, slot, target)
        task.hop_count += 1
        task.chain_length += 1
        return_slots = tuple(
            index for index, value in enumerate(self.targets[target]) if value == source
        )
        kind = self._return_class(slot, return_slots)
        if task.hop_count >= self.policy.response.max_chain_depth:
            self._finish_worker(worker_id, "max_hops")
            return
        if not self.policy.response.allow_chain or self.rng.random() >= self._response_probability(kind):
            self._finish_worker(worker_id, "completed")
            return
        task.current_node = target
        task.incoming_source = source
        task.incoming_slot = slot
        task.return_slots = return_slots
        task.response_kind = kind

    def _finish_worker(self, worker_id: int, termination: str) -> None:
        task_id = self.workers[worker_id]
        self.workers[worker_id] = None
        if task_id is not None:
            self._finish_task(task_id, termination, self.tick)

    def _finish_task(self, task_id: int, termination: str, completion_tick: int) -> None:
        task = self.threads.pop(task_id, None)
        if task is None:
            return
        lifetime = completion_tick - task.start_tick
        if termination == "queue_rejected":
            self.counters["responses_rejected_total"] += 1
        else:
            self.counters["responses_completed_total"] += 1
        self.counters[f"termination_{termination}"] += 1
        self.counters["completed_chain_length_sum"] += task.chain_length
        self.counters["completed_lifetime_ticks_sum"] += lifetime
        self.completed_chain_histogram[task.chain_length] += 1
        self.completed_lifetime_histogram[lifetime] += 1
        if self.enable_response_histograms:
            context = (
                f"source_slot={task.incoming_slot};source_mutual={int(task.source_has_mutual)};"
                f"target_back={int(task.target_points_back)};"
                f"target_same={int(task.target_same_meaning)};"
                f"source_candidates={task.source_candidate_size};"
                f"target_candidates={task.target_candidate_size}"
            )
            self.response_aggregates[
                (
                    termination,
                    task.response_kind,
                    context,
                    histogram_bucket(task.chain_length),
                    histogram_bucket(lifetime),
                )
            ] += 1

    def _sample_queue_depth(self) -> None:
        self.counters["queue_depth_sum"] += len(self.queue)
        self.counters["queue_depth_samples"] += 1

    def _response_slot(self, task: ResponseTask) -> int | None:
        mode = self._slot_mode_for(task.response_kind)
        if mode == "none":
            return None
        if mode == "same_as_incoming":
            return task.incoming_slot
        if mode == "returning_slot":
            return self._choice(task.return_slots)
        if mode == "third_slot":
            return self._choice(
                tuple(set(range(SLOT_COUNT)) - {task.incoming_slot} - set(task.return_slots))
            )
        if mode == "random_slot":
            return self.rng.randrange(SLOT_COUNT)
        if mode == "weighted_slot":
            raise NotImplementedError("weighted response-slot mode is reserved")
        raise ValueError(f"unknown response slot mode: {mode}")

    def _response_probability(self, kind: str) -> float:
        policy = self.policy.response
        return {
            "NO_RETURN": policy.p_no_return,
            "SAME_RETURN": policy.p_same_return,
            "OTHER_RETURN": policy.p_other_return,
            "MULTI_RETURN": policy.p_multi_return,
        }[kind]

    def _slot_mode_for(self, kind: str) -> str:
        policy = self.policy.response
        return {
            "NO_RETURN": policy.slot_no_return,
            "SAME_RETURN": policy.slot_same_return,
            "OTHER_RETURN": policy.slot_other_return,
            "MULTI_RETURN": policy.slot_multi_return,
        }[kind]

    def _return_class(self, slot: int, returns: tuple[int, ...]) -> str:
        if len(returns) > 1:
            return "MULTI_RETURN"
        if not returns:
            return "NO_RETURN"
        return "SAME_RETURN" if returns[0] == slot else "OTHER_RETURN"

    def _retarget(self, node: int, slot: int, target: int) -> None:
        if target == node:
            raise ValueError("raw objects cannot point to themselves")
        if any(
            index != slot and existing == target
            for index, existing in enumerate(self.targets[node])
        ):
            raise ValueError("a raw object cannot target one object from multiple slots")
        old = self.targets[node][slot]
        self.incoming[slot][old].remove(node)
        self.incoming[slot][target].add(node)
        self.targets[node][slot] = target

    def _consistent_pairs(self, slot: int) -> set[tuple[int, int]]:
        return {
            (node, target)
            for node, target in enumerate(row[slot] for row in self.targets)
            if node < target and self.targets[target][slot] == node
        }

    def is_consistent(self, a: int, b: int, slot: int) -> bool:
        return self.targets[a][slot] == b and self.targets[b][slot] == a

    def _choice(self, values: Any) -> int | None:
        values = tuple(values)
        return self.rng.choice(values) if values else None

    def _global_choice(self, blocked: set[int]) -> int | None:
        if len(blocked) >= len(self.targets):
            return None
        while True:
            candidate = self.rng.randrange(len(self.targets))
            if candidate not in blocked:
                return candidate

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


def _response_probability(policy: ResponsePolicy) -> float:
    return sum(
        (
            policy.p_no_return,
            policy.p_same_return,
            policy.p_other_return,
            policy.p_multi_return,
        )
    )


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
        "M2": dict(p_same_return=1, slot_same_return="same_as_incoming", target_mode="escape_to_nonincoming"),
        "M3": dict(p_other_return=1, slot_other_return="returning_slot"),
        "M4": dict(p_other_return=1, slot_other_return="third_slot"),
        "M5": dict(p_no_return=0.1, p_same_return=0.1, p_other_return=0.1, slot_no_return="same_as_incoming"),
        "M6": {},
        "LEGACY": dict(allow_chain=False),
    }
    if name not in profiles:
        raise ValueError(f"unknown policy profile: {name}")
    base.update(profiles[name])
    base.update(overrides.get("response", {}))
    return ExperimentPolicy(
        selection,
        ResponsePolicy(**base),
        "tick",
        overrides.get("p_process_response", 1.0),
    )
