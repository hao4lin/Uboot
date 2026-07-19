"""Deep-trace coarse relation candidates by exact deterministic baseline replay."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable

from uboot.adaptive_relation_tracer import (
    AnchorNeighborhoodQuery,
    GeneratedSlice,
    MotionCandidate,
    MOTION_CLASSIFICATIONS,
    PairSliceQuery,
    TraceCandidate,
    focus_localized_commit,
    generate_slice,
    trace_interval,
)
from uboot.adaptive_trace_candidates import load_trace_candidates
from uboot.commit_centered_relation_trace import (
    motion_v2_row,
    primary_row,
    report_v2,
    trace_commit_centered,
)
from uboot.deterministic_replay import (
    IMPACT_INDEX_MODES,
    ReplayOracle,
    build_replay_oracle,
    checkpoint_manifest_rows,
)
from uboot.kernel import SLOT_COUNT
from uboot.minimal_edge_experiment import network_hash, run_baseline_reference
from uboot.motion_reclassification import (
    reclassify_existing_motion_candidates,
    write_reclassified_csv,
)


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("interval-v1", "commit-centered-v2"),
        default="interval-v1",
    )
    parser.add_argument("--N", type=int, default=100, dest="size")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--sweeps", type=int, required=True)
    parser.add_argument("--checkpoint-sweeps", default="0,100,300,1000,3000,10000")
    parser.add_argument("--checkpoint-every-atoms", type=int, default=0)
    parser.add_argument("--candidate-source", type=Path, required=True)
    parser.add_argument("--candidate-count", type=int, default=20)
    parser.add_argument("--candidate-seed", type=int, default=20260718)
    parser.add_argument("--max-focus-level", type=int, choices=range(4), default=3)
    parser.add_argument("--max-probes-per-candidate", type=int, default=500)
    parser.add_argument(
        "--max-replay-atoms-per-candidate", type=int, default=50_000_000
    )
    parser.add_argument("--max-primary-commits-per-candidate", type=int, default=5)
    parser.add_argument("--local-window-atoms", default="8,32,128")
    parser.add_argument("--max-support-candidates-per-commit", type=int, default=32)
    parser.add_argument("--max-support-boundary-probes", type=int, default=128)
    parser.add_argument("--max-total-replay-atoms", type=int, default=50_000_000)
    parser.add_argument("--exclude-trivial-source-sibling-support", action="store_true")
    parser.add_argument(
        "--reclassify-input-dir",
        type=Path,
        default=ROOT / "artifacts" / "baseline_adaptive_relation_trace_N100_final",
    )
    parser.add_argument("--impact-index", choices=IMPACT_INDEX_MODES, default="all-commits")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.size <= SLOT_COUNT:
        raise SystemExit("N must exceed the three-slot count")
    if args.sweeps < 0 or args.candidate_count < 1:
        raise SystemExit("sweeps must be nonnegative and candidate count positive")
    if args.mode == "commit-centered-v2":
        positive_limits = (
            args.max_primary_commits_per_candidate,
            args.max_support_candidates_per_commit,
            args.max_support_boundary_probes,
            args.max_total_replay_atoms,
        )
        if any(value < 1 for value in positive_limits):
            raise SystemExit("v2 resource limits must be positive")
        if args.impact_index == "none":
            raise SystemExit("commit-centered-v2 requires an impact index")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    reporter = PercentReporter()
    try:
        candidates = load_trace_candidates(
            args.candidate_source,
            candidate_count=args.candidate_count,
            seed=args.candidate_seed,
        )
        if not candidates:
            raise RuntimeError("candidate source produced no trace candidates")
        max_locator = args.sweeps * SLOT_COUNT * args.size
        checkpoint_locators = _checkpoint_locators(args, max_locator)
        tracked_members = sorted(
            {raw_id for candidate in candidates for raw_id in candidate.involved_raw_ids}
        )
        reporter.phase("building checkpoints and impact index", 0)
        oracle = build_replay_oracle(
            size=args.size,
            seed=args.seed,
            max_locator=max_locator,
            checkpoint_locators=checkpoint_locators,
            impact_index_mode=args.impact_index,
            tracked_members=tracked_members,
            git_commit_hash=_git_version(),
            checkpoint_dir=output / "checkpoints",
            progress=reporter.build,
        )
        reporter.phase("tracing candidates", 50)
        if args.mode == "commit-centered-v2":
            windows = tuple(
                sorted(
                    {
                        int(value.strip())
                        for value in args.local_window_atoms.split(",")
                        if value.strip()
                    }
                )
            )
            if not windows or any(value < 1 for value in windows):
                raise SystemExit("local windows must contain positive atom counts")
            traced = trace_commit_centered(
                oracle,
                candidates,
                max_primary_commits_per_candidate=args.max_primary_commits_per_candidate,
                local_windows=windows,
                max_support_candidates_per_commit=args.max_support_candidates_per_commit,
                max_support_boundary_probes=args.max_support_boundary_probes,
                max_total_replay_atoms=args.max_total_replay_atoms,
                progress=reporter.trace,
            )
        else:
            traced = _trace_candidates(args, oracle, candidates, reporter)
        reporter.phase("verifying direct baseline", 92)
        direct_network, direct_rng = run_baseline_reference(
            size=args.size,
            seed=args.seed,
            sweeps=args.sweeps,
            progress=reporter.reference,
        )
        final = oracle.checkpoints[max_locator]
        verification = {
            "baseline_final_network_hash_match": final.network_hash
            == network_hash(direct_network),
            "baseline_final_rng_state_match": final.rng_state == direct_rng,
            "baseline_final_network_hash": final.network_hash,
            "baseline_final_rng_state_hash": final.rng_state_hash,
            "fingerprint_hash": oracle.fingerprint.fingerprint_hash,
            "impact_index_mode": oracle.impact_index.mode,
            "impact_record_count": len(oracle.impact_index),
        }
        if not all(
            verification[key]
            for key in (
                "baseline_final_network_hash_match",
                "baseline_final_rng_state_match",
            )
        ):
            raise RuntimeError("replay trajectory diverged from direct baseline")
        reporter.phase("writing outputs", 98)
        if args.mode == "commit-centered-v2":
            _write_v2_outputs(
                output,
                args,
                oracle,
                candidates,
                traced,
                verification,
                time.monotonic() - started,
            )
        else:
            _write_outputs(
                output,
                args,
                oracle,
                candidates,
                traced,
                verification,
                time.monotonic() - started,
            )
        reporter.phase("complete", 100)
    except BaseException:
        print(f"incomplete output retained at {output}", file=sys.stderr)
        raise
    print(f"complete: {output}", file=sys.stderr)


def _trace_candidates(
    args: argparse.Namespace,
    oracle: ReplayOracle,
    candidates: tuple[TraceCandidate, ...],
    reporter: "PercentReporter",
) -> dict[str, list[Any]]:
    results = []
    motions: list[MotionCandidate] = []
    pair_slices = []
    three_slices = []
    for index, candidate in enumerate(candidates, start=1):
        query = _candidate_query(candidate)
        if len(candidate.coarse_locators) < 2:
            continue
        result = trace_interval(
            oracle,
            candidate_id=candidate.candidate_id,
            left_locator=candidate.coarse_locators[0],
            right_locator=candidate.coarse_locators[-1],
            query=query,
            max_probes=args.max_probes_per_candidate,
            max_replay_atoms=args.max_replay_atoms_per_candidate,
        )
        results.append(result)
        for localized in result.localized_commits:
            transition = localized.transition
            details = transition.details
            before_pair = generate_slice(
                transition.before_state.network,
                PairSliceQuery(details.source_raw_id, details.old_target),
            )
            after_pair = generate_slice(
                transition.after_state.network,
                PairSliceQuery(details.source_raw_id, details.actual_new_target),
            )
            pair_slices.extend(
                (
                    _generated_slice_row(localized, "before", before_pair),
                    _generated_slice_row(localized, "after", after_pair),
                )
            )
            focused = focus_localized_commit(
                localized, max_focus_level=args.max_focus_level
            )
            motions.extend(focused)
            for motion in focused:
                three_slices.extend(
                    (
                        _motion_evidence_row(motion, "before"),
                        _motion_evidence_row(motion, "after"),
                    )
                )
        reporter.trace(index, len(candidates), candidate.candidate_id, result.status)
    return {
        "results": results,
        "motions": motions,
        "pair_slices": pair_slices,
        "three_slices": three_slices,
    }


def _write_outputs(
    output: Path,
    args: argparse.Namespace,
    oracle: ReplayOracle,
    candidates: tuple[TraceCandidate, ...],
    traced: dict[str, list[Any]],
    verification: dict[str, Any],
    runtime_seconds: float,
) -> None:
    fingerprint = asdict(oracle.fingerprint)
    fingerprint["fingerprint_hash"] = oracle.fingerprint.fingerprint_hash
    (output / "run_fingerprint.json").write_text(
        json.dumps(fingerprint, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest = {
        "fingerprint_hash": oracle.fingerprint.fingerprint_hash,
        "checkpoints": checkpoint_manifest_rows(oracle),
    }
    (output / "checkpoint_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    _write_csv(
        output / "trace_candidates.csv",
        (
            {
                **asdict(candidate),
                "involved_raw_ids": _join(candidate.involved_raw_ids),
                "query_raw_ids": _join(candidate.query_raw_ids),
                "coarse_locators": _join(candidate.coarse_locators),
            }
            for candidate in candidates
        ),
    )
    results = traced["results"]
    _write_csv(
        output / "adaptive_probe_log.csv",
        (asdict(probe) for result in results for probe in result.probes),
    )
    localized = [item for result in results for item in result.localized_commits]
    _write_csv(
        output / "localized_commits.csv",
        (_localized_row(item) for item in localized),
        (
            "candidate_id",
            "last_locator_with_left_content",
            "first_locator_with_right_content",
            "commit_locator",
            "source_raw_id",
            "slot_semantic",
            "old_target",
            "candidate_target",
            "new_target",
            "directly_modified_slot",
            "indirectly_affected_raw_ids",
            "rng_hash_before",
            "rng_hash_after",
            "before_slice_hash",
            "after_slice_hash",
            "content_diff",
        ),
    )
    generated_fields = (
        "candidate_id",
        "commit_locator",
        "side",
        "query_type",
        "member_ids",
        "slice_hash",
        "slice_serialization",
    )
    _write_csv(
        output / "generated_pair_slices.csv",
        traced["pair_slices"],
        generated_fields,
    )
    _write_csv(
        output / "generated_three_member_slices.csv",
        traced["three_slices"],
        generated_fields,
    )
    _write_csv(
        output / "motion_candidates.csv",
        (_motion_csv_row(item) for item in traced["motions"]),
        (
            "candidate_id",
            "commit_locator",
            "before_member_ids",
            "after_member_ids",
            "preserved_member_ids",
            "preserved_internal_relations",
            "changed_members",
            "changed_relations",
            "classification",
            "evidence_slice_before",
            "evidence_slice_after",
        ),
    )
    summary = {
        "runtime_seconds": runtime_seconds,
        "candidate_count": len(candidates),
        "traced_candidate_count": len(results),
        "status_counts": Counter(result.status for result in results),
        "localized_commit_count": len(localized),
        "motion_candidate_count": len(traced["motions"]),
        "motion_rows": traced["motions"],
        "classification_counts": Counter(
            item.classification for item in traced["motions"]
        ),
        "total_probes": sum(len(result.probes) for result in results),
        "total_replayed_atoms": sum(result.replayed_atom_count for result in results),
        "verification": verification,
        "resource_limits": {
            "max_probes_per_candidate": args.max_probes_per_candidate,
            "max_replay_atoms_per_candidate": args.max_replay_atoms_per_candidate,
            "max_focus_level": args.max_focus_level,
        },
    }
    (output / "trace_report.md").write_text(
        _report(summary, results), encoding="utf-8"
    )


def _write_v2_outputs(
    output: Path,
    args: argparse.Namespace,
    oracle: ReplayOracle,
    candidates: tuple[TraceCandidate, ...],
    traced: Any,
    verification: dict[str, Any],
    runtime_seconds: float,
) -> None:
    fingerprint = asdict(oracle.fingerprint)
    fingerprint["fingerprint_hash"] = oracle.fingerprint.fingerprint_hash
    (output / "run_fingerprint.json").write_text(
        json.dumps(fingerprint, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output / "checkpoint_manifest.json").write_text(
        json.dumps(
            {
                "fingerprint_hash": oracle.fingerprint.fingerprint_hash,
                "checkpoints": checkpoint_manifest_rows(oracle),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _write_csv(
        output / "trace_candidates.csv",
        (
            {
                **asdict(candidate),
                "involved_raw_ids": _join(candidate.involved_raw_ids),
                "query_raw_ids": _join(candidate.query_raw_ids),
                "coarse_locators": _join(candidate.coarse_locators),
            }
            for candidate in candidates
        ),
    )
    _write_csv(
        output / "primary_change_commits.csv",
        (primary_row(case.primary) for case in traced.cases),
        (
            "candidate_id",
            "commit_locator",
            "source_raw_id",
            "slot_semantic",
            "old_target",
            "new_target",
            "primary_change_type",
            "source_artifact",
        ),
    )
    with (output / "commit_centered_slice_bundles.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        for case in traced.cases:
            handle.write(
                json.dumps(case.bundle, sort_keys=True, separators=(",", ":")) + "\n"
            )
    _write_csv(
        output / "support_candidates.csv",
        (row for case in traced.cases for row in case.support_candidates),
        (
            "candidate_id",
            "primary_commit_locator",
            "third_raw_id",
            "candidate_groups",
            "source_sibling_only",
        ),
    )
    _write_csv(
        output / "support_change_commits.csv",
        (row for case in traced.cases for row in case.support_changes),
        (
            "candidate_id",
            "primary_commit_locator",
            "support_commit_locator",
            "support_relation_before",
            "support_relation_after",
            "shared_raw_members",
            "role_mapping",
            "distance_in_locator",
        ),
    )
    _write_csv(
        output / "motion_candidates_v2.csv",
        (motion_v2_row(case) for case in traced.cases),
    )

    old_root = args.reclassify_input_dir.resolve()
    old_rows: tuple[dict[str, Any], ...] = ()
    required = (
        old_root / "motion_candidates.csv",
        old_root / "localized_commits.csv",
        old_root / "generated_three_member_slices.csv",
    )
    if all(path.exists() for path in required):
        old_rows = reclassify_existing_motion_candidates(*required)
    write_reclassified_csv(output / "motion_candidates_reclassified.csv", old_rows)
    old_counts = Counter(row["v2_reclassification"] for row in old_rows)
    (output / "adaptive_trace_v2_report.md").write_text(
        report_v2(
            traced,
            verification=verification,
            old_reclassification_counts=old_counts,
            runtime_seconds=runtime_seconds,
        ),
        encoding="utf-8",
    )


class PercentReporter:
    def __init__(self) -> None:
        self.last_percent = -5
        self.last_time = 0.0

    def build(self, completed: int, total: int) -> None:
        self._emit(50 * completed / max(1, total), "building checkpoints and impact index")

    def trace(self, completed: int, total: int, candidate: str, status: str) -> None:
        self._emit(
            50 + 42 * completed / max(1, total),
            f"tracing {candidate} status={status}",
            force=True,
        )

    def reference(self, completed: int, total: int) -> None:
        self._emit(92 + 6 * completed / max(1, total), "verifying direct baseline")

    def phase(self, label: str, percent: int) -> None:
        self._emit(percent, label, force=True)

    def _emit(self, percent: float, label: str, *, force: bool = False) -> None:
        now = time.monotonic()
        rounded = min(100, int(percent))
        if force or rounded >= self.last_percent + 5 or now - self.last_time >= 60:
            print(f"progress={rounded}% phase={label}", file=sys.stderr, flush=True)
            self.last_percent = rounded
            self.last_time = now


def _candidate_query(candidate: TraceCandidate):
    if candidate.query_type == "pair":
        return PairSliceQuery(*candidate.query_raw_ids)
    if candidate.query_type == "anchor_neighborhood":
        return AnchorNeighborhoodQuery(candidate.query_raw_ids[0])
    raise ValueError(f"unsupported candidate query type: {candidate.query_type}")


def _checkpoint_locators(args: argparse.Namespace, max_locator: int) -> tuple[int, ...]:
    locators = {
        int(value.strip()) * SLOT_COUNT * args.size
        for value in args.checkpoint_sweeps.split(",")
        if value.strip()
    }
    if args.checkpoint_every_atoms > 0:
        locators.update(range(0, max_locator + 1, args.checkpoint_every_atoms))
    return tuple(sorted(locator for locator in locators | {0, max_locator} if locator <= max_locator))


def _generated_slice_row(localized: Any, side: str, item: GeneratedSlice) -> dict[str, Any]:
    return {
        "candidate_id": localized.candidate_id,
        "commit_locator": localized.responsible_commit_locator,
        "side": side,
        "query_type": item.query_type,
        "member_ids": _join(item.member_ids),
        "slice_hash": item.slice_hash,
        "slice_serialization": item.serialization,
    }


def _motion_evidence_row(motion: MotionCandidate, side: str) -> dict[str, Any]:
    serialization = (
        motion.evidence_slice_before if side == "before" else motion.evidence_slice_after
    )
    return {
        "candidate_id": motion.candidate_id,
        "commit_locator": motion.commit_locator,
        "side": side,
        "query_type": "ThreeMemberSliceQuery",
        "member_ids": _join(
            motion.before_member_ids if side == "before" else motion.after_member_ids
        ),
        "slice_hash": sha256(serialization.encode()).hexdigest(),
        "slice_serialization": serialization,
    }


def _localized_row(item: Any) -> dict[str, Any]:
    details = item.transition.details
    return {
        "candidate_id": item.candidate_id,
        "last_locator_with_left_content": item.last_locator_with_left_content,
        "first_locator_with_right_content": item.first_locator_with_right_content,
        "commit_locator": item.responsible_commit_locator,
        "source_raw_id": details.source_raw_id,
        "slot_semantic": details.slot_semantic,
        "old_target": details.old_target,
        "candidate_target": details.candidate_target,
        "new_target": details.actual_new_target,
        "directly_modified_slot": details.directly_modified_slot,
        "indirectly_affected_raw_ids": _join(details.indirectly_affected_raw_ids),
        "rng_hash_before": details.rng_hash_before,
        "rng_hash_after": details.rng_hash_after,
        "before_slice_hash": item.transition.slice_before.slice_hash,
        "after_slice_hash": item.transition.slice_after.slice_hash,
        "content_diff": item.content_diff,
    }


def _motion_csv_row(item: MotionCandidate) -> dict[str, Any]:
    row = asdict(item)
    for key in (
        "before_member_ids",
        "after_member_ids",
        "preserved_member_ids",
        "preserved_internal_relations",
        "changed_members",
        "changed_relations",
    ):
        row[key] = _join(row[key])
    return row


def _write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: Iterable[str] | None = None,
) -> None:
    materialized = list(rows)
    columns = list(fieldnames or dict.fromkeys(key for row in materialized for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if columns:
            writer.writeheader()
            writer.writerows(materialized)


def _report(summary: dict[str, Any], results: list[Any]) -> str:
    candidate_count = max(1, summary["traced_candidate_count"])
    hidden = sum(
        result.localized_commits
        and result.probes[0].slice_hash == result.probes[1].slice_hash
        for result in results
    )
    incomplete = summary["status_counts"].get("INCOMPLETE_RESOURCE_LIMIT", 0)
    classifications = summary["classification_counts"]
    support_candidate_ids = {
        item.candidate_id
        for item in summary["motion_rows"]
        if item.classification == "NEIGHBOR_CHANGED_BODY_SUPPORT_PRESERVED"
    }
    mean_probes = summary["total_probes"] / candidate_count
    mean_replay = summary["total_replayed_atoms"] / candidate_count
    verification = summary["verification"]
    lines = [
        "# Deterministic adaptive relation replay report",
        "",
        "Locators below are deterministic recording positions only. They do not define "
        "object time, motion direction, age, birth, death, speed, or causality.",
        "",
        "## Questions",
        "",
        "1. Byte-exact deterministic replay: network hash match "
        f"{verification['baseline_final_network_hash_match']}; RNG match "
        f"{verification['baseline_final_rng_state_match']}.",
        "2. Coarse candidates localized to commits: "
        f"{sum(bool(result.localized_commits) for result in results)}/{len(results)}.",
        f"3. Equal-endpoint intervals with hidden changes: {hidden}.",
        "4. T1/T2 investigations ending with one-member-only support: "
        f"{classifications.get('ONE_MEMBER_ONLY', 0)}.",
        "5. Candidates with at least one inspected commit preserving two raw members "
        f"plus an internal relation: {len(support_candidate_ids)}.",
        "6. Neighbor changed while body-local support stayed: "
        f"{len(support_candidate_ids)} candidates, "
        f"{classifications.get('NEIGHBOR_CHANGED_BODY_SUPPORT_PRESERVED', 0)} "
        "representative commits. This minimum is mechanically easy to satisfy because "
        "one slot can change while sibling source slots remain unchanged.",
        "7. Body support also alignably changed: "
        f"{classifications.get('BODY_AND_NEIGHBOR_CHANGED_LOCAL_SUPPORT_PRESERVED', 0)}.",
        "8. Long-gap random reunion only: unresolved; failed localization is not "
        "classified as reunion.",
        f"9. Mean probes/candidate={mean_probes:.2f}; mean replay atoms/candidate={mean_replay:.2f}.",
        "10. Compute-for-storage feasibility: runtime "
        f"{summary['runtime_seconds']:.2f}s; incomplete resource-limited candidates={incomplete}.",
        "",
        "## Counts",
        "",
        f"- Candidate status: {dict(summary['status_counts'])}",
        "- Motion classifications: "
        + str({kind: classifications.get(kind, 0) for kind in MOTION_CLASSIFICATIONS}),
        f"- Localized commits: {summary['localized_commit_count']}",
        f"- Impact records: {verification['impact_record_count']}",
    ]
    return "\n".join(lines) + "\n"


def _git_version() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    commit = completed.stdout.strip() or "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return commit + ("+dirty" if dirty else "")


def _join(values: Iterable[object]) -> str:
    return "|".join(map(str, values))


if __name__ == "__main__":
    main()
