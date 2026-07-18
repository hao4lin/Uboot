"""Trace fixed-point external slots and object relations over one short interval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from random import Random
from typing import Any

from uboot.checkpoint import dynamics_state_hash, load_checkpoint
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.exposure_output import write_exposure_trace
from uboot.exposure_trace import run_exposure_trace
from uboot.joint_continuation import SnapshotSeries
from uboot.kernel import SLOT_COUNT
from uboot.microtrace import compare_replays


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--root-checkpoint", type=Path)
    source.add_argument("--N", type=int)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--warmup-sweeps", type=int, default=0)
    parser.add_argument("--root-snapshot-id", type=int, default=0)
    parser.add_argument("--snapshot-interval-sweeps", type=int, default=1)
    parser.add_argument("--start-snapshot-id", type=int, required=True)
    parser.add_argument("--end-snapshot-id", type=int, required=True)
    parser.add_argument("--start-step", type=int)
    parser.add_argument("--end-step", type=int)
    parser.add_argument("--root-pair-ids", type=int, nargs=2, required=True)
    parser.add_argument("--root-triangle-ids", type=int, nargs=3, required=True)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--object-radius", type=int, default=2)
    parser.add_argument("--full-object-index", action="store_true", default=True)
    parser.add_argument("--verification-full-recompute", action="store_true")
    parser.add_argument(
        "--event-detail-level",
        choices=("changed-only", "all"),
        default="changed-only",
    )
    parser.add_argument("--write-all-exposure-touches", action="store_true")
    parser.add_argument("--max-event-rows", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.end_snapshot_id <= args.start_snapshot_id:
        parser.error("--end-snapshot-id must exceed --start-snapshot-id")
    if args.snapshot_interval_sweeps < 1:
        parser.error("--snapshot-interval-sweeps must be positive")
    if args.object_radius != 2:
        parser.error("the first exposure experiment requires --object-radius 2")
    if args.root_checkpoint and args.warmup_sweeps:
        parser.error("--warmup-sweeps is only valid with --N")

    direct, direct_source = _prepare_engine(args)
    traced, traced_source = _prepare_engine(args)
    start_step = traced.tick
    if args.start_step is not None and args.start_step != start_step:
        parser.error(
            f"configured start step {args.start_step} != replayed step {start_step}"
        )
    ticks_per_sweep = SLOT_COUNT * len(traced.targets)
    default_end_step = start_step + (
        (args.end_snapshot_id - args.start_snapshot_id)
        * args.snapshot_interval_sweeps
        * ticks_per_sweep
    )
    end_step = args.end_step if args.end_step is not None else default_end_step
    if end_step <= start_step:
        parser.error("end step must exceed the replayed start step")
    start_hash_direct = dynamics_state_hash(direct)
    start_hash_traced = dynamics_state_hash(traced)
    _run_to_step(direct, end_step)
    result = run_exposure_trace(
        traced,
        root_pair=args.root_pair_ids,
        root_triangle=args.root_triangle_ids,
        start_snapshot_id=args.start_snapshot_id,
        end_snapshot_id=args.end_snapshot_id,
        end_step=end_step,
        object_radius=args.object_radius,
        verification_full_recompute=args.verification_full_recompute,
    )
    verification = compare_replays(
        direct,
        traced,
        start_hash_direct=start_hash_direct,
        start_hash_traced=start_hash_traced,
    )
    snapshot_evidence = _snapshot_evidence(
        args.snapshot_dir,
        args.start_snapshot_id,
        args.end_snapshot_id,
        start_step,
        end_step,
        result,
    )
    exposure_checks = {
        "internal_fixed_point_invariant_preserved": result.summary[
            "level0_internal_change_count"
        ]
        == 0,
        "incremental_full_recompute_match": (
            result.summary["full_recompute_verification_count"]
            == result.summary["atomic_update_count"]
            if args.verification_full_recompute
            else "not_requested"
        ),
        "full_object_index": args.full_object_index,
    }
    verification.update(
        snapshot_evidence=snapshot_evidence,
        snapshot_evidence_match=snapshot_evidence["all_checks_pass"],
        exposure_checks=exposure_checks,
    )
    verification["all_checks_pass"] = verification["all_checks_pass"] and all(
        value is True or value == "not_requested"
        for value in exposure_checks.values()
    ) and snapshot_evidence["all_checks_pass"]
    if not verification["all_checks_pass"]:
        raise RuntimeError("direct and exposure-traced replay verification diverged")
    config: dict[str, Any] = {
        "source": direct_source,
        "traced_source": traced_source,
        "start_snapshot_id": args.start_snapshot_id,
        "end_snapshot_id": args.end_snapshot_id,
        "start_step": start_step,
        "end_step": end_step,
        "snapshot_interval_sweeps": args.snapshot_interval_sweeps,
        "root_pair_ids": sorted(args.root_pair_ids),
        "root_triangle_ids": sorted(args.root_triangle_ids),
        "root_object_ids": result.root_object_ids,
        "snapshot_dir": str(args.snapshot_dir.resolve())
        if args.snapshot_dir
        else None,
        "object_radius": args.object_radius,
        "full_object_index": args.full_object_index,
        "verification_full_recompute": args.verification_full_recompute,
        "event_detail_level": args.event_detail_level,
        "write_all_exposure_touches": args.write_all_exposure_touches,
        "max_event_rows": args.max_event_rows,
    }
    summary = write_exposure_trace(
        result,
        args.output_dir,
        config=config,
        verification=verification,
        write_all_exposure_touches=(
            args.write_all_exposure_touches or args.event_detail_level == "all"
        ),
        max_event_rows=args.max_event_rows,
    )
    print(
        json.dumps(
            {"summary": summary, "verification": verification},
            indent=2,
            sort_keys=True,
        )
    )


def _prepare_engine(args: argparse.Namespace) -> tuple[EdgeResponseEngine, dict[str, Any]]:
    if args.root_checkpoint:
        engine, metadata = load_checkpoint(args.root_checkpoint)
        root_sweep = engine.tick // (SLOT_COUNT * len(engine.targets))
        offset = args.start_snapshot_id - args.root_snapshot_id
        if offset < 0:
            raise ValueError("start snapshot precedes the root checkpoint")
        start_sweep = root_sweep + offset * args.snapshot_interval_sweeps
        engine.run(start_sweep)
        return engine, {
            "kind": "root_checkpoint",
            "path": str(args.root_checkpoint.resolve()),
            "root_dynamics_hash": metadata["dynamics_state_hash"],
            "root_sweep": root_sweep,
            "replayed_start_sweep": start_sweep,
        }
    rng = Random(args.seed)
    engine = EdgeResponseEngine(
        distinct_random_network(args.N, rng),
        policy_profile("M2"),
        rng,
        worker_count=1,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )
    engine.run(args.warmup_sweeps)
    return engine, {
        "kind": "seeded_initialization",
        "N": args.N,
        "seed": args.seed,
        "warmup_sweeps": args.warmup_sweeps,
    }


def _run_to_step(engine: EdgeResponseEngine, end_step: int) -> None:
    ticks_per_sweep = SLOT_COUNT * len(engine.targets)
    if end_step % ticks_per_sweep == 0:
        engine.run(end_step // ticks_per_sweep)
        return
    while engine.tick < end_step:
        engine.step_tick()


def _snapshot_evidence(
    snapshot_dir: Path | None,
    start_id: int,
    end_id: int,
    start_step: int,
    end_step: int,
    result,
) -> dict[str, Any]:
    if snapshot_dir is None:
        return {"available": False, "all_checks_pass": True}
    system_path, object_path = _resolve_snapshot_files(snapshot_dir)
    series = SnapshotSeries.from_csv(system_path, object_path)
    if start_id not in series.by_id or end_id not in series.by_id:
        raise ValueError("snapshot evidence does not contain both requested IDs")
    start, end = series.by_id[start_id], series.by_id[end_id]
    indexed_pairs = {
        item.members for item in result.index.objects if item.object_type == "pair"
    }
    indexed_triangles = {
        item.members
        for item in result.index.objects
        if item.object_type == "triangle"
    }
    checks = {
        "start_step_match": start.tick == start_step,
        "end_step_match": end.tick == end_step,
        "start_full_object_index_match": (
            set(start.pairs) == indexed_pairs
            and set(start.triples) == indexed_triangles
        ),
        "end_full_object_index_match": (
            set(end.pairs) == indexed_pairs
            and set(end.triples) == indexed_triangles
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"snapshot evidence mismatch: {checks}")
    return {
        "available": True,
        "system_file": str(system_path.resolve()),
        "object_file": str(object_path.resolve()),
        **checks,
        "all_checks_pass": True,
    }


def _resolve_snapshot_files(snapshot_dir: Path) -> tuple[Path, Path]:
    for system_name, object_name in (
        ("system_snapshots.csv", "closed_object_snapshots.csv"),
        ("phase2_system_snapshots.csv", "phase2_object_snapshots.csv"),
    ):
        system, objects = snapshot_dir / system_name, snapshot_dir / object_name
        if system.is_file() and objects.is_file():
            return system, objects
    raise FileNotFoundError("snapshot directory has no supported system/object CSV pair")


if __name__ == "__main__":
    main()
