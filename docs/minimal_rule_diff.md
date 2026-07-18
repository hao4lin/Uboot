# Minimal reciprocal edge-rule differences

This experiment is independent of the corrected M2 engine. No existing entry
point exactly matched the requested baseline: corrected M2 uses the endogenous
`IN union OUT2` pool, while M0 excludes the slot's old target. The experiment
therefore freezes the explicit requested baseline below and validates its
wrapper against a separate direct reference loop.

All three rules use the same random initialization, random active node, random
active slot, three distinct non-self targets, global legal target set, sweep
definition, snapshots, and passive statistics.

## Baseline: `baseline_random_replace`

Randomly select one global legal target, including the slot's old target, and
commit it directly.

## Policy B: `direct_reciprocal_candidate_first`

The only difference is the proposal pool. If any legal node currently points
to the active node through the same semantic slot, choose uniformly from those
direct returners. Otherwise choose uniformly from the baseline global pool.

## Policy C: `old_new_reciprocal_compare`

Generate one proposal from the unchanged baseline global pool. Compare only
whether the old and proposed targets point back through the same slot. When
exactly one is reciprocal, select it; when both have the same status, choose
uniformly between old and proposed. A proposal equal to old is a natural hold.

## Confirmed absence of additional behavior

The new runner has no relation strength, reward, penalty, closure query,
promotion, demotion, internal/external identity, lock, lifetime resistance,
score, component feedback, or historical selection state. Relation ages,
reciprocity transitions, R/Q graphs, and structure episodes are observations
only and never enter target selection.
Transition counts cover the selected directed slot and any old/new same-slot
partner whose reciprocal status changes as a consequence of that commit.

Policy D is not implemented. The new experiment is intentionally a single
atomic active-update process, so introducing a distinct response phase solely
for D would violate the requested minimal boundary.
