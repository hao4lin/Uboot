# Commit-centered relation trace v2 semantics

This observer is passive. It does not modify Baseline dynamics, RNG consumption,
checkpoint restoration, or the historical relation-transformation observer.

The analysis has two stages:

1. The all-commits impact index locates bounded primary relation changes for each
   coarse candidate. A 12-candidate run uses a balanced 3/3/3/3 sample and keeps
   at most five primary commits per candidate.
2. Exact states immediately before and after each commit generate a bounded
   bundle of pair, neighborhood, and dynamically selected three-member slices.
   Support-boundary diagnostics inspect only the configured progressive windows
   (default `8,32,128` atoms).

`distance_in_locator` is only a replay-search diagnostic. It is not object time,
duration, speed, age, or causal distance.

## Support boundary

An unchanged outgoing slot of the commit source is classified as
`TRIVIAL_SOURCE_SIBLING_SLOT_PRESERVATION` when it is the only evidence. It has
strength 1 and is excluded from nontrivial support.

Strength 2 requires at least one independent relation, such as an incoming edge
from a third source to the primary source. Strength 3 requires raw multi-source,
multi-edge, or role-transfer evidence. Strength 4 is reserved for a deterministic
three-role canonical mapping with at least two matching non-primary relations and
a replaced raw third member. Canonical and raw evidence remain separate fields.

The compatibility reclassifier only uses context saved by v1. Missing or
ambiguous context is emitted as `INSUFFICIENT_SAVED_CONTEXT`; it is never filled
in by inference from locator order.
