# M1 scheduler and snapshot validation

Run date: 2026-07-12. This report contains structural and statistical results
only and assigns no physical interpretation.

## Implementation changes

The whole-network closed object was caused by a graph mismatch. M1 active
selection used incoming/global candidate groups, while snapshot object analysis
used the older relation-endogenous `IN union OUT2` graph from `reporting.py`.
The SCC implementation did not symmetrize the graph, but it was analyzing a
different, much denser graph than the dynamics.

M1 now has one direct candidate function. It returns current incoming relation
sources after removing self and the node's three current targets. Active M1
selection, snapshots, and closed-object analysis all use this definition. M0
retains its separate incoming/global selection and completely bypasses response
creation, workers, queues, and closure.

Closed objects are strict SCCs of the direct candidate graph. An SCC is closed
only when every original direct candidate target of every member remains inside
the SCC. Classes are `singleton`, `pair`, `triple`, and `larger`; raw-slot edges
and tentacles never enter candidate SCC construction.

M1 uses a fixed discrete tick order:

1. each occupied worker advances at most one response micro-step;
2. completed tasks release workers;
3. pending tasks fill idle workers;
4. one ordinary active-slot update runs;
5. a newly created task is assigned or queued but cannot advance until the next tick.

The pending queue is bounded by `--response-queue-capacity`; its policy is
`reject` (default) or `drop_oldest`. Rejections are counted explicitly. Tasks
store fixed state only; no complete paths or response-event rows are retained.

Response lifetime is the integer `completion_tick - start_tick`. A task created
at tick t cannot finish before tick t+1. Current strict termination classes are
`completed`, `absorbed`, `max_hops`, `invalidated`, and `queue_rejected`.
`escaped` is not emitted.

Density formulas are:

- `mutual_pair_density = mutual_pair_count / (3N)`;
- `mutual_endpoint_count = 2 * mutual_pair_count`;
- `mutual_endpoint_density = mutual_endpoint_count / (3N)`.

A mutual pair requires reciprocal targets in the same slot meaning and is
counted once. Runtime assertions enforce both factor-of-two identities and the
endpoint-density range.

## Modified files

- `src/uboot/candidate_graph.py`
- `src/uboot/edge_response.py`
- `src/uboot/snapshot_lineage.py`
- `experiments/edge_response.py`
- `experiments/analyze_snapshot_lineage.py`
- `experiments/edge_response_scans.py`
- `tests/test_edge_response.py`
- `tests/test_candidate_objects.py`
- `tests/test_snapshot_lineage.py`

## Tests

Ruff passed. Pytest passed 36 tests. New regression coverage includes stats
invariance, M0 bypass, cross-tick lifetime, worker concurrency, bounded queue
rejection, candidate/raw-graph isolation, strict closure, density formulas,
final snapshot uniqueness, deterministic replay, and thresholded lineage cases.

## Stats invariance

At both sizes, seed 20260712, 100 sweeps, worker=1, stats-off and stats-on
produced identical final state hashes:

| N | final state hash | pair density | responses |
|---:|---|---:|---:|
| 100 | `d3d2b5ae4ea8313a506e9d0da7e02f412d7e9bc0e0c9e7c7ba6575bcf4078faf` | 0.153333 | 1829 |
| 1000 | `1c58a420d40fd16babf5469cf4f2f3f889d252ac6c19f87858fe35189c17100c` | 0.139333 | 15719 |

The hash covers targets, active workers, pending queue, live task fields, tick,
and RNG state.

## Worker utilization

N=1000 short scan:

| workers | max concurrent | max queue | completed | rejected | mean chain | mean lifetime ticks | p50/p90/p99 | pair density |
|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 1 | 1 | 3 | 15719 | 0 | 1.04752 | 1.02182 | 1/1/2 | 0.139333 |
| 2 | 2 | 0 | 15388 | 0 | 1.05225 | 1.01768 | 1/1/2 | 0.142667 |
| 4 | 2 | 0 | 15388 | 0 | 1.05225 | 1.01768 | 1/1/2 | 0.142667 |
| 8 | 2 | 0 | 15388 | 0 | 1.05225 | 1.01768 | 1/1/2 | 0.142667 |

Worker capacity is active: two simultaneous tasks occurred, and worker=1
differs from worker>=2. This workload never required more than two simultaneous
workers, so capacities 2, 4, and 8 remained equivalent.

## Object validation

| N/tick | candidate sizes 0/1/2/ge3 | candidate edges | SCC count | closed singleton/pair/triple/larger | max SCC | max closed SCC | whole network closed |
|---|---|---:|---:|---|---:|---:|---|
| 100/0 | 7/10/24/59 | 296 | 10 | 7/0/0/0 | 91 | 1 | no |
| 100/30000 | 82/14/4/0 | 22 | 100 | 82/0/0/0 | 1 | 1 | no |
| 1000/0 | 38/151/256/555 | 2986 | 44 | 38/0/0/0 | 957 | 1 | no |
| 1000/300000 | 851/111/28/10 | 198 | 1000 | 851/0/0/0 | 1 | 1 | no |

The whole-network closed SCC has disappeared. Large initial SCCs exist but are
not closed because their original candidate targets include outgoing edges.
After 100 sweeps only closed singletons remain; no closed pair or triple was
observed. This is an actual result, not a reason to alter parameters.

## Snapshot interval limits

For intervals 10, 30, 100, 300, and 1000 sweeps, both short scans report
`no_closed_pair_or_triple`. Lineage quality metrics were therefore not computed.
No whole-network 1.0 match is presented as validation. The run is shorter than
the largest intervals, so those cases are functional checks only.

## Performance

Three-process median smoke timings for 100 sweeps:

| N | stats off median | stats on median | ratio |
|---:|---:|---:|---:|
| 100 | 1.673 s | 1.676 s | 1.002x |
| 1000 | 2.472 s | 2.574 s | 1.041x |

The N=100 off timings contained a cold-run outlier, so these remain smoke
measurements. Histogram aggregation is constant work at completion. Enabled
overhead comes mainly from snapshot candidate materialization, SCC analysis,
object/tentacle scans, and CSV serialization. Lineage is offline and excluded.

## Known unresolved semantics

- No strict `escaped` state exists.
- No closed pair/triple appeared, so their lineage and lifetime are unvalidated.
- `absorbed` combines “no executable response slot” and “no direct response
  target”; these could be separated later without changing scheduling.
- Interval quality needs a longer run only after non-singleton objects exist.
