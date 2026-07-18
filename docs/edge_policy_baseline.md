# Edge-policy comparison baseline

`baseline_current` is the current corrected M2 engine at commit `63b6b8b`. It is
wrapped only by the comparison runner; the engine receives no relation-strength
state or policy callback.

## Actual edge-change rule

- The object-level candidate support pool is
  `C(i) = (IN(i) union OUT2(i)) - {i}`.
- For active slot `s`, the executable pool removes the targets held by `i`'s
  other two slots. The old target of `s` remains eligible.
- Uniform selection from that pool may naturally sample the old target or propose
  another target. An empty pool is a no-op.
- Every active slot is visited once per shuffled fair sweep.
- A same-slot reciprocal result can create the existing cross-tick response.
  A response chooses a response slot, uses the same executable pool, additionally
  removes its incoming source, and can therefore change an earlier relation.
- Reciprocal relations are observed by `is_consistent` and snapshot statistics;
  they do not create persistent lock state.
- There is no `locked` flag, relation strength, promotion, or demotion.

## Graph and object semantics

The current SCC, `g`, pair, and triple statistics use the full current candidate
support graph `C`, not the raw-slot graph and not the strict reciprocal graph.
A pair or triple is a closed candidate SCC of size two or three. In the corrected
N=100 run through 300,000 sweeps, three distinct targets preserve roughly 10.2
candidate edges/node, so the candidate graph remains one size-100 SCC and no
candidate pair/triple appears.

## Minimal control points

- `get_current_candidate_targets`: object-level support pool.
- `EdgeResponseEngine.executable_candidates`: slot-level uniqueness filter that
  permits the slot's old target.
- `EdgeResponseEngine._active_update`: active proposal and commit path.
- `EdgeResponseEngine._advance_worker`: response proposal and commit path.
- `EdgeResponseEngine._retarget`: the single raw edge-state commit boundary.
- `EdgeResponseEngine.is_consistent`: strict same-slot reciprocity query.

The independent comparison engine varies only proposal scope, resistance,
strength evolution, promotion, and hard-lock decisions around equivalent
slot-level commit points. It does not change raw identities, slot count or
meaning, fair active scheduling, sweep size, or the current baseline engine.
