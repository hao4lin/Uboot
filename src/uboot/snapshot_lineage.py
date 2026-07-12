"""Snapshot-only closed-object extraction and offline lineage matching."""

from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path
from statistics import fmean
from typing import Any

from uboot.kernel import RawNetwork
from uboot.observables import mutual_pairs
from uboot.reporting import _candidate_graph, _strongly_connected_components


def histogram_bucket(value: int) -> str:
    if value < 0:
        raise ValueError("histogram values cannot be negative")
    if value <= 5:
        return str(value)
    for lower, upper in ((6, 7), (8, 15), (16, 31), (32, 63), (64, 127), (128, 255)):
        if value <= upper:
            return f"{lower}-{upper}"
    return "256+"


def continuity_metrics(source: frozenset[int], target: frozenset[int]) -> dict[str, float | int]:
    shared = source & target
    union = source | target
    return {
        "shared_member_count": len(shared),
        "member_jaccard": len(shared) / len(union),
        "source_member_retention": len(shared) / len(source),
        "target_member_inheritance": len(shared) / len(target),
    }


def tentacle_counts(network: RawNetwork, members: frozenset[int]) -> tuple[int, int]:
    outgoing = sum(
        target not in members
        for node in members
        for target in network.targets[node]
    )
    incoming = sum(
        target in members and source not in members
        for source, targets in enumerate(network.targets)
        for target in targets
    )
    return incoming, outgoing


def closed_object_rows(
    network: RawNetwork, snapshot_id: int, sweep: float
) -> list[dict[str, Any]]:
    candidates = _candidate_graph(network)
    components = [
        component
        for component in _strongly_connected_components(candidates)
        if all(candidates[node] <= component for node in component)
    ]
    components.sort(key=lambda members: tuple(sorted(members)))
    pairs = mutual_pairs(network)
    rows = []
    for index, members in enumerate(components):
        ordered = tuple(sorted(members))
        incoming, outgoing = tentacle_counts(network, frozenset(members))
        internal = sum(left in members and right in members for left, right in pairs)
        rows.append(
            {
                "snapshot_id": snapshot_id,
                "sweep": sweep,
                "local_object_index": index,
                "object_type": _object_type(len(members)),
                "member_count": len(members),
                "member_ids": "|".join(map(str, ordered)),
                "internal_mutual_edge_count": internal,
                "incoming_tentacle_count": incoming,
                "outgoing_tentacle_count": outgoing,
                "total_external_tentacle_count": incoming + outgoing,
            }
        )
    return rows


def system_snapshot(
    network: RawNetwork,
    snapshot_id: int,
    sweep: float,
    active_slots: int,
    engine_summary: dict[str, Any],
    objects: list[dict[str, Any]],
    worker_count: int,
) -> dict[str, Any]:
    candidates = _candidate_graph(network)
    sizes = [len(group) for group in candidates]
    pair_count = engine_summary["mutual_connection_count"]
    types = Counter(row["object_type"] for row in objects)
    return {
        "snapshot_id": snapshot_id,
        "sweep": sweep,
        "active_slot_count": active_slots,
        "mutual_edge_count": pair_count,
        "mutual_density": 2 * pair_count / (3 * network.size),
        "same_slot_mutual_count_1": engine_summary["consistent_slot_0"],
        "same_slot_mutual_count_2": engine_summary["consistent_slot_1"],
        "same_slot_mutual_count_3": engine_summary["consistent_slot_2"],
        "candidate_size_0_count": sum(size == 0 for size in sizes),
        "candidate_size_1_count": sum(size == 1 for size in sizes),
        "candidate_size_2_count": sum(size == 2 for size in sizes),
        "candidate_size_3plus_count": sum(size >= 3 for size in sizes),
        "closed_pair_count": types["pair"],
        "closed_triple_count": types["triple"],
        "closed_other_count": types["other"],
        "active_worker_count": engine_summary["active_worker_count"],
        "idle_worker_count": worker_count - engine_summary["active_worker_count"],
        "responses_started_total": engine_summary["thread_count"],
        "responses_completed_total": engine_summary.get("terminated_complete", 0),
        "responses_aborted_total": engine_summary.get("terminated_obsolete", 0)
        + engine_summary.get("terminated_no_slot", 0),
        "responses_timed_out_total": engine_summary.get("terminated_timeout", 0),
    }


def analyze_lineages(input_path: Path, output_dir: Path) -> dict[str, float | int]:
    rows = list(csv.DictReader(input_path.open(encoding="utf-8")))
    by_snapshot: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        row["snapshot_id"] = int(row["snapshot_id"])
        row["local_object_index"] = int(row["local_object_index"])
        row["members"] = frozenset(map(int, row["member_ids"].split("|")))
        by_snapshot.setdefault(row["snapshot_id"], []).append(row)
    lineage: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    transitions: Counter[tuple[str, str]] = Counter()
    ids = sorted(by_snapshot)
    for before_id, after_id in zip(ids, ids[1:]):
        before, after = by_snapshot[before_id], by_snapshot[after_id]
        candidates = []
        source_counts: Counter[int] = Counter()
        target_counts: Counter[int] = Counter()
        for source in before:
            for target in after:
                shared = source["members"] & target["members"]
                if not shared:
                    continue
                source_counts[source["local_object_index"]] += 1
                target_counts[target["local_object_index"]] += 1
                metrics = continuity_metrics(source["members"], target["members"])
                candidates.append((source, target, metrics["shared_member_count"],
                    metrics["member_jaccard"], metrics["source_member_retention"],
                    metrics["target_member_inheritance"]))
        primary_sources: set[int] = set()
        primary_targets: set[int] = set()
        for source in before:
            options = [item for item in candidates if item[0] is source]
            options.sort(key=lambda item: (-item[4], -item[3], item[0]["object_type"] != item[1]["object_type"], item[1]["local_object_index"]))
            if options:
                primary_sources.add(source["local_object_index"])
                primary_targets.add(options[0][1]["local_object_index"])
            for position, item in enumerate(options):
                src, dst, shared, jaccard, retention, inheritance = item
                relation = "primary_match" if position == 0 else "secondary_candidate"
                if source_counts[src["local_object_index"]] > 1:
                    relation = "split_candidate" if position else relation
                if target_counts[dst["local_object_index"]] > 1 and position:
                    relation = "merge_candidate"
                lineage.append({"from_snapshot_id": before_id, "to_snapshot_id": after_id,
                    "from_object_index": src["local_object_index"], "to_object_index": dst["local_object_index"],
                    "from_type": src["object_type"], "to_type": dst["object_type"],
                    "shared_member_count": shared, "member_jaccard": jaccard,
                    "source_member_retention": retention, "target_member_inheritance": inheritance,
                    "same_type": src["object_type"] == dst["object_type"], "match_relation": relation})
                if position == 0:
                    transitions[(src["object_type"], dst["object_type"])] += 1
        unmatched_sources = [
            row for row in before if row["local_object_index"] not in primary_sources
        ]
        unmatched_targets = [
            row for row in after if row["local_object_index"] not in primary_targets
        ]
        deaths = len(unmatched_sources)
        births = len(unmatched_targets)
        for row in unmatched_targets:
            transitions[("none", row["object_type"])] += 1
        for row in unmatched_sources:
            transitions[(row["object_type"], "none")] += 1
        summaries.append({"from_snapshot_id": before_id, "to_snapshot_id": after_id,
            "from_object_count": len(before), "to_object_count": len(after),
            "primary_matches": len(primary_sources), "unmatched_births": births,
            "unmatched_deaths": deaths, "ambiguous_source_count": sum(v > 1 for v in source_counts.values()),
            "ambiguous_target_count": sum(v > 1 for v in target_counts.values()),
            "split_candidate_count": sum(v > 1 for v in source_counts.values()),
            "merge_candidate_count": sum(v > 1 for v in target_counts.values())})
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "object_lineages.csv", lineage)
    _write_csv(output_dir / "snapshot_matching_summary.csv", summaries)
    transition_rows = [{"from_type": key[0], "to_type": key[1], "count": count} for key, count in sorted(transitions.items())]
    _write_csv(output_dir / "object_transition_matrix.csv", transition_rows)
    return {"snapshot_count": len(ids), "mean_objects_per_snapshot": fmean(map(len, by_snapshot.values())) if by_snapshot else 0,
        "primary_matches": sum(row["primary_matches"] for row in summaries),
        "unmatched_births": sum(row["unmatched_births"] for row in summaries),
        "unmatched_deaths": sum(row["unmatched_deaths"] for row in summaries),
        "split_candidates": sum(row["split_candidate_count"] for row in summaries),
        "merge_candidates": sum(row["merge_candidate_count"] for row in summaries)}


def _object_type(size: int) -> str:
    return "pair" if size == 2 else "triple" if size == 3 else "other"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
