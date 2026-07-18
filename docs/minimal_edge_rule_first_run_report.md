# Minimal reciprocal edge rules: first N=100 run

## Scope and validation

- Seed: `20260712`.
- N: 100 raw objects, three distinct semantic targets each.
- Atomic updates: random active node, random active slot.
- Snapshots: 0, 100, 300, 1,000, 3,000, and 10,000 sweeps.
- Policies: `baseline_random_replace`,
  `direct_reciprocal_candidate_first`, and
  `old_new_reciprocal_compare`.
- The baseline final network hash and complete RNG state exactly matched the
  independent frozen reference loop.
- R/Q statistics, transitions, relation ages, and component episodes were
  passive and never fed back into selection.

The full ignored artifact is
`artifacts/minimal_edge_rule_compare_N100_10k_20260718/`.

## Time-series result

| Policy | Q edges at 0/100/300/1k/3k/10k | Q max component at 10k | Reciprocal slots at 10k | Mean current relation age at 10k |
| --- | --- | ---: | ---: | ---: |
| Baseline | 1 / 1 / 0 / 2 / 3 / 1 | 2 | 2/300 | 302.45 ticks |
| Direct reciprocal first | 1 / 149 / 150 / 150 / 150 / 150 | 100 | 300/300 | 2,974,551.37 ticks |
| Old/new reciprocal compare | 1 / 101 / 135 / 142 / 146 / 150 | 100 | 300/300 | 2,174,329.11 ticks |

Baseline remained loose. Its completed relation age was 303.11 ticks, Q stayed
at zero to three isolated pair components, and both `n->r` and `r->n` continued
at comparable cumulative counts (59,483 each at 10k).

Policy B reached a size-100 Q component by sweep 100 and all 150 undirected Q
edges (all 300 directed slots reciprocal) by sweep 300. Its target-change count
stopped at 2,358 after sweep 300 while target keeps continued to 2,997,642. The
rule has no explicit lock, but the fully reciprocal three-slot graph is an
absorbing state because each active slot's only direct same-slot returner is its
current target.

Policy C reached a size-100 Q component by sweep 300, then grew from 135 Q edges
at 300 to 142 at 1k, 146 at 3k, and all 150 at 10k. It recorded both formation
and loss (`n->r=614`, `r->n=316`) and therefore did exhibit reversible
reciprocity during approach. Nevertheless, the cumulative retention rate rose
to 0.999946 and the endpoint was again the fully reciprocal absorbing graph.

## Component lifetime result

No pair or triangle component persisted across more than one sampled interval.
Policy B's size-100 component was observed from sweep 100 through 10,000 and is
right-censored at 9,900 observed sweeps. Policy C's size-100 component was
observed from sweep 300 through 10,000 and is right-censored at 9,700 observed
sweeps. Their trend is global adhesion, not gradual accumulation of independent
small Q components.

## Answers and branch decision

1. All three rules ran according to their minimal definitions.
2. Baseline reproduced its frozen direct reference exactly.
3. B increased reciprocal count and lifetime extremely rapidly, then saturated.
4. C retained reciprocal relations far more strongly than baseline.
5. Neither B nor C produced a long-lived small Q pair/triangle component.
6. Both B and C merged Q into one size-100 component.
7. C allowed both reciprocal formation and loss during approach, but loss became
   negligible and finally ceased at the absorbing endpoint.
8. Baseline is too loose; B rapidly globally adheres; C more slowly globally
   adheres. Neither is a trend that merits longer identical runs.
9. There is no compute-limited unresolved trend in B or C at N=100.
10. Under the specified branch rule, abandon B and C as generating rules. Do not
    extend them, tune them, or add reward, lock, strength, closure, or scoring.

This result answers only the two tested reciprocal-priority rules. It does not
establish that every possible minimal local generation rule globally adheres.
