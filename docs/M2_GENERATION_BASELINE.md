# Corrected M2 generation baseline contract

Provisional model: `M2_distinct_target_candidate_baseline`, version `2.0.0`.
Reserved tag: `m2-distinct-target-baseline-v2`. The tag must not be created until
new fragmentation evidence and the replacement checkpoint have been generated.

## Raw invariant

Every raw object has exactly three semantic slots and three pairwise-distinct,
non-self raw targets. This is a state invariant, not merely an initialization
condition. Constructors, active updates, response updates, snapshots, and
checkpoint loading must reject a duplicate target.

For an update of slot `s`, targets held by the other two slots are unavailable.
The old target of `s` remains eligible when it is still in the relation-derived
pool. Selecting it is a valid hold rather than a forced rewrite.

## Relation and executable pools

The object-level candidate support relation remains:

```text
IN(i)   = raw nodes having any slot targeting i
OUT(i)  = the three distinct raw targets of i
OUT2(i) = targets reached by exactly one slot from i and one from its target
C(i)    = (IN(i) union OUT2(i)) - {i}
```

The executable pool for slot `s` is:

```text
E(i,s) = C(i) - {target(i,k) for k != s}
```

A response uses the same pool and additionally excludes its incoming source.
There is no global fallback. Empty executable pools are no-ops for active
updates and absorption for responses. Candidate tuples are not sorted; filtering
preserves the underlying relation-pool order and then uniform selection is used.

## Scheduler and checkpoints

The bounded cross-tick scheduler is retained: existing workers advance in
worker-id order, waiting tasks are assigned FIFO, one shuffled-fair active slot
is attempted, and waiting tasks are assigned again. A response worker advances
at most once per tick.

Checkpoint state includes targets, incoming-index set layout, workers, queue,
tasks, fair-ring position, counters, tick, and RNG state. A checkpoint containing
duplicate targets is incompatible even if its metadata or hash is otherwise
valid. Version-1 checkpoints are provenance-only and cannot seed version 2.

## Invalidated evidence

The archived `m2-generation-baseline-v1` used a pool that allowed one node's
slots to converge on the same target. Its fragmentation, pair/triple partition,
phase-two, continuation, microtrace, and exposure results do not validate this
corrected model. All such empirical claims are pending regeneration.
