# Uboot

Uboot is an independent Python simulator for exploring how universe-level
objects and laws might bootstrap from minimal structures.

Its software lifecycle is independent from Iboot. Its operating principles are
informed by Iboot: select small weakly coupled modules by task direction,
recompose them before execution, preserve minimum sufficient invariants, and
retain backward validation paths.

## Status

The first implemented model explores a deliberately narrow transition: a random
three-slot raw-object network is initialized once, then every rewrite target is
derived only from current relations. No legacy source or data was migrated.

## Relation Transformation Tracing v3 — closed

This research slice is closed at the radius-one relation-motif review. The
closed result is an observer result, not a Baseline policy change. The following
systems remained unchanged throughout the v1--v3 tracing line:

- `baseline_random_replace` dynamics and its main RNG;
- the order-neutral relation-transformation observer;
- deterministic replay, checkpoints, and run fingerprints;
- v2 candidate extraction, primary-commit localization, and progressive
  8/32/128-atom support windows.

### Question and final answer

The tracing line asked whether an apparent neighbor replacement
`a -> b` to `a -> c` retains more than mechanically unchanged background edges.
The evidence threshold was tightened three times:

1. V1 localized real commits exactly, but its minimum of two common members plus
   one relation was mechanically weak because an update changes only one source
   slot.
2. V2 excluded unchanged source-sibling slots and separated weak, raw-strong,
   and canonical support. Its literal multi-source rule was still too weak:
   every one of 60 reviewed commits passed simply because several unrelated
   incoming edges remained unchanged.
3. V3 withdrew edge count and source count as strength criteria. It requires a
   radius-one relationship motif involving the old/new neighbor roles, then
   compares the real motif rate with up to ten deterministic same-state controls
   matched by slot semantic, old/new indegree, and local node count.

The final compression funnel is:

```text
1,581 old representatives
-> 934 after excluding source-sibling-only preservation
-> 101 after excluding single independent-edge support
-> 10 old rows with a relation-level motif
-> 1 old row remaining strength 3 after descriptive controls
```

For the 60 v2 rows, ten initially matched a relationship motif, but all 60 were
downgraded after background controls. Across the combined 161-row review:

- 131 were `STATIC_CONVERGENCE_BACKGROUND`;
- 10 were `BACKGROUND_MULTI_SOURCE_PRESERVATION`;
- 19 were `SHARED_THIRD_MEMBER_ROLE_TRANSFER`, but their real rate was lower
  than the matched-control rate, so all were `STRUCTURED_BUT_NOT_ENRICHED`;
- one was `MULTI_SOURCE_CONVERGENCE_TRANSFER` and remained strength 3 under the
  descriptive control rule;
- no real closed local path, divergence transfer, three-member relational
  scaffold, or canonical motif was found.

The one retained bounded candidate is:

```text
review_id              V1-087
candidate_id           C14
primary_commit_locator 1154805
source_raw_id          91
old_neighbor           2
new_neighbor           16
classification         MULTI_SOURCE_CONVERGENCE_TRANSFER
old/new indegree       6 / 8
```

Its rate was `1/161` in real cases and `1/1610` in controls, a descriptive ratio
of about 10. No significance test was performed. It is therefore a precise
follow-up candidate, not established motion, continuity, or grounds for changing
the model.

### Implementation map

- `src/uboot/relation_transform_observer.py`: order-neutral coarse relation
  slices and transformations.
- `src/uboot/relation_transform_diagnostic.py`: deterministic diagnostic sample.
- `src/uboot/deterministic_replay.py`: exact passive replay/checkpoint layer.
- `src/uboot/adaptive_relation_tracer.py`: pure local slices and v1 refinement.
- `src/uboot/commit_centered_relation_trace.py`: v2 primary-centered support
  bundles and bounded windows.
- `src/uboot/motion_reclassification.py`: conservative v1 compatibility review.
- `src/uboot/support_graph_review.py`: factual before/after support graphs,
  aligned role edges, motif signatures, v3 classifications, and controls.
- `src/uboot/support_graph_review_runner.py`: bounded 101+60 review orchestration,
  enrichment downgrade, stable outputs, and report generation.
- `experiments/baseline_relation_transformation_trace.py`: coarse observer CLI.
- `experiments/baseline_adaptive_relation_trace.py`: v1/v2 replay CLI.
- `experiments/review_support_graphs_v3.py`: closed v3 review CLI.

Detailed semantic boundaries are in:

- `docs/relation_transformation_observer_semantics.md`;
- `docs/adaptive_relation_trace_semantics.md`;
- `docs/commit_centered_relation_trace_v2_semantics.md`;
- `docs/support_graph_review_v3_semantics.md`.

### Retained local evidence

Generated artifacts are ignored by Git. A machine continuing this exact result
must retain these directories together:

```text
artifacts/baseline_relation_transform_N100_K3_final3
artifacts/baseline_relation_transform_N100_K4_20260718_183514
artifacts/baseline_adaptive_relation_trace_N100_final
artifacts/baseline_adaptive_relation_trace_N100_v2_final2
artifacts/baseline_adaptive_relation_trace_N100_v3_review_final
```

The first two preserve candidate-source provenance. V1 final and v2 final2 are
the bounded replay-review inputs. V3 review final is the canonical closed output.
Smoke runs, incomplete runs, superseded v2/v3 outputs, duplicate repeat outputs,
and temporary pytest directories were deleted at closeout.

The final v3 directory contains:

```text
support_graphs_v3.jsonl
support_motifs_v3.csv
motion_candidates_v3.csv
support_v3_report.md
run_fingerprint.json
checkpoint_manifest.json
checkpoints/
```

Two independent final-code runs produced byte-identical graph JSONL, motif CSV,
and motion CSV. Both final network and main RNG matched pure Baseline exactly.
The suite has 135 passing tests and Ruff passes.

### Reproduce the closed review

```powershell
.venv\Scripts\python.exe experiments\review_support_graphs_v3.py `
  --v1-dir artifacts\baseline_adaptive_relation_trace_N100_final `
  --v2-dir artifacts\baseline_adaptive_relation_trace_N100_v2_final2 `
  --N 100 `
  --seed 20260712 `
  --sweeps 10000 `
  --checkpoint-sweeps 0,100,300,1000,3000,10000 `
  --replay-missing-context `
  --max-local-radius 1 `
  --require-primary-role-participation `
  --output-dir artifacts\baseline_adaptive_relation_trace_N100_v3_review_new
```

Do not automatically increase radius, candidate count, primary-commit count, or
replay the old coarse intervals. If this line is explicitly reopened, begin with
the exact V1-087 graph and additional matched controls or seeds. Keep statistical
enrichment separate from motif existence and do not modify Baseline on the basis
of this single bounded candidate.

## Development

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python experiments\endogenous_bootstrap.py --N 1000 --max-steps 1000000 --seed 0
.venv\Scripts\python experiments\edge_response.py --profile M1 --N 100 --background-sweeps 1000 --seed 0
.venv\Scripts\python experiments\edge_response_batch.py --sizes 20 50 100 --seeds 0 1 2 3 4
.venv\Scripts\python experiments\m2_fragmentation_scaling_scan.py --help
.venv\Scripts\python experiments\m2_generation_run.py --help
.venv\Scripts\python experiments\edge_policy_compare.py --help
.venv\Scripts\python experiments\minimal_edge_rule_compare.py --help
.venv\Scripts\python experiments\baseline_relation_transformation_trace.py --help
.venv\Scripts\python experiments\sample_relation_transformations.py --help
.venv\Scripts\python experiments\baseline_adaptive_relation_trace.py --help
.venv\Scripts\python experiments\review_support_graphs_v3.py --help
```

Long edge-response runs report active-slot completion, processed responses, and
current FIFO length to standard error every 60 seconds, plus final completion.
Batch runs print a separator before every child experiment with its batch index,
profile, N, seed, background sweeps, and output directory.

Edge-response output uses bounded periodic snapshots rather than continuous event
logs. `--snapshot-interval-sweeps` controls snapshot frequency and
`--worker-count` controls concurrent reusable response tendrils (default one).

The corrected M2 line enforces three pairwise-distinct non-self targets after
every active and response update. A slot may retain its own old target, but may
not select either target occupied by the node's other slots. Archived version-1
M2 checkpoints are incompatible and must not be resumed.

See `docs/architecture.md`, `docs/iboot-contract.md`, and
`docs/legacy-inventory.md` before adding simulation behavior.
