# Support graph review v3 semantics

V3 is a passive, radius-one review of already selected v1/v2 commits. It does
not alter Baseline dynamics, the coarse observer, deterministic replay,
checkpoints, fingerprints, candidate selection, primary localization, or the
8/32/128-atom window policy.

Each review stores factual before/after edges separately from role alignment.
For example, aligning `b -> d` in the old-neighbor view with `c -> d` in the
new-neighbor view does not assert that both support edges changed at the primary
commit. It states only that `b` and `c` occupy corresponding local roles.

Raw edge counts and independent-source counts cannot produce strong support.
Strong raw support is restricted to shared-third transfer, closed local paths,
whole convergence/divergence transfer, shared-source dual roles, and coherent
three-member scaffolds. Canonical support requires at least three role nodes,
three aligned relations, two non-primary/non-sibling relations, and at most
eight explicitly recorded mappings. Unchanged source sibling slots are always
excluded from canonical evidence.

Every real case receives up to ten deterministic same-state controls selected
with observer seed 20260718. Controls preserve slot semantic and approximate
old/new indegree while using unrelated source/neighbor combinations. They never
consume the Baseline RNG. A structural class whose real rate does not exceed its
control rate is retained as a structural diagnosis but downgraded to strength 2
with `STRUCTURED_BUT_NOT_ENRICHED`.

Locator values remain replay positions only. They are not object time, duration,
speed, or causal distance. Radius one is a hard boundary; v3 never expands to
radius two automatically.
