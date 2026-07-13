"""Stage-two local structure snapshots and conservative object transitions."""

from __future__ import annotations

from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
from typing import Any

from uboot.checkpoint import dynamics_state_hash
from uboot.edge_response import EdgeResponseEngine
from uboot.observables import mutual_pairs
from uboot.snapshot_lineage import closed_object_rows, same_meaning_mutual_pairs


def snapshot_state(engine: EdgeResponseEngine, snapshot_id: int) -> dict[str, Any]:
    network, graph = engine.snapshot(), engine.candidate_graph()
    objects = closed_object_rows(network, graph, snapshot_id, engine.tick)
    members = {
        frozenset(map(int, row["member_ids"].split("|"))): row
        for row in objects if row["member_ids"]
    }
    candidate_cycles = frozenset(
        (left, right) for left, targets in enumerate(graph) for right in targets
        if left < right and left in graph[right]
    )
    return {
        "snapshot_id": snapshot_id, "tick": engine.tick, "network": network,
        "graph": graph, "objects": objects, "members": members,
        "raw_mutual": mutual_pairs(network), "candidate_cycles": candidate_cycles,
        "same_meaning": frozenset((a, b) for a, b, _ in same_meaning_mutual_pairs(network)),
    }


def classify_transition(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous, current = before["members"], after["members"]
    matched_current: set[frozenset[int]] = set()
    for members, source in previous.items():
        if members in current:
            target = current[members]
            matched_current.add(members)
            changed = (
                source["internal_candidate_edges"] != target["internal_candidate_edges"]
                or source["internal_raw_mutual_edges"] != target["internal_raw_mutual_edges"]
            )
            relation = "internal_rewire" if changed else "persist"
            rows.append(_transition(before, after, members, members, relation))
            continue
        replacements = [
            candidate for candidate in current
            if len(candidate) == len(members)
            and len(candidate & members) == len(members) - 1
            and _support_continuity(before, after, members, candidate)
        ]
        if len(replacements) == 1:
            matched_current.add(replacements[0])
            rows.append(_transition(before, after, members, replacements[0], "reclosed_member_replacement"))
        else:
            rows.append(_transition(before, after, members, frozenset(), "death"))
    for members in current:
        if members not in matched_current and members not in previous:
            rows.append(_transition(before, after, frozenset(), members, "independent_birth"))
    return rows


def detect_open_reclose(
    snapshots: list[dict[str, Any]], *, max_gap_snapshots: int
) -> list[dict[str, Any]]:
    """Recognize exact-member closure gaps without matching adjacent birth/death."""
    seen: dict[frozenset[int], tuple[int, dict[str, Any]]] = {}
    active: set[frozenset[int]] = set()
    events = []
    for snapshot in snapshots:
        current = set(snapshot["members"])
        for members in active - current:
            seen[members] = (snapshot["snapshot_id"], snapshot)
        for members in current - active:
            if members in seen:
                opened_at, _ = seen.pop(members)
                gap = snapshot["snapshot_id"] - opened_at
                if gap <= max_gap_snapshots:
                    events.append({
                        "members": "|".join(map(str, sorted(members))),
                        "opened_snapshot_id": opened_at,
                        "reclosed_snapshot_id": snapshot["snapshot_id"],
                        "open_gap_snapshots": gap,
                        "state_change": "reclosed_same_members",
                    })
        active = current
    return events


def _support_continuity(before: dict[str, Any], after: dict[str, Any], left: frozenset[int], right: frozenset[int]) -> bool:
    shared = left & right
    support_before = {edge for edge in before["candidate_cycles"] | before["raw_mutual"] if set(edge) <= left}
    support_after = {edge for edge in after["candidate_cycles"] | after["raw_mutual"] if set(edge) <= right}
    return bool(shared) and bool(support_before & support_after)


def _transition(before: dict[str, Any], after: dict[str, Any], source: frozenset[int], target: frozenset[int], state: str) -> dict[str, Any]:
    return {
        "from_snapshot_id": before["snapshot_id"], "to_snapshot_id": after["snapshot_id"],
        "from_members": "|".join(map(str, sorted(source))),
        "to_members": "|".join(map(str, sorted(target))), "state_change": state,
        "removed_member": next(iter(source - target)) if len(source - target) == 1 else "",
        "added_member": next(iter(target - source)) if len(target - source) == 1 else "",
    }


def run_tracking(engine: EdgeResponseEngine, metadata: dict[str, Any], *, tracking_sweeps: int, interval: int, output_dir: Path) -> dict[str, Any]:
    started = monotonic()
    source_sweep = engine.background_sweep
    snapshots = [snapshot_state(engine, 0)]
    for offset in range(interval, tracking_sweeps + 1, interval):
        engine.run(source_sweep + offset)
        snapshots.append(snapshot_state(engine, len(snapshots)))
    if (snapshots[-1]["tick"] - snapshots[0]["tick"]) // (3 * len(engine.targets)) != tracking_sweeps:
        engine.run(source_sweep + tracking_sweeps)
        snapshots.append(snapshot_state(engine, len(snapshots)))
    transitions = [row for left, right in zip(snapshots, snapshots[1:]) for row in classify_transition(left, right)]
    common = {
        "baseline_tag": metadata["baseline_git_tag"], "baseline_commit": metadata["baseline_git_commit"],
        "checkpoint_hash": metadata["full_checkpoint_hash"], "source_checkpoint_sweep": metadata["sweep"],
        "tracking_interval": interval, "tracking_sweeps": tracking_sweeps,
    }
    object_rows = []
    system_rows = []
    raw_events, cycle_events = [], []
    for snap in snapshots:
        for row in snap["objects"]:
            members = row["member_ids"]
            row.update(common, track_id=sha256(members.encode()).hexdigest()[:16])
            object_rows.append(row)
        classes = Counter(row["object_class"] for row in snap["objects"])
        coverage = sum(int(row["size"]) for row in snap["objects"]) / len(engine.targets)
        system_rows.append({**common, "snapshot_id": snap["snapshot_id"], "tick": snap["tick"],
            "closed_pair_count": classes["pair"], "closed_triple_count": classes["triple"],
            "closed_larger_count": classes["larger"], "candidate_isolated_count": sum(not x for x in snap["graph"]),
            "object_coverage": coverage})
    for left, right in zip(snapshots, snapshots[1:]):
        raw_events.extend(_edge_events(left, right, "raw_mutual"))
        cycle_events.extend(_edge_events(left, right, "candidate_cycles"))
    final_hash = dynamics_state_hash(engine)
    common["final_dynamics_state_hash"] = final_hash
    output_dir.mkdir(parents=True, exist_ok=True)
    _write(output_dir / "phase2_system_snapshots.csv", system_rows, common)
    _write(output_dir / "phase2_object_snapshots.csv", object_rows, common)
    _write(output_dir / "phase2_object_tracks.csv", transitions, common)
    _write(output_dir / "phase2_object_transitions.csv", transitions, common)
    _write(output_dir / "phase2_raw_mutual_events.csv", raw_events, common)
    _write(output_dir / "phase2_candidate_two_cycle_events.csv", cycle_events, common)
    _write(output_dir / "phase2_open_reclose_events.csv", [], common)
    summary = Counter(row["state_change"] for row in transitions)
    manifest = {**common, "phase2_ready": metadata["phase2_ready"], "runtime_seconds": monotonic() - started,
        "transition_counts": dict(summary), "version_mismatch_allowed": metadata.get("version_mismatch_allowed", False)}
    (output_dir / "phase2_run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _edge_events(left: dict[str, Any], right: dict[str, Any], field: str) -> list[dict[str, Any]]:
    rows = []
    for event, edges in (("removed", left[field] - right[field]), ("added", right[field] - left[field])):
        rows.extend({"from_snapshot_id": left["snapshot_id"], "to_snapshot_id": right["snapshot_id"], "event": event, "left": a, "right": b} for a, b in sorted(edges))
    return rows


def _write(path: Path, rows: list[dict[str, Any]], common: dict[str, Any]) -> None:
    rows = [{**common, **row} for row in rows]
    if not rows:
        rows = [common]
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
