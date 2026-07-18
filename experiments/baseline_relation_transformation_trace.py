"""Observe order-neutral local relation transformations in the random baseline."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Iterable

from uboot.minimal_edge_experiment import (
    MinimalEdgeEngine,
    initial_network,
    network_hash,
    run_baseline_reference,
    state_hash,
)
from uboot.minimal_edge_rules import BaselineRandomReplace
from uboot.relation_transform_observer import (
    BODY_SAME_NEIGHBOR_DIFFERENT,
    MEMBERS_SAME_RELATION_DIFFERENT,
    NEIGHBOR_SAME_BODY_DIFFERENT,
    ONE_MEMBER_OVERLAP_ROLE_CHANGED,
    RelationTransformationObserver,
    transformation_rows,
)


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--N", type=int, default=100, dest="size")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--observer-seed", type=int, default=20260718)
    parser.add_argument("--sweeps", type=int, required=True)
    parser.add_argument("--tracked-anchor-count", type=int, default=10)
    parser.add_argument("--snapshot-sweeps", default="0,100,300,1000,3000,10000")
    parser.add_argument("--max-transformation-path-length", type=int, default=4)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.size != 100:
        raise SystemExit("the first relation-transformation run is restricted to N=100")
    if args.sweeps < 0:
        raise SystemExit("--sweeps must be non-negative")
    snapshots = tuple(
        sweep
        for sweep in sorted(
            set(int(value) for value in _csv_values(args.snapshot_sweeps))
            | {0, args.sweeps}
        )
        if 0 <= sweep <= args.sweeps
    )
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    reporter = PercentReporter(args.sweeps * args.size * 3)
    try:
        result = _run(args, snapshots, reporter)
        _write_outputs(output, args, result)
        reporter.phase("complete", 100)
    except BaseException:
        print(f"incomplete output retained at {output}", file=sys.stderr)
        raise
    print(f"complete: {output}", file=sys.stderr)


def _run(
    args: argparse.Namespace,
    snapshots: tuple[int, ...],
    reporter: "PercentReporter",
) -> dict[str, Any]:
    from random import Random

    rng = Random(args.seed)
    engine = MinimalEdgeEngine(
        initial_network(args.size, rng), rng, BaselineRandomReplace()
    )
    observer = RelationTransformationObserver(
        args.size,
        args.observer_seed,
        args.tracked_anchor_count,
        args.max_transformation_path_length,
    )
    for sweep in snapshots:
        engine.run_to_sweep(
            sweep,
            "baseline_random_replace",
            lambda policy, tick, target: reporter.simulation(tick),
        )
        observer.observe(engine.snapshot(), atom_step=engine.tick, snapshot=sweep)
    reporter.phase("verifying baseline", 60)
    reference_network, reference_rng = run_baseline_reference(
        size=args.size,
        seed=args.seed,
        sweeps=args.sweeps,
        progress=reporter.reference,
    )
    final_network = engine.snapshot()
    hash_match = network_hash(final_network) == network_hash(reference_network)
    rng_match = engine.rng.getstate() == reference_rng
    if not hash_match or not rng_match:
        raise RuntimeError("observer changed baseline final state or main RNG")
    reporter.phase("building transformation graph", 90)
    transformations = observer.transformations()
    reporter.phase("building order-neutral paths", 94)
    paths = observer.paths(transformations, reporter.paths)
    reporter.phase("writing outputs", 98)
    return {
        "observer": observer,
        "transformations": transformations,
        "paths": paths,
        "verification": {
            "baseline_final_hash_match": hash_match,
            "baseline_main_rng_state_match": rng_match,
            "baseline_final_hash": network_hash(final_network),
            "baseline_final_rng_state_hash": state_hash(engine.rng.getstate()),
            "observer_seed": args.observer_seed,
            "tracked_anchor_ids": observer.anchor_ids,
            "anchor_sampling": "seeded_random_permutation_prefix",
            "snapshot_sweeps": snapshots,
            "identity_uses_observation_locator": False,
            "transformations_use_observation_order": False,
            "paths_use_observation_order": False,
            "missing_exposure_has_state_meaning": False,
        },
    }


class PercentReporter:
    def __init__(self, total_ticks: int) -> None:
        self.total_ticks = max(1, total_ticks)
        self.last_percent = -5
        self.last_time = 0.0

    def simulation(self, tick: int) -> None:
        self._emit(60 * tick / self.total_ticks, "sampling baseline")

    def reference(self, tick: int, total: int) -> None:
        self._emit(60 + 30 * tick / max(1, total), "verifying baseline")

    def paths(self, completed: int, total: int) -> None:
        self._emit(
            94 + 4 * completed / max(1, total),
            "building order-neutral paths",
        )

    def phase(self, label: str, percent: int) -> None:
        self._emit(percent, label, force=True)

    def _emit(self, percent: float, label: str, *, force: bool = False) -> None:
        now = time.monotonic()
        rounded = min(100, int(percent))
        if force or rounded >= self.last_percent + 5 or now - self.last_time >= 60:
            print(f"progress={rounded}% phase={label}", file=sys.stderr, flush=True)
            self.last_percent = rounded
            self.last_time = now


def _write_outputs(
    output: Path,
    args: argparse.Namespace,
    result: dict[str, Any],
) -> None:
    observer: RelationTransformationObserver = result["observer"]
    transformations = result["transformations"]
    paths = result["paths"]
    slices = observer.slice_rows()
    equivalence = observer.equivalence_groups()
    transformation_table = transformation_rows(transformations)
    anchor_summary = observer.anchor_summary(transformations, paths)
    _write_csv(
        output / "relation_slices.csv",
        slices,
        (
            "slice_id",
            "anchor_id",
            "relation_kind",
            "slot_semantics",
            "left_raw_id",
            "right_raw_id",
            "canonical_signature",
            "observation_locator",
            "observation_count",
        ),
    )
    _write_csv(
        output / "slice_equivalence_groups.csv",
        equivalence,
        (
            "equivalence_group_id",
            "canonical_signature",
            "slice_count",
            "slice_ids",
            "raw_member_sets",
            "canonical_same_raw_disjoint_pair_count",
        ),
    )
    _write_csv(
        output / "slice_transformations.csv",
        transformation_table,
        (
            "slice_a_id",
            "slice_b_id",
            "transformation_type",
            "shared_raw_ids",
            "changed_raw_ids",
            "same_relation_kind",
            "same_slot_semantics",
            "role_mapping",
        ),
    )
    _write_csv(
        output / "transformation_paths.csv",
        paths,
        (
            "path_length",
            "slice_ids",
            "transformation_types",
            "shared_body_sequence",
            "shared_neighbor_sequence",
            "raw_member_union",
            "canonical_pattern",
        ),
    )
    _write_csv(
        output / "anchor_relation_summary.csv",
        anchor_summary,
        (
            "anchor_id",
            "unique_relation_slices",
            "unique_raw_neighbor_ids",
            "unique_canonical_relations",
            "body_same_neighbor_different_count",
            "neighbor_same_body_different_count",
            "members_same_relation_different_count",
            "relation_same_members_different_count",
            "role_changed_overlap_count",
            "max_body_preserving_family_size",
            "max_neighbor_preserving_family_size",
            "transformation_path_count_length_2",
            "transformation_path_count_length_3",
            "transformation_path_count_length_4",
        ),
    )
    verification = {
        **result["verification"],
        "N": args.size,
        "seed": args.seed,
        "sweeps": args.sweeps,
        "tracked_anchor_count": args.tracked_anchor_count,
        "max_transformation_path_length": args.max_transformation_path_length,
        "unique_slice_count": len(slices),
        "transformation_count": len(transformations),
        "transformation_path_count": len(paths),
        "equivalence_group_count": len(equivalence),
        "canonical_same_raw_disjoint_pair_count": sum(
            row["canonical_same_raw_disjoint_pair_count"] for row in equivalence
        ),
    }
    (output / "observer_verification.json").write_text(
        json.dumps(verification, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output / "relation_transformation_report.md").write_text(
        _report(observer, transformations, paths, anchor_summary, verification),
        encoding="utf-8",
    )
    shutil.copyfile(
        ROOT / "docs" / "relation_transformation_observer_semantics.md",
        output / "relation_transformation_observer_semantics.md",
    )


def _report(
    observer: RelationTransformationObserver,
    transformations: tuple[Any, ...],
    paths: tuple[dict[str, Any], ...],
    anchor_summary: tuple[dict[str, Any], ...],
    verification: dict[str, Any],
) -> str:
    type_counts = Counter(item.transformation_type for item in transformations)
    shared_counts: Counter[int] = Counter(
        raw_id for item in transformations for raw_id in item.shared_raw_ids
    )
    stable_role_counts: Counter[int] = Counter(
        raw_id
        for item in transformations
        for raw_id, role_a, role_b in item.role_mapping
        if role_a == role_b
    )
    path_types = Counter(
        kind for path in paths for kind in path["transformation_types"].split("|")
    )
    max_body = max(
        anchor_summary,
        key=lambda row: row["max_body_preserving_family_size"],
        default=None,
    )
    max_neighbor = max(
        anchor_summary,
        key=lambda row: row["max_neighbor_preserving_family_size"],
        default=None,
    )
    top_shared = shared_counts.most_common(10)
    top_stable_role = stable_role_counts.most_common(10)
    lines = [
        "# Local relation slice same/different transformation report",
        "",
        "Observation locators are source references only. No result below uses "
        "their ordering as object time or edge direction.",
        "",
        f"Tracked anchors: `{','.join(map(str, observer.anchor_ids))}`.",
        f"Unique relation slices: {len(observer.records)}.",
        f"Undirected transformation edges: {len(transformations)}.",
        f"Chordless transformation paths (2--{observer.max_path_length} edges): {len(paths)}.",
        "",
        "## Questions",
        "",
        "1. Body-same/neighbor-different families: "
        + _family_answer(max_body, "max_body_preserving_family_size"),
        "2. Neighbor-same/body-different families: "
        + _family_answer(max_neighbor, "max_neighbor_preserving_family_size"),
        "3. Raw objects repeatedly retaining roles: top stable-role objects are "
        + str(top_stable_role),
        "4. Stable content dimensions: compare canonical groups, slot-preserving "
        "edge counts, role mappings, and raw-member groups in the CSV outputs.",
        "5. Composable transformation paths: "
        + (f"{len(paths)} found." if paths else "none found."),
        "6. Path composition by transformation type: " + str(dict(path_types)),
        "7. Raw objects retained across many transformations: " + str(top_shared),
        "8. Relation positions with changing raw occupants: canonical raw-disjoint "
        "pair count is "
        f"{verification['canonical_same_raw_disjoint_pair_count']}; details are "
        "aggregated in `slice_equivalence_groups.csv` without global pairwise edges.",
        "",
        "## Transformation counts",
        "",
    ]
    lines.extend(
        f"- {kind}: {type_counts[kind]}"
        for kind in (
            BODY_SAME_NEIGHBOR_DIFFERENT,
            NEIGHBOR_SAME_BODY_DIFFERENT,
            MEMBERS_SAME_RELATION_DIFFERENT,
            ONE_MEMBER_OVERLAP_ROLE_CHANGED,
        )
    )
    lines.append(
        "- RELATION_SAME_MEMBERS_DIFFERENT: secondary per-anchor counts are in "
        "`anchor_relation_summary.csv`."
    )
    lines.extend(
        [
            "",
            "## Verification",
            "",
            f"- Baseline final hash match: {verification['baseline_final_hash_match']}.",
            f"- Main RNG state match: {verification['baseline_main_rng_state_match']}.",
            "- Slice identity uses no observation locator.",
            "- Transformation edges and paths use no observation ordering.",
            "- An unexposed relation at another locator receives no state meaning.",
        ]
    )
    return "\n".join(lines) + "\n"


def _family_answer(row: dict[str, Any] | None, key: str) -> str:
    if row is None:
        return "none."
    return f"anchor {row['anchor_id']} has maximum family size {row[key]}."


def _write_csv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    fieldnames: tuple[str, ...],
) -> None:
    materialized = list(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(materialized)


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


if __name__ == "__main__":
    main()
