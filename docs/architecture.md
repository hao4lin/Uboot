# Architecture boundary

Uboot begins with one deliberately small cycle:

1. represent a task-local direction;
2. select only modules aligned with that direction;
3. recompose them into an explicit `FusionPlan`;
4. execute without mutating the input state;
5. validate named invariants against the result.

The first promoted universe model is the relation-endogenous raw network:

1. create indistinguishable raw objects represented by God-visible integer IDs;
2. give every object exactly three outgoing slots and initialize them randomly;
3. select an object and slot uniformly;
4. construct its candidate targets from current incoming neighbors and the
   targets reached through its current targets;
5. exclude self-reference, deduplicate candidates, and select uniformly;
6. rewrite the slot without locking it.

After initialization there is no global random target fallback. IDs and slot
indices are operational handles, not object-visible types. Mutual connection is
a passive observable and ignores slot-number alignment.

Time is reported in slot sweeps, `steps / (3N)`. The main density observable is
`2M / (3N)`, where `M` is the number of unordered mutually pointing object
pairs. Neither quantity changes the dynamics.

The initial sequential `FusionPlan.run` is a minimal executable boundary, not a
claim that the final simulator is fundamentally sequential. It may later be
replaced or supplemented by graph, fixed-point, concurrent, or cortical-style
execution while keeping module contracts stable.
