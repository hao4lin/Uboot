# Joint movement-touch diagnostics report

Run date: 2026-07-18.

## Scope and implementation

This layer preserves the existing joint continuation tracker, M2 dynamics,
snapshot definition, and closed pair/triple definition. `EngineUpdateEvent` is a
passive callback emitted for active attempts and completed retargets. The touch
observer copies the raw target slots, identifies exact direct-IN and OUT2 slot
witnesses for the currently tracked pair and triple, and never calls the RNG or
feeds a measurement back into target selection.

Every scanned post-start God slice records `jointly_present`, `pair_changed`, and
`triangle_changed` independently. All valid `00`, `10`, `01`, and `11` slices
remain in the joint chain. Only `10`, `01`, and `11` enter the derived movement
event file. A pair-only or triple-only match is diagnostic evidence but cannot
advance either current tracked state.

The diagnostic classes are `untouched`,
`touched_but_joint_state_unchanged`, `pair_moved_triangle_static`,
`pair_static_triangle_moved`, `both_moved`, `pair_only_continuation`,
`triangle_only_continuation`, the three changed-without-valid-successor classes,
the three ambiguity classes, and `structural_conflict`. Unknown historical touch
information remains explicitly `N/A`.

## Real replay results

| measurement | N=1000 | N=100 |
|---|---:|---:|
| tracked structure | `616|629` + `602|979|993` | `4|83` + `12|32|48` |
| post-start scanned slices | 1,000 | 578 |
| jointly valid slices | 1,000 | 578 |
| tracked-member active selections | 15,000 | 86,700 |
| pair support-slot touches | 6,000 | 34,680 |
| triangle support-slot touches | 10,169 | 58,789 |
| any structure-relevant touch events | 16,169 | 93,469 |
| touched, joint state unchanged | 1,000 | 578 |
| untouched slices | 0 | 0 |
| movement events | 0 | 0 |
| pair-only continuations in paired replay | 0 | 0 |
| triangle-only continuations in paired replay | 0 | 0 |
| changed without valid successor | 0 | 0 |
| ambiguity / structural conflict | 0 / 0 | 0 / 0 |

Every paired replay slice had at least one relevant touch. Consequently
`P(any_structure_touch per scanned slice) = 1`, while all observed conditional
movement and collapse probabilities are zero. These are slice-level empirical
ratios; raw touch-event totals are reported separately.

The N=1000 replay started from the original frozen 60,000-sweep checkpoint. Its
final dynamics hash
`b2bc06d9281bcfb5a28230c1a70877c2a33d528ee40bfdfcaceb1b77c087a088`
exactly equals the original phase-two run, establishing that passive observation
did not perturb that trajectory.

The historical N=100 artifact has no per-update log or checkpoint at snapshot
422. Its original descending snapshots show 208 pair-only continuations, but all
208 retain pair `4|83`; `pair_only_moved_count` is zero. They therefore show
one-sided structural presence, not a pair member migration suppressed by the
joint requirement. Touch counts for those old descending intervals remain
`N/A`. The new N=100 update counts come from a same-configuration, same-process
rerun whose snapshots and event observations were recorded together.

## Interpretation

The zero migration result is not explained by lack of exposure. Across both
paired runs, every scanned interval touched the monitored structure and every
tracked member was repeatedly selected. The supported conclusion is narrower:
under these runs and sampling intervals, the existing pair and triple closure
were robust to many relevant rewrites, so disturbances returned to the same
member sets at every observed snapshot.

There is no observed pair-only or triangle-only member migration hidden by the
joint co-occurrence filter, and no observed support change that ended without a
valid successor. Thus the data favor “touched but structurally stable” over
“never touched,” “single-side movement filtered out,” or “change failed to form
a successor.” They do not establish that migration is impossible between
snapshots or under longer/different runs.
