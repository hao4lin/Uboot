# Corrected M2 baseline preparation

Version 2 is code-frozen only after its tests pass; it is not yet empirically
frozen. No replacement tag or canonical checkpoint exists until the corrected
fragmentation scan is complete.

The generation entry point writes a deterministic checkpoint containing the
full scheduler and RNG state. Repeated loads must fork identically, statistics
must not affect dynamics, and duplicate-target checkpoints must fail closed.

After the user supplies the new scan output, choose the warm-up boundary from
that evidence, generate the N=1000 replacement checkpoint, validate phase-two
readiness if present, then create the reserved tag
`m2-distinct-target-baseline-v2`. Do not reuse the archived version-1 checkpoint.
