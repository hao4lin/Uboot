# M2 generation freeze report

The immutable baseline is `M2_endogenous_candidate_baseline` version `1.0.0`,
with candidate rule `IN union OUT2 - self`, the bounded cross-tick response
scheduler, and deterministic checkpoint serialization. The release tag is
`m2-generation-baseline-v1`; the commit and generated checkpoint evidence are
`5ba92c7ee2499cbbc0b01cc310617de82d9e0411`.

The required warmup artifact is
`artifacts/checkpoints/m2_generation_baseline_v1/N1000_seed20260712_w1_s60000`.
It has dynamics hash `da8a48ac23f7b716ace39a32ad1e63d6232c5d4e295a2f13325fb17635d294f3`.
Validation found maximum candidate SCC size 3, 335 closed pairs, 110 closed
triples, zero closed larger objects, exact coverage of all 1000 nodes, and
coverage 1.0; therefore it is phase2-ready.

Ruff passes. The freeze-commit test scope has 54 tests, including exact
save/load continuation, double-load fork identity, stats identity, compatibility
rejection, and pair/triple readiness validation. Generated checkpoint hashes and
the N=1000 continuation result are recorded in the manifest.

The first generated 60k checkpoint predates serialization of the incoming-set
iteration layout. It is phase2-ready and reproducible when loaded repeatedly,
but a direct 0-to-61k run exposed that rebuilding incoming sets can change
candidate iteration order. The baseline serializer now stores that layout; the
user waived a full N=1000 regeneration after the logic repair. Accordingly the
existing artifact must not be described as strict cross-process continuation
evidence for the repaired format.
