# Experiments

Keep reproducible experiment entry points here. Each experiment should declare
its configuration, seed where applicable, expected outputs, and artifact path.
Do not commit generated outputs.

`endogenous_bootstrap.py` prints CSV-compatible samples to standard output. Its
random initialization is the only exogenous target-selection phase; all later
rewrites use relation-derived candidates. During a long run it writes sweep
progress and completion percentage to standard error once per minute, plus a
final 100% report, so redirected CSV output remains clean. The progress line
also reuses the most recently sampled mutual-pair count and density; it performs
no additional network scan, and identifies the sample sweep when it may lag.
