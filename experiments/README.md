# Experiments

Keep reproducible experiment entry points here. Each experiment should declare
its configuration, seed where applicable, expected outputs, and artifact path.
Do not commit generated outputs.

`endogenous_bootstrap.py` uses one single-slot rewrite attempt as one step. Its
random initialization is the only exogenous target-selection phase; all later
rewrites use relation-derived candidates. During a long run it writes step
progress and the most recent stage statistics to standard error once per minute,
without an additional scan. Generated CSV, JSON, snapshot, and Markdown files
belong under the ignored `artifacts/` tree.

Heavy snapshots additionally measure candidate-set sizes and closed strongly
connected components of the candidate graph. These measurements are derived
entirely from the heavy snapshot and do not alter or instrument each rewrite.

The edge-response M1 experiment uses a bounded discrete-tick worker scheduler.
Each occupied worker advances at most once before one active-slot update; newly
created tasks cannot advance until the next tick. Its snapshot candidate graph
is the exact direct-candidate function used by M1 selection, not the older
relation-endogenous `IN + OUT2` graph. M0 bypasses this response system.

## Corrected M2 rebuild

M2 uses the object-level relation `C(i) = (IN(i) union OUT2(i)) - {i}`. For a
specific slot, the two targets occupied by the other slots are removed while the
slot's own old target remains eligible. This preserves three pairwise-distinct
targets without forcing every attempted update to change state. Active and
response execution share the filter; response execution also removes its
incoming source. There is no global fallback.

`m2_fragmentation_scaling_scan.py` is the first replacement-data entry point.
It fails immediately on a target-uniqueness violation and writes the violation
count and duplicate-slot ratio at each checkpoint. Version-1 artifact paths and
checkpoints belong to the archived invalid branch; use a new output directory.

`m2_generation_run.py` generates a fresh deterministic warm-up checkpoint after
the corrected scan identifies a suitable boundary. It reports progress to
standard error and rejects any duplicate target before saving. Checkpoint loading
also rejects version-1 metadata and duplicate-target payloads.

## Independent edge-policy comparison

`edge_policy_compare.py` leaves the corrected M2 baseline engine unchanged and
compares it with five slot-local edge retention policies. New policies use an
independent deterministic policy RNG and report strict reciprocal (Q),
high-strength (S), promoted-internal (L), and historical candidate-graph (C)
statistics separately. Output directories must be new; generated tables and
optional relation-event logs remain under ignored `artifacts/` paths.

The strength/promotion comparison above is now historical and receives no
further implementation. `minimal_edge_rule_compare.py` is the active independent
experiment: it compares only global random replacement, direct same-slot
returner-first selection, and an old/new reciprocal-status comparison. It has no
strength, reward, closure, promotion, identity, lock, or score path. R/Q graphs,
state transitions, relation ages, and component episodes are passive outputs.

`baseline_relation_transformation_trace.py` observes a small, independently
sampled anchor set in `baseline_random_replace`. Snapshot/atom indices are stored
only as source locators. Slice identities, undirected same/different
transformations, canonical equivalence groups, and chordless paths of two to four
edges use relation content only; missing exposure has no disappearance or chain
meaning. Increase anchor count only after checking output size and path-building
cost at a smaller K.

`sample_relation_transformations.py` joins the transformation table back to its
slice metadata and produces four reproducible diagnostic groups: balanced
anchor/type samples, neighbor edges of the ten highest-degree slices, edges
whose shared raw members exclude every tracked anchor, and the complete bounded
`MEMBERS_SAME_RELATION_DIFFERENT` group. Sampling never uses CSV row order.
