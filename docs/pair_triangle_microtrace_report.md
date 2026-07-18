# Pair-triangle two-snapshot microtrace report

Run date: 2026-07-18.

## Boundary and method

The microtracer analyzes one S0-to-S1 interval and does not extend the existing
snapshot continuation chain. One atomic update is the frozen engine's existing
`step_tick()`: worker response work, one active-slot attempt, queue handling, and
the final tick increment complete before the outer wrapper classifies state.
Committed response and active retargets inside that tick are preserved as
directed, slot-bearing event details.

No frozen-engine function signature or control path changed. The wrapper invokes
`step_tick()` directly. The already-existing optional `EngineUpdateEvent`
observer mirrors committed raw slots and identifies exact pair/triple support
touches. It never calls the RNG, returns a decision, or writes into the engine.

The initial candidate graph is produced by the existing engine. After a
retarget, only candidate sets that can mathematically change under the exact M2
`IN union OUT2 - self` rule are recomputed: the source, old/new targets, and raw
sources pointing to the changed source. Existing `candidate_objects` then
performs the full closed-SCC recognition after every tick. The cache is checked
against a complete final candidate-graph rebuild and the run fails on mismatch.

Both the immutable initial pair/triple and the conservative current
continuations are checked. A unique continuation may advance its own side;
zero/multiple candidates retain the last confirmed side and record broken or
ambiguous state. Ambiguity never stops the interval. State runs use run-length
encoding, while the event file defaults to structure-touch or recognition-change
rows only.

## Automatically selected intervals

The first adjacent interval with unchanged endpoint members, positive prior
touch count, and matching saved endpoint steps was selected for each dataset.

| measurement | N=100 | N=1000 |
|---|---:|---:|
| snapshots | 422 to 423 | 0 to 1 |
| steps | 1,266,000 to 1,269,000 | 180,000,000 to 180,003,000 |
| atomic updates | 3,000 | 3,000 |
| structure-touch updates | 165 | 16 |
| pair-support-touch updates | 60 | 6 |
| triangle-support-touch updates | 105 | 10 |
| exact-static updates | 3,000 | 3,000 |
| initial pair ever absent | no | no |
| initial triangle ever absent | no | no |
| unique movement observed | no | no |
| ambiguity observed | no | no |
| break and restore | no | no |
| movement and return | no | no |
| state runs | 1 | 1 |
| interval classification | `atomically_static` | `atomically_static` |

The N=100 event-detail count exactly recovers the first interval of the prior
touch replay: 60 pair-support plus 105 triangle-support touched ticks. The
N=1000 interval similarly recovers 6 plus 10.

## Deterministic verification

Each start state was independently prepared twice. One engine ran directly to
S1; the other ran through the microtrace wrapper. Both real intervals matched on:

- start and final dynamics hashes;
- final step and full RNG state;
- counters, active-attempt arrays, and response aggregates;
- every raw target slot;
- all final closed-pair/triple static signatures and phase-two readiness;
- saved S0/S1 ticks and selected object memberships.

For N=100, the shared final hash was
`8430d850c8c0268ecd4b085709c13ddce6619cb237abf0f22974c6b2418bf3f2`.
For N=1000, it was
`fe6fbde6296b63a50265599cc62a4f98f1be77999b72a4aad242211f1c23fb20`,
which is also the previously recorded 60,001-sweep incremental validation hash.

## Answer to the micro-level question

In these two selected adjacent intervals, snapshot-level stability was literal
atomic-level stability of the recognized member sets. Relevant slots were
rewritten, including directed support slots, but after every one of the 6,000
combined atomic updates the same real closed pair and real closed triangle were
recognized uniquely. There is no evidence in these intervals of a hidden
short-lived migration, break, ambiguity, restoration, or return.

This conclusion applies only to the two inspected intervals. It does not claim
that all 1,000 recorded intervals are atomically static and does not infer a
physical time, speed, trajectory, or impossibility of migration.
