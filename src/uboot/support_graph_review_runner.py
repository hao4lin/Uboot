"""Load bounded v1/v2 evidence and perform deterministic v3 graph review."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from uboot.adaptive_relation_tracer import PairSliceQuery
from uboot.deterministic_replay import ReplayOracle
from uboot.support_graph_review import (
    ReviewedSupport,
    V3_STRENGTH,
    generate_background_controls,
    graph_payload,
    review_support_graph,
)


@dataclass(frozen=True, slots=True)
class ReviewInput:
    review_id: str
    evidence_origin: str
    candidate_id: str
    primary_commit_locator: int
    source_raw_id: int
    slot_semantic: int
    old_neighbor: int
    new_neighbor: int
    previous_classification: str
    previous_resource_status: str


@dataclass(frozen=True, slots=True)
class ReviewCase:
    source: ReviewInput
    reviewed: ReviewedSupport
    controls: tuple[ReviewedSupport, ...]
    enrichment_status: str = "NOT_APPLICABLE"


def load_review_inputs(v1_dir: Path, v2_dir: Path) -> tuple[ReviewInput, ...]:
    v1_localized = {
        (row["candidate_id"], row["commit_locator"]): row
        for row in _read_csv(v1_dir / "localized_commits.csv")
    }
    v1_rows = [
        row
        for row in _read_csv(v2_dir / "motion_candidates_reclassified.csv")
        if row["v2_reclassification"] == "STRONG_RAW_SUPPORT"
    ]
    inputs = []
    for index, row in enumerate(v1_rows, start=1):
        localized = v1_localized[(row["candidate_id"], row["commit_locator"])]
        inputs.append(
            ReviewInput(
                f"V1-{index:03d}",
                "V1_RECLASSIFIED_STRONG_RAW",
                row["candidate_id"],
                int(row["commit_locator"]),
                int(localized["source_raw_id"]),
                int(localized["slot_semantic"]),
                int(localized["old_target"]),
                int(localized["new_target"]),
                "STRONG_RAW_SUPPORT",
                "SAVED_V1_CONTEXT_REPLAYED_AT_COMMIT",
            )
        )
    v2_rows = [
        row
        for row in _read_csv(v2_dir / "motion_candidates_v2.csv")
        if int(row["support_strength"]) >= 3
    ]
    for index, row in enumerate(v2_rows, start=1):
        inputs.append(
            ReviewInput(
                f"V2-{index:03d}",
                "V2_STRONG_RAW",
                row["candidate_id"],
                int(row["primary_commit_locator"]),
                int(row["source_raw_id"]),
                int(row["slot_semantic"]) if row.get("slot_semantic") else -1,
                int(row["old_neighbor"]),
                int(row["new_neighbor"]),
                row["support_classification"],
                row["resource_status"],
            )
        )
    return tuple(inputs)


def run_support_graph_review(
    oracle: ReplayOracle,
    inputs: tuple[ReviewInput, ...],
    *,
    control_seed: int = 20260718,
    controls_per_case: int = 10,
    max_local_radius: int = 1,
    require_primary_role_participation: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[ReviewCase, ...]:
    transitions = oracle.inspect_commit_sequence(
        (item.primary_commit_locator for item in inputs), PairSliceQuery(0, 1)
    )
    transition_by_locator = {
        item.details.commit_locator: item for item in transitions
    }
    cases = []
    for index, source in enumerate(inputs, start=1):
        transition = transition_by_locator[source.primary_commit_locator]
        details = transition.details
        if (
            details.source_raw_id != source.source_raw_id
            or details.old_target != source.old_neighbor
            or details.actual_new_target != source.new_neighbor
        ):
            raise ValueError(f"saved primary fields disagree with replay: {source.review_id}")
        reviewed = review_support_graph(
            transition.before_state.network,
            transition.after_state.network,
            details,
            max_local_radius=max_local_radius,
            require_primary_role_participation=require_primary_role_participation,
        )
        controls = []
        derived_seed = _derived_seed(control_seed, source.review_id)
        control_specs = generate_background_controls(
            transition.before_state.network,
            real_source=details.source_raw_id,
            real_old=details.old_target,
            real_new=details.actual_new_target,
            slot_semantic=details.slot_semantic,
            count=controls_per_case,
            seed=derived_seed,
        )
        for control_source, control_old, control_new in control_specs:
            control_before = transition.before_state.network
            control_after = control_before.retarget(
                control_source, details.slot_semantic, control_new
            )
            control_commit = SimpleNamespace(
                source_raw_id=control_source,
                slot_semantic=details.slot_semantic,
                old_target=control_old,
                new_target=control_new,
            )
            controls.append(
                review_support_graph(
                    control_before,
                    control_after,
                    control_commit,
                    max_local_radius=max_local_radius,
                    require_primary_role_participation=require_primary_role_participation,
                )
            )
        cases.append(ReviewCase(source, reviewed, tuple(controls)))
        if progress is not None:
            progress(index, len(inputs))
    return _apply_enrichment(tuple(cases))


def write_review_outputs(
    output: Path,
    cases: tuple[ReviewCase, ...],
    *,
    old_reclassification_rows: list[dict[str, str]],
    verification: dict[str, Any],
    runtime_seconds: float,
) -> None:
    with (output / "support_graphs_v3.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        for case in cases:
            payload = {
                "review_id": case.source.review_id,
                "evidence_origin": case.source.evidence_origin,
                "candidate_id": case.source.candidate_id,
                "primary_commit_locator": case.source.primary_commit_locator,
                "evidence_kind": "REAL",
                **graph_payload(case.reviewed),
                "controls": [graph_payload(item) for item in case.controls],
            }
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    motif_rows = []
    motion_rows = []
    for case in cases:
        motif_rows.append(_motif_row(case, case.reviewed, "REAL", 0))
        motif_rows.extend(
            _motif_row(case, control, "CONTROL", index)
            for index, control in enumerate(case.controls, start=1)
        )
        motion_rows.append(_motion_row(case))
    _write_csv(output / "support_motifs_v3.csv", motif_rows)
    _write_csv(output / "motion_candidates_v3.csv", motion_rows)
    (output / "support_v3_report.md").write_text(
        _report(cases, old_reclassification_rows, verification, runtime_seconds),
        encoding="utf-8",
    )


def _apply_enrichment(cases: tuple[ReviewCase, ...]) -> tuple[ReviewCase, ...]:
    real = Counter(case.reviewed.review.v3_classification for case in cases)
    controls = Counter(
        control.review.v3_classification
        for case in cases
        for control in case.controls
    )
    real_total = max(1, len(cases))
    control_total = max(1, sum(map(len, (case.controls for case in cases))))
    rows = []
    for case in cases:
        classification = case.reviewed.review.v3_classification
        strength = case.reviewed.review.support_strength
        status = "NOT_APPLICABLE"
        reviewed = case.reviewed
        if strength >= V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]:
            real_rate = real[classification] / real_total
            control_rate = controls[classification] / control_total
            status = "ENRICHED" if real_rate > control_rate else "STRUCTURED_BUT_NOT_ENRICHED"
            if status == "STRUCTURED_BUT_NOT_ENRICHED":
                reviewed = ReviewedSupport(
                    reviewed.graph,
                    replace(
                        reviewed.review,
                        support_strength=V3_STRENGTH["WEAK_BACKGROUND"],
                        evidence_tags=reviewed.review.evidence_tags + (status,),
                    ),
                )
        rows.append(replace(case, reviewed=reviewed, enrichment_status=status))
    return tuple(rows)


def _motion_row(case: ReviewCase) -> dict[str, Any]:
    source = case.source
    graph = case.reviewed.graph
    review = case.reviewed.review
    return {
        "review_id": source.review_id,
        "evidence_origin": source.evidence_origin,
        "candidate_id": source.candidate_id,
        "primary_commit_locator": source.primary_commit_locator,
        "source_raw_id": source.source_raw_id,
        "old_neighbor": source.old_neighbor,
        "new_neighbor": source.new_neighbor,
        "previous_classification": source.previous_classification,
        "v3_classification": review.v3_classification,
        "support_strength": review.support_strength,
        "before_graph_nodes": _join(graph.nodes),
        "before_graph_edges": json.dumps(
            [asdict(edge) for edge in graph.before_edges], sort_keys=True, separators=(",", ":")
        ),
        "after_graph_nodes": _join(graph.nodes),
        "after_graph_edges": json.dumps(
            [asdict(edge) for edge in graph.after_edges], sort_keys=True, separators=(",", ":")
        ),
        "raw_motif_before": review.raw_motif_before,
        "raw_motif_after": review.raw_motif_after,
        "canonical_motif_before": review.canonical_motif_before,
        "canonical_motif_after": review.canonical_motif_after,
        "support_connected_to_primary": review.support_connected_to_primary,
        "involves_old_neighbor": review.involves_old_neighbor,
        "involves_new_neighbor": review.involves_new_neighbor,
        "crosses_old_to_new_role": review.crosses_old_to_new_role,
        "preserved_nontrivial_edge_count": review.preserved_nontrivial_edge_count,
        "preserved_independent_source_count": review.preserved_independent_source_count,
        "transferred_edge_count": review.transferred_edge_count,
        "closed_path_count": review.closed_path_count,
        "role_mapping": review.role_mapping,
        "mapping_ambiguity_count": review.mapping_ambiguity_count,
        "old_neighbor_indegree": sum(
            edge.target == source.old_neighbor for edge in graph.before_edges
        ),
        "new_neighbor_indegree": sum(
            edge.target == source.new_neighbor for edge in graph.after_edges
        ),
        "enrichment_status": case.enrichment_status,
        "resource_status": review.resource_status,
        "evidence_complete": review.evidence_complete,
    }


def _motif_row(
    case: ReviewCase,
    reviewed: ReviewedSupport,
    evidence_kind: str,
    control_index: int,
) -> dict[str, Any]:
    review = reviewed.review
    return {
        "review_id": case.source.review_id,
        "evidence_origin": case.source.evidence_origin,
        "candidate_id": case.source.candidate_id,
        "primary_commit_locator": case.source.primary_commit_locator,
        "evidence_kind": evidence_kind,
        "control_index": control_index,
        "v3_classification": review.v3_classification,
        "support_strength": review.support_strength,
        "raw_motif_before": review.raw_motif_before,
        "raw_motif_after": review.raw_motif_after,
        "canonical_motif_before": review.canonical_motif_before,
        "canonical_motif_after": review.canonical_motif_after,
        "evidence_tags": _join(review.evidence_tags),
    }


def _report(
    cases: tuple[ReviewCase, ...],
    old_rows: list[dict[str, str]],
    verification: dict[str, Any],
    runtime_seconds: float,
) -> str:
    old_counts = Counter(row["v2_reclassification"] for row in old_rows)
    real = Counter(case.reviewed.review.v3_classification for case in cases)
    origin_counts = defaultdict(Counter)
    for case in cases:
        origin_counts[case.source.evidence_origin][case.reviewed.review.v3_classification] += 1
    controls = Counter(
        control.review.v3_classification
        for case in cases
        for control in case.controls
    )
    strong = {
        "SHARED_THIRD_MEMBER_ROLE_TRANSFER",
        "CLOSED_LOCAL_PATH_PRESERVED",
        "MULTI_SOURCE_CONVERGENCE_TRANSFER",
        "MULTI_TARGET_DIVERGENCE_TRANSFER",
        "SHARED_SOURCE_DUAL_ROLE_TRANSFER",
        "THREE_MEMBER_RELATIONAL_SCAFFOLD_PRESERVED",
        "CANONICAL_MOTIF_PRESERVED",
    }
    real_total = max(1, len(cases))
    control_total = max(1, sum(controls.values()))
    enrichment = {
        kind: {
            "real_count": real[kind],
            "control_count": controls[kind],
            "real_rate": real[kind] / real_total,
            "control_rate": controls[kind] / control_total,
            "enrichment_ratio": (
                (real[kind] / real_total) / (controls[kind] / control_total)
                if controls[kind]
                else "INF" if real[kind] else "N/A"
            ),
        }
        for kind in sorted(set(real) | set(controls))
    }
    v1_structured = sum(
        count
        for kind, count in origin_counts["V1_RECLASSIFIED_STRONG_RAW"].items()
        if kind in strong
    )
    v2_structured = sum(
        count
        for kind, count in origin_counts["V2_STRONG_RAW"].items()
        if kind in strong
    )
    enriched_cases = [
        case
        for case in cases
        if case.reviewed.review.support_strength
        >= V3_STRENGTH["STRUCTURED_RAW_SUPPORT"]
    ]
    v1_enriched = sum(
        case.source.evidence_origin == "V1_RECLASSIFIED_STRONG_RAW"
        for case in enriched_cases
    )
    v2_enriched = sum(
        case.source.evidence_origin == "V2_STRONG_RAW"
        for case in enriched_cases
    )
    participant_counts = Counter(
        raw_id
        for case in enriched_cases
        for raw_id in (
            case.source.source_raw_id,
            case.source.old_neighbor,
            case.source.new_neighbor,
        )
    )
    concentration = participant_counts.most_common(10)
    background_only = not enriched_cases
    indegree_summary = [
        {
            "review_id": case.source.review_id,
            "old_indegree": sum(
                edge.target == case.source.old_neighbor
                for edge in case.reviewed.graph.before_edges
            ),
            "new_indegree": sum(
                edge.target == case.source.new_neighbor
                for edge in case.reviewed.graph.after_edges
            ),
        }
        for case in enriched_cases
    ]
    ledger = {
        "TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION": old_counts[
            "TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION"
        ],
        "WEAK_SINGLE_INDEPENDENT_SUPPORT": old_counts["WEAK_NONTRIVIAL"],
        **{kind: real[kind] for kind in sorted(real)},
    }
    lines = [
        "# Support graph v3 review",
        "",
        "Locators are replay positions only, not object time, duration, or speed.",
        "",
        "## Compression funnel",
        "",
        f"- Old representatives: {len(old_rows)}",
        f"- After excluding sibling-only: {len(old_rows) - old_counts['TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION']}",
        f"- After excluding single-edge weak support: {old_counts['STRONG_RAW_SUPPORT']}",
        f"- After excluding scattered multi-source/background structure: {v1_structured}",
        f"- Old strong rows reviewed as relation-level structured support: {v1_structured}",
        f"- New v2 strong rows reviewed as relation-level structured support: {v2_structured}/60",
        f"- Old/new rows remaining after descriptive control downgrade: {v1_enriched}/{v2_enriched}",
        "",
        "## Classification counts",
        "",
        f"- Real: {dict(real)}",
        f"- Controls: {dict(controls)}",
        f"- Full funnel ledger: {ledger}",
        f"- By origin: {dict((key, dict(value)) for key, value in origin_counts.items())}",
        "",
        "## Questions",
        "",
        f"1. Original 101 strong-raw rows: structurally strong {v1_structured}; control-enriched {v1_enriched}.",
        f"2. New 60: structurally downgraded {60 - v2_structured}; all-evidence downgraded {60 - v2_enriched}; control-enriched {v2_enriched}.",
        f"3. Shared-third role transfer: {real['SHARED_THIRD_MEMBER_ROLE_TRANSFER']}.",
        f"4. Closed local path: {real['CLOSED_LOCAL_PATH_PRESERVED']}.",
        f"5. Convergence/divergence transfer: {real['MULTI_SOURCE_CONVERGENCE_TRANSFER']}/{real['MULTI_TARGET_DIVERGENCE_TRANSFER']}.",
        f"6. Three-member relational scaffold: {real['THREE_MEMBER_RELATIONAL_SCAFFOLD_PRESERVED']}.",
        f"7. Canonical motifs with >=3 nodes/3 edges: {real['CANONICAL_MOTIF_PRESERVED']}.",
        f"8. Most frequent primary-role raw IDs among enriched rows: {concentration}; indegrees: {indegree_summary}.",
        "9. Indegree/background explanation: "
        + (
            "the descriptive controls account for every structured hit."
            if background_only
            else "not every enriched structured hit is accounted for by the matched controls."
        ),
        "10. Minimum continuity evidence reached relation-motif preservation: "
        + str(bool(v1_structured or v2_structured))
        + "; enrichment remains a separate requirement.",
        "",
        "## Descriptive background controls",
        "",
        f"`{json.dumps(enrichment, sort_keys=True)}`",
        "",
        f"- Baseline final network hash match: {verification['baseline_final_network_hash_match']}",
        f"- Baseline final RNG match: {verification['baseline_final_rng_state_match']}",
        f"- Runtime seconds: {runtime_seconds:.2f}",
    ]
    return "\n".join(lines) + "\n"


def _derived_seed(seed: int, label: str) -> int:
    return int.from_bytes(sha256(f"{seed}:{label}".encode()).digest()[:8], "big")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if columns:
            writer.writeheader()
            writer.writerows(rows)


def _join(values: Iterable[object]) -> str:
    return "|".join(map(str, values))
