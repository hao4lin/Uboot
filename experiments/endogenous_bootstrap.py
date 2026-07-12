"""Run the relation-endogenous bootstrap experiment in single-slot steps."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path
from random import Random
from statistics import fmean, pstdev
import sys
from time import monotonic
from typing import Any

from uboot.dynamics.endogenous import EndogenousEngine
from uboot.kernel import RawNetwork, random_network
from uboot.reporting import HeavyStats, SnapshotStats, heavy_stats, snapshot_stats

ROLLING_SAMPLE_WINDOWS = (10, 100, 1_000)
PROGRESS_CHECK_EVERY = 1_024


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rng = Random(args.seed)
    initial = random_network(args.N, rng)
    engine = EndogenousEngine(initial, rng)
    started = monotonic()
    last_progress = started
    step = 0
    stopped_by = "max_steps"

    ordinary_rows: list[dict[str, Any]] = []
    density_history: list[float] = []
    heavy_rows: list[dict[str, int | float]] = []
    latest = _record_ordinary(initial, 0, ordinary_rows, density_history)
    latest_heavy = heavy_stats(initial, 0)
    heavy_rows.append(latest_heavy.as_dict())

    while step < args.max_steps:
        engine.rewrite()
        step += 1

        ordinary_due = step % args.stats_interval == 0 or step == args.max_steps
        heavy_due = step % args.heavy_interval == 0 or step == args.max_steps
        snapshot = engine.snapshot() if ordinary_due or heavy_due else None
        if ordinary_due:
            assert snapshot is not None
            latest = _record_ordinary(snapshot, step, ordinary_rows, density_history)
        if heavy_due:
            assert snapshot is not None
            latest_heavy = heavy_stats(snapshot, step)
            heavy_rows.append(latest_heavy.as_dict())

        if step % PROGRESS_CHECK_EVERY == 0 or step == args.max_steps:
            now = monotonic()
            if now - last_progress >= 60 or step == args.max_steps:
                _print_progress(step, args.max_steps, latest)
                last_progress = now
            if args.max_seconds is not None and now - started >= args.max_seconds:
                stopped_by = "max_seconds"
                break

    final_network = engine.snapshot()
    if latest.step != step:
        latest = _record_ordinary(
            final_network, step, ordinary_rows, density_history
        )
    if latest_heavy.step != step:
        latest_heavy = heavy_stats(final_network, step)
        heavy_rows.append(latest_heavy.as_dict())
    if step != args.max_steps:
        _print_progress(step, args.max_steps, latest, stopped_by=stopped_by)

    _write_csv(output_dir / "u0_endogenous_stats.csv", ordinary_rows)
    _write_csv(output_dir / "u0_endogenous_heavy.csv", heavy_rows)

    final_payload = {
        "configuration": {
            "N": args.N,
            "seed": args.seed,
            "max_steps": args.max_steps,
            "max_seconds": args.max_seconds,
            "stats_interval": args.stats_interval,
            "heavy_interval": args.heavy_interval,
        },
        "completed_steps": step,
        "stopped_by": stopped_by,
        "elapsed_seconds": monotonic() - started,
        "final_stats": asdict(latest),
        "final_heavy_stats": asdict(latest_heavy),
        "omitted_statistics": {
            "turnover": "requires per-step mutual creation/destruction bookkeeping",
            "lifetimes": "requires per-step birth/death bookkeeping",
        },
    }
    _write_json(output_dir / "u0_endogenous_final.json", final_payload)
    (output_dir / "u0_endogenous_report.md").write_text(
        _build_report(ordinary_rows, latest, latest_heavy), encoding="utf-8"
    )

    if args.snapshot_out:
        _write_json(
            Path(args.snapshot_out),
            {"step": step, "targets": final_network.targets},
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--N", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=1_000_000)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--stats-interval", type=int, default=10_000)
    parser.add_argument("--heavy-interval", type=int, default=100_000)
    parser.add_argument("--output-dir", default="artifacts/endogenous")
    parser.add_argument("--snapshot-out")
    args = parser.parse_args()
    if args.N < 2:
        parser.error("--N must be at least 2")
    if args.max_steps < 0:
        parser.error("--max-steps cannot be negative")
    if args.max_seconds is not None and args.max_seconds <= 0:
        parser.error("--max-seconds must be positive")
    if args.stats_interval < 1 or args.heavy_interval < 1:
        parser.error("statistics intervals must be positive")
    return args


def _record_ordinary(
    network: RawNetwork,
    step: int,
    rows: list[dict[str, Any]],
    density_history: list[float],
) -> SnapshotStats:
    stats = snapshot_stats(network, step)
    density_history.append(stats.mutual_density)
    row: dict[str, Any] = stats.as_dict()
    for window in ROLLING_SAMPLE_WINDOWS:
        values = density_history[-window:]
        prefix = f"rolling_{window}_samples"
        row[f"{prefix}_count"] = len(values)
        row[f"{prefix}_mean"] = fmean(values)
        row[f"{prefix}_std"] = pstdev(values)
        row[f"{prefix}_min"] = min(values)
        row[f"{prefix}_max"] = max(values)
    rows.append(row)
    return stats


def _print_progress(
    step: int,
    maximum: int,
    latest: SnapshotStats,
    *,
    stopped_by: str | None = None,
) -> None:
    percent = 100.0 if maximum == 0 else 100 * step / maximum
    suffix = f" stopped_by={stopped_by}" if stopped_by else ""
    print(
        f"progress: {percent:6.2f}% ({step}/{maximum} steps) "
        f"mutual={latest.mutual_connection_count} "
        f"density={latest.mutual_density:.6g} "
        f"stats_step={latest.step}{suffix}",
        file=sys.stderr,
        flush=True,
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _build_report(
    rows: list[dict[str, Any]], final: SnapshotStats, heavy: HeavyStats
) -> str:
    initial_density = float(rows[0]["mutual_density"])
    densities = [float(row["mutual_density"]) for row in rows]
    changed = abs(final.mutual_density - initial_density) > 1 / (3 * final.N)
    fluctuates = len(set(densities[-min(10, len(densities)) :])) > 1
    cycles = sum(
        (
            heavy.triangle_count,
            heavy.square_count,
            heavy.pentagon_count,
            heavy.hexagon_count,
        )
    )
    return f"""# Relation-endogenous experiment report

This report contains observations only; it does not claim a physical interpretation
or a fixed point.

- Mutual count changed clearly from initialization: {changed}.
- Final mutual density is nonzero: {final.mutual_density > 0}.
- Recent sampled density fluctuates: {fluctuates}.
- Persistent creation/destruction balance: not collected; it requires per-step bookkeeping.
- Cross-N density agreement: unresolved by a single run.
- Mutual lifetime divergence: not collected; it requires per-step bookkeeping.
- Non-isolated components at final stage: {heavy.connected_components_excluding_isolates}.
- Simple cycles of length 3--6 at final stage: {cycles}.
- Largest mutual component: {heavy.largest_mutual_component} of N={final.N}.
- Final duplicate-slot ratio: {final.duplicate_slot_ratio:.12g}.
- Final maximum mutual degree: {final.max_mutual_degree}.
- Final candidate size mean/median/p90/max: {heavy.mean_candidate_size:.6g} /
  {heavy.median_candidate_size:.6g} / {heavy.p90_candidate_size} /
  {heavy.max_candidate_size}.
- Closed candidate SCCs: {heavy.closed_candidate_scc_count}; largest size:
  {heavy.largest_closed_candidate_scc}; node ratio:
  {heavy.closed_candidate_scc_node_ratio:.12g}.
- Closed candidate SCC size 2 / size 3 counts:
  {heavy.closed_candidate_scc_size_2} / {heavy.closed_candidate_scc_size_3}.

Classification: F. unresolved. Turnover and lifetime evidence were deliberately
not collected because doing so would add statistics-only work to every rewrite.
"""


if __name__ == "__main__":
    main()
