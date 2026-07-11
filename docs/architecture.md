# Architecture boundary

Uboot begins with one deliberately small cycle:

1. represent a task-local direction;
2. select only modules aligned with that direction;
3. recompose them into an explicit `FusionPlan`;
4. execute without mutating the input state;
5. validate named invariants against the result.

The current code defines interfaces, not a universe model. Physical objects,
laws, update rules, numerical methods, and experiment-specific dependencies must
enter through later focused changes with tests and provenance.

The initial sequential `FusionPlan.run` is a minimal executable boundary, not a
claim that the final simulator is fundamentally sequential. It may later be
replaced or supplemented by graph, fixed-point, concurrent, or cortical-style
execution while keeping module contracts stable.
