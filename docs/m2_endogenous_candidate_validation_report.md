# M2 endogenous candidate validation

Run date: 2026-07-12. No physical interpretation is assigned.

## Definitions

- Raw directed edge: one semantic slot source->target.
- Raw mutual pair: an unordered node pair with at least one raw slot in each
  direction, independent of slot meaning.
- Same-meaning mutual pair: a raw mutual pair whose two direction-specific
  meaning sets have nonempty intersection.
- Candidate directed edge: `j in C(i)` for the configured current candidate
  support relation.
- Candidate mutual pair: both `j in C(i)` and `i in C(j)`; raw mutual is not
  required.
- Closed candidate object: a directed candidate SCC of size at least two with
  no candidate edge leaving the SCC.
- Member replacement: adjacent closed objects of equal size differing by exactly
  one removed and one added raw member.

M2 uses the strict, nonrecursive rule:

```text
OUT2(i) = union of OUT(j) for j in OUT(i)
C(i) = (IN(i) union OUT2(i)) - {i}
```

Targets are deduplicated, OUT is not excluded, no global fallback exists, and a
response-specific pool alone removes its incoming source.

## Implementation

Candidate rules are parameterized rather than implemented in separate engines:

- `incoming_excluding_out`: historical M1 comparison;
- `endogenous_in_out2`: M2 and the default rule for profile M2.

Active selection, snapshots, and SCC analysis share
`get_current_candidate_targets`. The cross-tick scheduler, response
probabilities, semantic-slot generation, and queue behavior were not changed.

Modified or added modules include `candidate_graph.py`, `edge_response.py`,
`snapshot_lineage.py`, the experiment CLI, `m2_candidate_scans.py`, and focused
tests. Snapshots now separate all raw mutual, same-meaning mutual, fully matching
meaning sets, slot multiplicity, raw triangles, candidate edges/two-cycles, and
raw/candidate intersection. Object snapshots include candidate support edges,
raw mutual edges, candidate two-cycles, and both mutual layers. Lineage records
persist, member replacement, growth, shrink, split/merge candidates, birth,
death, and support-edge changes.

## Tests

Ruff passed and 45 tests passed. New tests verify strict two-step OUT2 without
third-step closure, mutual eligibility in M2, meaning-layer separation,
candidate two-cycles without raw mutual, triangle de-duplication, M2 stats
invariance, deterministic state replay, and growth/shrink lineage classes.

## N=100 comparison

Seed 20260712, worker=1, snapshot interval 10 sweeps:

| sweeps | rule | all mutual | same meaning | candidate edges | candidate two-cycles | raw+candidate | sizes 0/1/2/ge3 | closed pair/triple/larger | max SCC |
|---:|---|---:|---:|---:|---:|---:|---|---|---:|
| 100 | exclusion | 139 | 46 | 22 | 0 | 0 | 82/14/4/0 | 0/0/0 | 1 |
| 100 | M2 | 25 | 2 | 935 | 152 | 25 | 0/0/0/100 | 0/0/1 | 100 |
| 1000 | exclusion | 139 | 50 | 22 | 0 | 0 | 82/14/4/0 | 0/0/0 | 1 |
| 1000 | M2 | 18 | 1 | 915 | 126 | 18 | 0/0/0/100 | 0/0/1 | 100 |
| 10000 | exclusion | 139 | 51 | 22 | 0 | 0 | 82/14/4/0 | 0/0/0 | 1 |
| 10000 | M2 | 54 | 36 | 265 | 104 | 54 | 0/46/14/40 | 22/4/6 | 11 |

Restoring IN union OUT2 immediately makes every raw mutual pair a candidate
two-cycle: the intersection equals all raw mutual counts in every M2 final
snapshot. It initially creates a whole-network closed SCC rather than small
objects. By 10,000 sweeps that SCC fragmented and pair/triple objects reappeared.

The first observed closed pair was at tick 351,000; the first triple at tick
1,266,000. At snapshot resolution, 30 completed exact-member lifetimes averaged
274,600 ticks (maximum 1,503,000). The 32 active final objects averaged 1,247,250
ticks of observed age, with a maximum 2,649,000.

Lineage recorded 15,357 persists, 17 shrinks, 707 births, and 676 deaths. No
member replacement or growth was observed. Thus the classes are operational but
member replacement is not yet empirically present in this run.

## N=1000 validation

The N=100 final safety gate passed only after 10,000 sweeps, so a bounded N=1000,
1,000-sweep validation was run for workers 1 and 2.

| workers | all/same mutual | candidate edges/two-cycles | raw+candidate | closed pair/triple/larger | max SCC | mean response lifetime |
|---:|---|---|---:|---|---:|---:|
| 1 | 251/45 | 8943/1225 | 251 | 9/6/7 | 920 | 1.108 |
| 2 | 249/33 | 9135/1302 | 249 | 8/4/10 | 910 | 1.099 |

Worker capacity changed the seeded trajectory and object counts. Both runs
formed pair/triple objects, but both retained a giant closed candidate SCC of
910--920 nodes. This is a scale-dependent near-whole-network degeneration even
though `whole_network_object_present` is false. Candidate sizes did not grow to
O(N) per node (about 9 directed candidate edges/node), but the directed graph
remained globally strongly connected across most nodes.

Exact-member lifetime summaries at 30-sweep resolution:

- worker 1: 16 completed, mean 185,625 ticks, max 360,000;
- worker 2: 18 completed, mean 266,667 ticks, max 990,000.

Persist counts were 383 and 374. Worker 2 recorded three shrink events; neither
run recorded member replacement or growth.

## Meaning baseline

At N=100 M2 final state, 15 mutual pairs were single-slot on both directions and
2 were same-meaning (0.133). At N=1000 the ratios were 6/180 (worker 1) and
4/194 (worker 2). These are below the independent-uniform 1/3 reference. The
response and candidate dynamics do not preserve independent uniform meanings,
and multi-slot mutual pairs are numerous, so no 1/3 equality is claimed.

## Unresolved

- Pair/triple formation is preceded by, and at N=1000 coexists with, a giant
  closed SCC. M2 restores old local objects but does not robustly avoid global
  condensation at this runtime and scale.
- Object lifetime is snapshot-resolved and exact-member based; objects shorter
  than the interval are unobserved.
- Member replacement and growth are implemented but absent in these runs.
- No multi-seed 10,000-sweep scan was run because the N=1000 giant SCC indicates
  that the scale validation is not yet clean enough to justify the next stage.
- No parameter was tuned and no longer experiment was run.
