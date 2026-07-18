# Candidate relation definition audit

Audit date: 2026-07-12. Scope: M1 definitions, Git history, one N=100 short
diagnostic, and statistical naming. No dynamical rule or parameter was changed.

## Current M1 definition

The unique active-candidate entry is `get_direct_candidate_targets` in
`src/uboot/candidate_graph.py`. For a network state X and node i:

```text
IN(i)  = {j | one or more raw slots of j point to i}
OUT(i) = {target of each of i's three raw slots}
C(i)   = IN(i) - OUT(i) - {i}
```

The implementation has no `OUT2(i)` term. It has no M1 global fallback. The
`SelectionPolicy` still contains historical incoming/global parameters, but
current M1 `_active_update` does not consult them: it uniformly selects one
member of `direct_candidates(i)`, or performs a no-op when C(i) is empty.

The three concepts are therefore:

```text
persistent_candidate_relation(i, X) = C(i)
selection_pool(i, tick)              = C(i) for active updates
selected_target(i, tick)             = uniform random member of C(i), or none
```

“Persistent” is only a graph-layer name here. C(i) is not stored as durable
state: it is derived immediately from raw slots and the maintained incoming
index every time it is requested. Snapshot code recomputes all C(i). There is
no candidate cache.

Response micro-steps use the same direct function with one additional exclusion:
the incoming source of that response. A response changes raw slots and the
incoming index, so it changes later C(i) indirectly; it does not mutate a
separate candidate state.

This reveals a conceptual conflation introduced in commit `5e05a67`: an
instantaneous active-selection eligibility pool was promoted to the graph named
`persistent_candidate_graph`. The random selected target is not conflated with
the pool, but no separately persistent candidate relation currently exists.

## Historical definitions

### Relation-endogenous model (`77185fa` through `80bfcf5`)

Both `src/uboot/dynamics/endogenous.py` and the heavy snapshot graph used:

```text
IN(i)   = {j | j has any raw slot pointing to i}
OUT(i)  = targets in i's three raw slots
OUT2(i) = union of raw targets of every j in OUT(i)

old_candidate_relation(i) = (IN(i) union OUT2(i)) - {i}
selection_pool(i)          = old_candidate_relation(i)
```

No current OUT target was excluded merely because it was already in OUT(i), and
there was no global fallback. The target was uniformly selected from that pool.
Commit `80bfcf5` added heavy SCC measurement by copying exactly this same
definition; it did not invent a different graph. This is the implementation
that produced candidate sizes 1--2 and closed SCC sizes 2--3 in the recorded
long runs.

### Edge-response introduction (`f3d8983`, 2026-07-12 10:22 -04:00)

This commit created a separate configurable edge-response family. Its code and
`docs/edge_response_logic_audit.md` explicitly state that the prior rule was
any-slot `IN union OUT2`, while the new experiment introduced:

```text
excluded = OUT(i) union {i}
incoming_same  = same-meaning incoming nodes - excluded
incoming_other = other-meaning incoming nodes - excluded
global         = all nodes - excluded - all incoming

with probability 0.95: choose an incoming pool when available
otherwise/fallback:      choose the global pool
```

Thus `IN union OUT2 -> incoming/global` was an intentional new experimental
model, not an optimization accident. It was introduced to support semantic-slot
selection and target response/propagation. It did, however, stop matching the
older endogenous object experiment.

### Candidate/snapshot alignment (`5e05a67`, 2026-07-12 17:10 -04:00)

This commit fixed a real engineering mismatch but made another model choice:
M1 active selection and snapshots were both changed to
`IN(i) - OUT(i) - {i}`, with no global fallback. The stated goal was one shared
candidate definition. Choosing the incoming-only, OUT-excluding pool as that
definition was not forced by the engineering fix; it was a dynamical change.

| Property | Old endogenous relation | Current M1 relation |
|---|---|---|
| IN | any-slot incoming | any-slot incoming |
| OUT used | only to compute OUT2 | subtracted from candidates |
| OUT2 | included | absent |
| self | removed | removed |
| global fallback | none | none |
| storage | derived each update | derived each request |
| active selection | uniform from relation | uniform from relation |
| mutual endpoint eligible | normally yes through IN/OUT2 | always no because it is in OUT |

## Mutual-to-candidate diagnostic

Run configuration: N=100, seed=20260712, 100 sweeps, worker=1. Twenty example
rows were written to `mutual_candidate_examples.csv`; all 46 pairs were included
in the aggregate.

```text
mutual_pair_count                          46
mutual_pairs_forming_candidate_two_cycle   0
mutual_pairs_forming_one_way_candidate     0
mutual_pairs_candidate_disconnected       46
```

The direct mechanism is exact, not probabilistic. For every mutual pair A--B:

```text
B belongs to IN(A), but B also belongs to OUT(A), so B is removed from C(A)
A belongs to IN(B), but A also belongs to OUT(B), so A is removed from C(B)
```

Consequently every raw mutual pair is disconnected in the current candidate
graph. Mutual pair density can remain near 0.14 while candidate two-cycles are
mathematically impossible. Closed candidate pairs/triples therefore cannot be
seeded by mutual pairs under this definition.

## Statistical naming

Singleton SCCs are now separated as:

```text
candidate_isolated: size 1, no self-loop, candidate out-degree 0
closed_self_loop:   size 1 with a candidate self-loop
nontrivial object:  closed SCC of size at least 2
```

System snapshots use `candidate_isolated_count`, `closed_self_loop_count`,
`closed_pair_count`, `closed_triple_count`, and `closed_larger_count`. Empty
candidate nodes are no longer written to `closed_object_snapshots.csv` and are
therefore excluded from lineage input by default.

The audited final N=100 snapshot contains 82 candidate-isolated nodes, zero
self-loops, pairs, triples, or larger closed objects. The closed-object CSV is
empty, as intended.

## Graph-layer definitions

| Layer | Nodes | Edge definition | Persistent | Dynamics | SCC input | Global allowed | Symmetrize | Transitive expansion |
|---|---|---|---|---|---|---|---|---|
| raw_slot_graph | raw IDs | each semantic raw slot source->target | state persists until rewritten | yes | diagnostics only | initialization/M0 selection only | no | no |
| mutual_graph | raw IDs | reciprocal same-meaning raw slots, counted once | derived per state | no | no; undirected components only | no | symmetric by definition | no |
| persistent_candidate_graph | raw IDs | current C(i)=IN-OUT-self | not separately stored; state-derived | supplies M1 pools | yes | no | forbidden | forbidden |
| selection_pool | raw IDs | active C(i); response C(i) minus incoming source | one selection only | yes | no | no in current M1 | forbidden | forbidden |
| response_transition_graph | active tasks/local states | not materialized as a graph | tasks persist across ticks | yes | no | no | no | no |
| object_lineage_graph | closed objects at adjacent snapshots | thresholded member-ID overlap candidates | offline artifact | no | no | no | no forced symmetry | no |

## Minimum unimplemented rule change

The smallest change that would merely allow mutual candidate two-cycles is to
stop subtracting OUT(i) from IN(i). That alone does not restore the old model.

The minimum exact restoration of the old candidate structure is:

```text
C(i) := (IN(i) union OUT2(i)) - {i}
```

and to use this same C(i) for active selection and snapshot SCC analysis. This
would restore the old eligibility mechanism, not guarantee the old empirical
distribution under the newer response scheduler. No such change was implemented
in this audit.
