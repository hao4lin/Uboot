# Relation transformation observer semantics

The prior `baseline_slice_chain_trace` interpretation is withdrawn: atomic
update indices, snapshot indices, and observation occurrence order are not
object time and cannot define identity, predecessor/successor, motion direction,
disappearance, return, or a temporal chain. No file with that prior name exists
in the current or archived Git branches, so this is a semantic retraction rather
than a claim that a historical source file was deleted.

The replacement observer treats each snapshot as one relation exposure. A slice
identity contains only relation kind, exact slot semantics, raw member IDs, and
member roles. Observation locators are retained solely for source lookup and
deduplication.

Transformation edges are undirected and are derived only from slice content.
All slice pairs sharing a raw member are compared regardless of their locator.
Raw-disjoint canonical isomorphism is aggregated in equivalence groups rather
than expanded into global pairwise edges. Transformation paths are undirected,
simple, chordless paths of two through four edges; reverse serialization is the
same path. The chordless representation removes redundant detours when two
slices already have a direct transformation edge and does not filter slices by
frequency.

For the overlapping categories, the direct-edge label uses the most specific
member-preservation rule: T3 for the same raw member set, then T1/T2/T5 for a
one-member overlap. T4 is also counted as the secondary
`relation_same_members_different_count` dimension per anchor, so a pair may
contribute to both a T1/T2 edge family and the T4 statistic. Raw-disjoint T6 is
reported only by the canonical equivalence-group aggregate.

The observer reads the baseline at configured snapshot boundaries. It does not
alter baseline candidates, choices, commits, raw identities, or RNG. Missing
exposure at a boundary has no object-level meaning. For a fixed observer seed,
larger anchor counts extend one seeded random permutation, so K=2, K=3, and
later samples are nested rather than unrelated redraws.
