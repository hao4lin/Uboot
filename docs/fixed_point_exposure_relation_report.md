# Fixed-point exposure relation trace

## Question and boundary

This experiment asks whether a closed pair or closed triangle can remain an
unchanged M2 fixed point while its outward raw-slot implementation, neighboring
fixed-point objects, or two-hop object paths change between adjacent snapshots.
It is analysis-only: the dynamics, RNG use, candidate ordering, snapshot writer,
and existing static trackers are unchanged.

At the start boundary every closed pair and triangle receives a stable ID based
only on canonical membership (`P...` or `T...`). The complete start index is
preserved, while dynamic event monitoring is limited to the selected roots and
their start-time radius-two object neighborhood.

## Exact definitions

- An internal support slot is an exact directed raw-slot witness already used by
  the current M2 candidate-closure support calculation.
- An exposed slot originates at a member of the fixed-point object, is not an
  internal support slot, and targets outside that same object.
- A direct object link retains source object, source member, raw slot, raw target,
  and target object. Its Level-2 aggregate is source/target object multiplicity.
- A two-hop path retains both raw implementations, including the middle entry
  member and middle exit member/slot; return paths are marked explicitly.

The definition deliberately uses all positive internal support witnesses. It
does not choose a minimal support basis after seeing the result.

## Per-update validation

After every completed `step_tick()` the tracer:

1. verifies that the complete pair/triangle membership partition is identical to
   the start boundary;
2. updates only exposure states affected by committed retargets and changed
   candidate nodes;
3. optionally reconstructs the entire exposure state and checks exact equality;
4. classifies raw retargeting, source-slot reassignment, neighbor create/break,
   multiplicity changes, detailed two-hop changes, gap-transfer candidates, and
   consecutive external rewiring chains;
5. extends a run-length encoded Level-1/2/3 state history.

An internal fixed-point change is a hard invariant violation, not an ordinary
exposure event. The independent direct replay checks dynamics hash, RNG state,
counters, targets, response aggregates, phase readiness, and complete static
signatures before any output is accepted.

## Reference results

| Interval | Atomic updates | Fixed objects | Internal support slots | Exposed slots | L1/L2/L3 changes | Full rebuild checks |
|---|---:|---:|---:|---:|---:|---:|
| N=100, 422 -> 423 | 3,000 | 11 pairs + 1 triangle | 75 | 0 | 0 / 0 / 0 | 3,000 |
| N=1000, 0 -> 1 | 3,000 | 335 pairs + 110 triangles | 3,000 | 0 | 0 / 0 / 0 | 3,000 |

Both complete object partitions matched their saved start and end snapshots.
Both traced runs matched their independent direct replay, including RNG state
and final dynamics hash. No fixed-point internal membership changed.

The result is structural rather than a positive observation of frozen outward
relations. In these two start states, every member slot is already used by at
least one internal closure-support witness. The requested strict subtraction
therefore leaves an empty exposed-slot set; direct and two-hop exposure graphs
cannot exist, much less rewire. Conditional rates with zero denominators are
correctly reported as `N/A`.

This rules out interpreting the earlier absence of member migration as evidence
that a nonempty outward relation layer was observed and found static. Testing a
different notion such as a minimal indispensable support basis would be a new
experiment and requires an explicit new definition; it is not silently folded
into this one.

## Outputs

Each result directory contains the complete fixed-object index; start/end
exposed slots, direct links, and two-hop paths; changed-event detail; RLE state
runs; configuration; summary in JSON and Markdown; and replay/snapshot/full-
recompute verification. Empty relation/event CSVs retain headers so that later
automation can distinguish an empty measured set from a missing file.
