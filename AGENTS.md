# Uboot working contract

- Keep Uboot an independent Python package; reference Iboot through documented
  contracts rather than directory nesting or copied theory.
- Keep modules small and weakly coupled. Add a dependency only when its invariant
  and validation path are explicit.
- Recompose selected modules into a task-local plan before executing them.
- Keep generated data, figures, logs, and large experiment outputs out of Git.
- Do not copy from `C:\Users\cogg\universe` without an explicit migration task,
  provenance record, and focused validation.
- Add tests for every promoted behavior. A historical script is evidence, not an
  API specification.
