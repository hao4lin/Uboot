# M2.1 candidate fragmentation scaling report

Run date: 2026-07-12. Seed 20260712, worker=1, unchanged M2 dynamics and
`endogenous_in_out2` candidate rule. All four sizes completed 30,000 sweeps.
No physical interpretation is assigned.

## Implementation and tests

Added checkpoint-only `fragmentation.py`,
`experiments/m2_fragmentation_scaling_scan.py`, and focused tests. No M2
dynamics, response probability, worker scheduler, or slot-meaning rule changed.
Full SCC, giant, mutual, tentacle, and object scans run only at checkpoints.

Ruff passed and 50 tests passed. New tests cover giant fractions and threshold
boundaries, three-consecutive-snapshot sustained detection, exact absolute
checkpoints without duplicate final rows, large-member suppression, and stats
invariance including RNG state.

The first execution completed all dynamics but exposed a variable-column bug
while writing top-10 SCC sizes. After fixing the union-field CSV writer, the
same deterministic scan was rerun and all artifacts were written successfully.

## Scaling summary

| N | completed sweeps | initial g | final g | first g<=0.2 | sustained g<=0.2 | first pair | first triple | initial edges/N | final edges/N | final pair/triple/larger | runtime s |
|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|
| 100 | 30000 | 1.000 | 0.030 | 10000 | not established | 3000 | 10000 | 11.340 | 1.180 | 41/6/0 | 34.4 |
| 200 | 30000 | 1.000 | 0.015 | 10000 | not established | 1000 | 100 | 11.690 | 1.290 | 64/24/0 | 74.1 |
| 500 | 30000 | 1.000 | 0.012 | 30000 | not established | 100 | 10 | 11.886 | 1.368 | 165/52/3 | 220.2 |
| 1000 | 30000 | 1.000 | 0.008 | 30000 | not established | 30 | 30 | 11.944 | 1.417 | 324/103/8 | 443.4 |

Sustained fragmentation requires three consecutive recorded snapshots with
g<=0.2. The checkpoint list supplies only 10,000 and 30,000 after crossing for
N=100/200, and only 30,000 for N=500/1000. Therefore no sustained first passage
is reported, even though all final fractions are small.

## Checkpoint time series

Values are `giant fraction / candidate edges per node / raw mutual pairs per node / closed pair / closed triple`.

| sweep | N=100 | N=200 | N=500 | N=1000 |
|---:|---|---|---|---|
| 0 | 1.000/11.340/0.020/0/0 | 1.000/11.690/0.015/0/0 | 1.000/11.886/0.012/0/0 | 1.000/11.944/0.007/0/0 |
| 10 | 1.000/8.830/0.250/0/0 | 1.000/9.280/0.270/0/0 | 0.994/9.612/0.234/0/1 | 1.000/9.445/0.253/0/0 |
| 30 | 1.000/9.150/0.230/0/0 | 1.000/9.260/0.245/0/0 | 0.976/9.212/0.240/0/1 | 0.995/9.315/0.244/1/1 |
| 100 | 1.000/9.350/0.250/0/0 | 0.985/9.020/0.285/0/1 | 0.972/9.282/0.236/1/1 | 0.988/9.338/0.213/1/1 |
| 300 | 1.000/9.350/0.130/0/0 | 0.865/8.990/0.260/0/1 | 0.954/9.164/0.272/3/1 | 0.979/9.246/0.249/4/2 |
| 1000 | 1.000/9.150/0.180/0/0 | 0.790/8.540/0.220/3/1 | 0.920/9.022/0.256/9/1 | 0.920/8.943/0.251/9/6 |
| 3000 | 0.770/7.790/0.320/5/0 | 0.560/7.280/0.290/8/4 | 0.800/8.310/0.276/19/6 | 0.752/8.099/0.286/31/17 |
| 10000 | 0.110/2.650/0.540/22/4 | 0.115/3.425/0.535/33/12 | 0.402/4.790/0.434/81/29 | 0.325/5.031/0.447/138/51 |
| 30000 | 0.030/1.180/0.530/41/6 | 0.015/1.290/0.510/64/24 | 0.012/1.368/0.564/165/52 | 0.008/1.417/0.562/324/103 |

## First passage and finite-size fits

Observed first g<=0.8 sweeps: 3000, 1000, 3000, 3000. First g<=0.5:
10,000 for all sizes. First g<=0.2: 10,000, 10,000, 30,000, 30,000.

The exploratory finite-size fit for first g<=0.2 is:

```text
T_sweeps(N) = 629.6 * N^0.576
R² = 0.844, sample_count = 4
```

This is a coarse checkpoint-binned finite-size fit, not a law. The g<=0.8 fit
has alpha=0.164 and R²=0.091; g<=0.5 is unresolved by the checkpoint grid because
all four first appear at 10,000 sweeps.

For first g<=0.2, coefficients of variation are 0.500 for T in sweeps, 0.425
for T/N, and 1.095 for T/N². T/N is marginally the most stable of these three
normalizations, but four coarse samples do not distinguish linear from a
sublinear crossover reliably.

System-wide first pair/triple fits have negative exponents because larger N
provides more simultaneous locations for a first event and the checkpoint grid
is coarse. They should not be interpreted as local object-formation scaling.

## Density and stage correlation

Across all 36 snapshots:

```text
corr(g, candidate_edges_per_node) = +0.9651
corr(g, raw_mutual_pairs_per_node) = -0.8751
```

The largest SCC falls rapidly once candidate density enters roughly the 3--5
edges/node range: at 10,000 sweeps N=100/200 are already near g=0.11 with
2.65--3.43 edges/node, while N=500/1000 remain at g=0.40/0.325 with 4.79--5.03.
At 30,000 sweeps all sizes have 1.18--1.42 edges/node and g<=0.03.

Raw mutual support generally rises as fragmentation advances, but the sparse
checkpoints do not establish temporal precedence. The data support correlation,
not causation or a lead/lag claim.

## Object emergence relative to the giant

First pair/triple events occur before g<=0.2 for every size. For N=500/1000 they
appear while g>=0.97, showing that small closed SCCs can split off while a giant
candidate SCC still dominates rather than only being generated after total
fragmentation.

Object snapshots begin only when g<=0.5. Among newly observed objects whose
members can be checked against the previous checkpoint giant, born-from-previous-
giant counts were 26, 39, 166, and 264 for N=100,200,500,1000. This is checkpoint-
level lineage evidence, not a continuous event history.

## Evidence boundary

### Established in this scan

- Every tested size reached g<=0.2 by 30,000 sweeps.
- Candidate density and giant fraction declined together strongly.
- Pair/triple objects appeared well before final fragmentation.
- N=1000 completed the full 30,000-sweep checkpoint, not merely 3,000.

### Candidate interpretations

- The giant SCC is an initial transient for these four finite sizes and this seed.
- Fragmentation time grows sublinearly to roughly linearly over this small range.
- Local closed objects split from the giant during its decay.

### Not established

- Fragmentation is sustained under the strict three-checkpoint definition.
- Every seed or N->infinity fragments.
- The fitted exponent has an asymptotic meaning.
- Pair/triple objects are unique final attractors.

The absence of a giant at 30,000 sweeps is evidence against permanent
condensation for these finite runs, but not a proof against it generally. A
follow-up with checkpoints beyond 30,000 is useful specifically to establish
sustained fragmentation; multi-seed work should follow only after that sampling
gap is closed.
