# Deterministic adaptive relation trace semantics

The random-replacement Baseline is treated as an exactly reproducible recording,
not as object time. An atom commit locator means the network state after that
many one-based Baseline commits. Snapshot and checkpoint locators are alternate
positions on the same recording. Locator magnitude never enters slice identity,
motion classification, relation age, direction, birth, death, speed, or causal
meaning.

Replay checkpoints contain the complete future-determining Baseline state:
network targets, the full main RNG state, commit/sweep position, and the empty
persistent policy state of `baseline_random_replace`. Passive statistics are not
future-determining state and are intentionally absent. Every checkpoint carries
the same run fingerprint and independent network, RNG, and complete-state hashes.

Local slices are pure functions of a network and an explicit query. Their stable
serialization uses explicit member, source, and slot ordering; it contains no
locator, observation history, RNG, or cache state. The impact index stores only
commit locator, source, slot, old target, and new target. It is a conservative
search index rather than evidence of motion.

Adaptive refinement does not assume a monotone predicate and is not ordinary
binary search. It checks both endpoints, consults relevant updates, and inspects
the state immediately before and after every update that can change the queried
slice within the configured resource limits. Equal endpoints therefore do not
close an interval when relevant updates exist. A resource limit produces
`INCOMPLETE_RESOURCE_LIMIT`, never a negative structural conclusion.

A motion candidate requires content evidence at one localized commit. Retaining
one raw member alone is `ONE_MEMBER_ONLY`. The first positive local-support class
requires two preserved raw members and at least one identical directed internal
slot relation between them. No intermediate state is invented and no global
transformation path graph is generated.
