# Experiments

Keep reproducible experiment entry points here. Each experiment should declare
its configuration, seed where applicable, expected outputs, and artifact path.
Do not commit generated outputs.

`endogenous_bootstrap.py` prints CSV-compatible samples to standard output. Its
random initialization is the only exogenous target-selection phase; all later
rewrites use relation-derived candidates.
