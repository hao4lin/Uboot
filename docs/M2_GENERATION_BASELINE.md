# M2 generation baseline (immutable)

Baseline model: `M2_endogenous_candidate_baseline`, version `1.0.0`, tag
`m2-generation-baseline-v1`.

## Model definition

A **raw object** is an integer-labelled simulation handle with exactly three
directed raw slots. Slot meanings 1, 2, and 3 are the fixed semantic identities
of indices 0, 1, and 2; an update changes a target, never a meaning. `OUT(i)` is
the set of raw targets in the three slots of `i`. `IN(i)` is the set of sources
having any raw slot targeting `i`. `OUT2(i)` is the set of targets reached by
following exactly two raw slots: one slot from `i`, then one raw slot from that
first target. It is raw-slot based, non-recursive, and is not a transitive
closure. The candidate relation is
`C(i) = (IN(i) union OUT2(i)) - {i}`.

A **raw mutual pair** is an unordered pair whose endpoints each have at least
one raw slot targeting the other. A **same-meaning mutual pair** has a raw edge
in each direction in at least one identical slot meaning. A **candidate
two-cycle** is an unordered pair with each endpoint in the other's candidate
set. A **closed pair** or **closed triple** is respectively a size-two or
size-three strongly connected component of the candidate graph with no outgoing
candidate edge. A baseline object is any closed candidate SCC of size at least
two.

A **response worker** owns at most one cross-tick response task and advances it
at most once at the beginning of a tick. Existing workers advance in worker-id
order, waiting tasks are assigned FIFO, one shuffled-fair active slot is then
updated, and waiting tasks are assigned again. A response-specific candidate
pool may exclude the incoming source without altering the base candidate
relation. The bounded queue is FIFO with configured capacity; `reject` rejects
the new task when full, while `drop_oldest` terminates the oldest queued task and
admits the new task.

## Confirmed result

For `N=1000` over multiple seeds, initialization contains a giant candidate SCC.
It is fragmented by 30,000 sweeps, fully partitions all nodes into closed pairs
and triples by 60,000 sweeps, and pair/triple counts remain stable through
100,000 sweeps. This is an empirical baseline statement, not a physical
interpretation.

## Locked semantics

The candidate rule, exact `OUT2` definition, slot update semantics, response
scheduler order, worker lifetime definition, queue semantics, RNG call order,
and checkpoint serialization semantics are immutable baseline behavior. The
serialized deterministic state includes raw targets, the incoming-index set
layout that preserves candidate iteration order, fixed slot meanings,
worker tasks, queue and task ids, fair scheduler position, dynamic counters, tick
and sweep position, and RNG state. Incoming indexes and non-dynamic caches may be
rebuilt and verified after loading.

## Future changes

Any experiment changing a locked semantic must use a new candidate-rule name,
a new model version, and an independent experiment runner. It must not overwrite
baseline defaults, and its report must identify the baseline tag from which it
forked. Stage-two tracking loads this baseline checkpoint and observes the same
dynamics; it does not modify this module's semantics.
