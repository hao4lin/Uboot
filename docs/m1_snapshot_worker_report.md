# M1 snapshot and worker validation report

Run date: 2026-07-12. This report describes structural and statistical results
only; it assigns no physical interpretation.

## Implementation

Added bounded system/object snapshots, closed-candidate-SCC object rows with
separate incoming/outgoing tentacles, fixed response histograms, an offline
adjacent-snapshot lineage analyzer, and worker/snapshot scan entry points.
Continuous response-event output remains disabled. Statistics can be selected
with `--enable-snapshots`, `--enable-worker-stats`,
`--enable-closed-object-snapshots`, and `--enable-response-histograms`.

New CSV outputs are `system_snapshots.csv`, `closed_object_snapshots.csv`,
`response_by_tentacle_summary.csv`, `object_lineages.csv`,
`object_transition_matrix.csv`, and `snapshot_matching_summary.csv`.

## Verification

Ruff passed. Pytest passed 29 tests in 0.18 seconds. A fixed-seed N=20
integration run produced readable snapshot/object CSV files and the lineage
analyzer completed successfully.

For N=100 and 1,000 sweeps, the stats-off run took 5.642 s and the enabled run
took 6.162 s (1.092x, about 9.2% overhead). These are single local timings, not
a benchmark distribution.

## Worker-count scan

N=100, seed=20260712, 100 sweeps:

| workers | mutual density | responses completed | mean chain | runtime s |
|---:|---:|---:|---:|---:|
| 0 | 0.6667 | 0 | 0 | 0.218 |
| 1 | 0.6000 | 7458 | 2.2757 | 0.562 |
| 2 | 0.6000 | 7458 | 2.2757 | 0.594 |
| 4 | 0.6000 | 7458 | 2.2757 | 0.594 |
| 8 | 0.6000 | 7458 | 2.2757 | 0.641 |

Worker count zero differs from M0: it retains M1 selection but processes no
responses. Counts 1--8 gave identical structural/result statistics under the
current response-priority scheduler. Thus worker capacity above one is not
exercised by this scheduler; it affects runtime slightly but not the observed
result in this scan. Zero versus nonzero workers did change mutual density.

## Snapshot-interval scan

N=100, seed=20260712, 100 sweeps:

| interval | snapshots | mean objects | primary match rate | birth/death/ambiguity rates |
|---:|---:|---:|---:|---:|
| 10 | 11 | 1 | 1.0 | 0 |
| 30 | 5 | 1 | 1.0 | 0 |
| 100 | 2 | 1 | 1.0 | 0 |
| 300 | 2 | 1 | 1.0 | 0 |
| 1000 | 2 | 1 | 1.0 | 0 |

At every snapshot the candidate graph formed one closed `other` SCC containing
all 100 nodes. Consequently interval changes produced no lineage ambiguity in
this short run and cannot validate pair/triple lifetime recovery. Longer or
structurally different runs are required before interpreting interval trends.

## Known limits

- `escaped` has no strict current termination definition and is unavailable.
- Initial response-to-object tentacle attribution is unavailable without adding
  stale snapshot context or a hot-path SCC calculation; rows therefore use the
  explicit `unavailable` bucket rather than guessing.
- Response lifetime is zero under response-priority because no active sweep
  advances while a response chain is drained.
- Worker timeout and debug path retention were not added: neither can be added
  as statistics-only behavior without defining scheduler semantics or retaining
  paths.
- The current scan is a functional N=100 validation, not the requested later
  N=1000 core comparison.

## Reproduction

```powershell
.\.venv\Scripts\python.exe experiments\edge_response_scans.py --N 100 --seed 20260712 --background-sweeps 100 --output-dir artifacts\m1_snapshot_worker_validation
```
