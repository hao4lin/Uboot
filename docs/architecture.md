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

Time is reported only in steps. One step independently samples one object and
one of its slots, then attempts at most one rewrite. The main density observable
is `2M / (3N)`, where `M` is the number of unordered mutually pointing object
pairs. Neither quantity changes the dynamics.

Ordinary statistics are full-network snapshots taken only at configured stage
boundaries. Components and simple cycles are computed only at heavier boundaries.
Creation/destruction turnover and connection lifetimes are deliberately omitted:
both would require statistics-only bookkeeping on every rewrite.

The separate edge-response experiment family uses semantic slot types, a
shuffled fair-slot ring, incoming/global candidate groups, and an optional FIFO
response chain. Any-slot mutual relations and same-slot consensus are distinct
derived statistics. Candidate generation, target selection, retargeting, and
response generation remain separate boundaries. See
`docs/edge_response_logic_audit.md` for the prior-model audit.

Edge-response production runs retain no continuous event list. Periodic snapshot
summaries are bounded by configuration, while response-chain state is held only
for the configured number of reusable workers and compressed into histograms on
completion. Global candidates are sampled lazily instead of materializing all N
nodes on every active opportunity.

The initial sequential `FusionPlan.run` is a minimal executable boundary, not a
claim that the final simulator is fundamentally sequential. It may later be
replaced or supplemented by graph, fixed-point, concurrent, or cortical-style
execution while keeping module contracts stable.
