"""Crash-contained incremental M2 history generation from frozen checkpoints."""

from __future__ import annotations

from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from uboot.checkpoint import dynamics_state_hash, save_checkpoint
from uboot.edge_response import EdgeResponseEngine
from uboot.kernel import SLOT_COUNT
from uboot.phase2 import snapshot_state


ProgressCallback = Callable[[int, int], None]


def run_incremental_chunk(
    engine: EdgeResponseEngine,
    source_metadata: dict[str, Any],
    *,
    chunk_sweeps: int,
    snapshot_interval_sweeps: int,
    chunk_dir: Path,
    checkpoint_dir: Path,
    seed: int,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if chunk_sweeps < 1:
        raise ValueError("chunk_sweeps must be positive")
    if snapshot_interval_sweeps < 1:
        raise ValueError("snapshot_interval_sweeps must be positive")
    if chunk_dir.exists() or checkpoint_dir.exists():
        raise FileExistsError("chunk and checkpoint outputs must not already exist")
    start_tick = engine.tick
    ticks_per_sweep = SLOT_COUNT * len(engine.targets)
    if start_tick % ticks_per_sweep:
        raise ValueError("incremental chunks must start at a complete sweep boundary")
    start_sweep = start_tick // ticks_per_sweep
    end_sweep = start_sweep + chunk_sweeps
    start_hash = dynamics_state_hash(engine)
    started = monotonic()
    snapshots = [snapshot_state(engine, engine.tick)]
    offsets = list(range(snapshot_interval_sweeps, chunk_sweeps + 1, snapshot_interval_sweeps))
    if not offsets or offsets[-1] != chunk_sweeps:
        offsets.append(chunk_sweeps)
    for offset in offsets:
        target_sweep = start_sweep + offset
        engine.run(target_sweep)
        snapshots.append(snapshot_state(engine, engine.tick))
        if progress is not None:
            progress(offset, chunk_sweeps)
    final_hash = dynamics_state_hash(engine)
    common = {
        "baseline_tag": source_metadata["baseline_git_tag"],
        "baseline_commit": source_metadata["baseline_git_commit"],
        "source_checkpoint_hash": source_metadata["full_checkpoint_hash"],
        "start_sweep": start_sweep,
        "end_sweep": end_sweep,
        "snapshot_interval_sweeps": snapshot_interval_sweeps,
        "start_dynamics_state_hash": start_hash,
        "final_dynamics_state_hash": final_hash,
    }
    object_rows: list[dict[str, Any]] = []
    system_rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        for row in snapshot["objects"]:
            object_rows.append({**common, **row})
        classes = Counter(row["object_class"] for row in snapshot["objects"])
        coverage = sum(int(row["size"]) for row in snapshot["objects"]) / len(
            engine.targets
        )
        system_rows.append(
            {
                **common,
                "snapshot_id": snapshot["snapshot_id"],
                "tick": snapshot["tick"],
                "sweep": snapshot["tick"] / ticks_per_sweep,
                "closed_pair_count": classes["pair"],
                "closed_triple_count": classes["triple"],
                "closed_larger_count": classes["larger"],
                "candidate_isolated_count": sum(not item for item in snapshot["graph"]),
                "object_coverage": coverage,
            }
        )
    chunk_dir.mkdir(parents=True)
    _write_csv(chunk_dir / "system_snapshots.csv", system_rows)
    _write_csv(chunk_dir / "closed_object_snapshots.csv", object_rows)
    checkpoint_metadata = save_checkpoint(
        engine,
        checkpoint_dir,
        seed=seed,
        baseline_commit=source_metadata["baseline_git_commit"],
        extra_metadata={
            "parent_checkpoint_hash": source_metadata["full_checkpoint_hash"],
            "history_chunk_start_sweep": start_sweep,
            "history_chunk_end_sweep": end_sweep,
        },
    )
    manifest = {
        **common,
        "snapshot_count": len(snapshots),
        "object_row_count": len(object_rows),
        "runtime_seconds": monotonic() - started,
        "output_checkpoint": str(checkpoint_dir.resolve()),
        "output_checkpoint_hash": checkpoint_metadata["full_checkpoint_hash"],
        "history_format_version": "1.0.0",
    }
    (chunk_dir / "incremental_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def choose_root_checkpoint(run_dir: Path, source_checkpoint: Path | None) -> Path:
    """Fix one replay root; later chunks never resume hidden Python set layout."""
    root = run_dir / "root_checkpoint.txt"
    if root.exists():
        path = Path(root.read_text(encoding="utf-8").strip())
        if not path.is_dir():
            raise FileNotFoundError(f"root checkpoint is missing: {path}")
        return path
    if source_checkpoint is None:
        raise ValueError("--source-checkpoint is required for the first chunk")
    if not source_checkpoint.is_dir():
        raise FileNotFoundError(source_checkpoint)
    resolved = source_checkpoint.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    temporary = run_dir / "root_checkpoint.txt.tmp"
    temporary.write_text(str(resolved) + "\n", encoding="utf-8")
    temporary.replace(root)
    return resolved


def history_tail(run_dir: Path) -> dict[str, str] | None:
    index = run_dir / "history_index.csv"
    if not index.exists():
        return None
    with index.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return rows[-1] if rows else None


def reserve_chunk_paths(
    run_dir: Path, start_sweep: int, end_sweep: int
) -> tuple[Path, Path]:
    label = f"s{start_sweep:09d}_to_s{end_sweep:09d}"
    return run_dir / "chunks" / label, run_dir / "checkpoints" / f"s{end_sweep:09d}"


def finalize_run_index(run_dir: Path, manifest: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    index = run_dir / "history_index.csv"
    exists = index.exists()
    fields = [
        "start_sweep",
        "end_sweep",
        "snapshot_interval_sweeps",
        "snapshot_count",
        "object_row_count",
        "runtime_seconds",
        "replay_sweeps",
        "replay_runtime_seconds",
        "source_checkpoint_hash",
        "output_checkpoint_hash",
        "start_dynamics_state_hash",
        "final_dynamics_state_hash",
        "output_checkpoint",
    ]
    with index.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: manifest[field] for field in fields})
    pointer = run_dir / "latest_checkpoint.txt"
    temporary = run_dir / "latest_checkpoint.txt.tmp"
    temporary.write_text(manifest["output_checkpoint"] + "\n", encoding="utf-8")
    temporary.replace(pointer)


def acquire_run_lock(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = run_dir / ".incremental.lock"
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write("one incremental process owns this run directory\n")
    except FileExistsError as error:
        raise RuntimeError(f"another incremental process may be active: {lock}") from error
    return lock


def source_series_id(metadata: dict[str, Any]) -> str:
    payload = (
        f"{metadata['baseline_git_tag']}|{metadata['full_checkpoint_hash']}|"
        f"{metadata['sweep']}"
    )
    return sha256(payload.encode()).hexdigest()[:16]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
