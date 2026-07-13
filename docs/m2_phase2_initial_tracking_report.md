# M2 phase-2 initial tracking report

All runs load the phase2-ready 60,000-sweep checkpoint for `N=1000`, seed
20260712, baseline commit `d6f859b`, tag `m2-generation-baseline-v1`, full
checkpoint hash `719fef73ecfcd5a33eace1544f936eeac080144457fe5b987a72e5b87c26570f`.
Each branch continues the unchanged baseline dynamics for 1,000 sweeps from the
same RNG state.

| interval (sweeps) | internal rewire observations | persist observations | runtime (s) |
|---:|---:|---:|---:|
| 1 | 92,606 | 352,394 | 166.953 |
| 5 | 18,734 | 70,266 | 44.203 |
| 10 | 9,411 | 35,089 | 28.578 |
| 30 | 3,185 | 11,945 | 17.797 |
| 100 | 933 | 3,517 | 14.547 |

Every branch ended at dynamics hash
`b2bc06d9281bcfb5a28230c1a70877c2a33d528ee40bfdfcaceb1b77c087a088`.
Thus snapshot statistics are dynamically invariant. The closed pair/triple
member partition remained stable: no temporary open/reclose, member replacement,
birth, death, split, or merge was observed. Internal support rewiring was common.

Relative to interval 1, the raw observation count retained is approximately
20.23% at interval 5, 10.16% at interval 10, 3.44% at interval 30, and 1.01% at
interval 100. For event-resolved work the minimum acceptable interval is one
sweep; five sweeps may be used only for coarse rate estimation. False
death+birth pairs were zero in this window. Runtime is reported separately and
is not a state-consistency field.

These five branches are mutually invariant because they load the same historical
checkpoint. A later direct 0-to-61k audit did not match their final hash; the
cause was incoming-set iteration layout reconstruction. The serializer was
repaired to persist that layout, but this N=1000 checkpoint and these runs were
not regenerated at the user's direction. They remain valid for comparing
snapshot intervals from the same loaded state, not for claiming strict equality
to a fresh 0-to-61k process.
