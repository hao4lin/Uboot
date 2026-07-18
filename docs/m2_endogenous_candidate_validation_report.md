# Corrected M2 candidate validation status

The M2 relation remains `C(i) = (IN(i) union OUT2(i)) - {i}`, but execution is
now slot-aware. An update may retain its own old target and may not choose either
target occupied by the node's other slots. Active and response paths share this
rule; a response additionally excludes its incoming source.

Validation covers:

- construction and retarget rejection of duplicate targets;
- distinct seeded initialization;
- an executable pool that retains the updating slot's old target;
- rejection of targets occupied by either other slot;
- active and response invariance after every tick of a 20,000-tick run;
- checkpoint rejection of duplicate-target payloads;
- deterministic checkpoint continuation and statistics-on/off identity;
- exact two-step `OUT2`, mutual eligibility, and candidate/static-layer
  separation.

The old numerical scans are intentionally not copied into this branch. New
finite-size data is required before making any fragmentation or fixed-point
claim.
