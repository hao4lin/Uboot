# Iboot contract

Uboot is independent software informed by Iboot rather than a subdirectory or
implementation copy of Iboot.

The initial operational contract is intentionally small:

- infer direction from input, objective, and requested operation;
- select the minimum sufficient weakly coupled modules;
- preserve explicit invariants at module boundaries;
- recompose selected modules before execution;
- retain named backward checks so compressed conclusions remain recoverable;
- distinguish updating simulation state from learning or changing the update
  operator itself.

The canonical theory remains in the Iboot repository and its `MetaKnowledge`
document. This file records only the software-facing interface. A later change
must identify the precise Iboot source and version when promoting a theoretical
claim into executable behavior.
