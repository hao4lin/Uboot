# Edge-policy semantic difference report

The comparison is isolated from `EdgeResponseEngine`: `baseline_current` runs
that class directly, while the five alternatives run a subclass with slot-local
`RelationState` and a pure decision policy. All variants retain three distinct
non-self targets, three fixed semantic slots, the fair active schedule, response
ordering, snapshot points, and raw-state commit checks.

| Policy | Minimal difference from baseline | Internal stability | External mobility | Semicontinuous | Main failure risk |
| --- | --- | --- | --- | --- | --- |
| baseline_current | None; uses current `IN union OUT2` executable pool | No persistent mechanism | Current endogenous rewrites | No | One global candidate SCC and no local fixed point |
| hard_reciprocal_lock | Global eligible target pool; strict same-slot reciprocity permanently locks only those two slots | Immediate hard lock | Every unlocked slot remains globally selectable | No | Monotone accumulation and over-hard components |
| soft_reciprocal_inertia | Global pool plus strength-proportional resistance | Reciprocal observations add strength | Low-strength slots readily retarget | Yes | Longer lifetimes without promoted local structure |
| soft_internal_harder_than_external | Adds pair/triangle closure reinforcement and resistance | Closure-supported relations resist more | Unsupported slots retain low resistance | Yes | Closure signal may still be too weak or diffuse |
| threshold_internal_soft_break | Adds promotion/demotion hysteresis and extra internal resistance | Promoted slots are difficult, not impossible, to break | Non-internal slots use ordinary soft inertia | Yes | Parameter-sensitive promotion or excessive persistence |
| certified_internal_hard_external_free | Only high-strength reciprocal triangle-supported slots hard-lock | Certified slots cannot be broken | All other slots remain soft and independently mutable | Yes, before certification | Certification may be absent or hard locks may accumulate |

## Exact semantic boundaries

- Candidate scope changes only in the five experimental strategies: they use all
  nodes except self and targets occupied by the other two slots. The old target
  remains eligible. `baseline_current` keeps its existing endogenous pool.
- Sampling the old target is a natural hold, never a resisted change.
- Active and response resistance have separate configurable multipliers. Both
  paths commit through the inherited target-uniqueness boundary.
- Reciprocity is strict same-slot mutual targeting. It adds no persistent state
  in baseline, locks immediately only in the hard control, and otherwise provides
  gradual reinforcement.
- Strength belongs to one directed slot relation. Its replacement, reinforcement,
  disturbance, promotion, and demotion never write another slot on the same node.
  The sole paired write synchronizes the matching partner slot of a certified
  hard reciprocal relation.
- Policy C and D never promote an internal label. Policy E promotes above the
  upper threshold and demotes below the lower threshold. Policy F additionally
  requires reciprocal triangle support and then hard-locks that exact slot.
- Q identifies strict reciprocal raw relations; S identifies high-strength
  directed relations; L identifies promoted internal directed relations. The
  historical candidate SCC and `g` remain separately labeled C statistics and
  are not reused as Q, S, or L identities.
- All additional probability decisions use a deterministic policy RNG seeded by
  `main_seed + stable_policy_seed_offset`; they never draw from the main engine
  RNG. Baseline uses no policy RNG and therefore remains hash-identical.

This report describes mechanisms only. Whether a policy is over-soft,
over-hard, or a useful compromise must be decided from its generated Q/S/L
time series, relation lifetimes, external change rate, and strength transitions.
