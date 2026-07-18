# Uboot

Uboot is an independent Python simulator for exploring how universe-level
objects and laws might bootstrap from minimal structures.

Its software lifecycle is independent from Iboot. Its operating principles are
informed by Iboot: select small weakly coupled modules by task direction,
recompose them before execution, preserve minimum sufficient invariants, and
retain backward validation paths.

## Status

The first implemented model explores a deliberately narrow transition: a random
three-slot raw-object network is initialized once, then every rewrite target is
derived only from current relations. No legacy source or data was migrated.

## Development

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python experiments\endogenous_bootstrap.py --N 1000 --max-steps 1000000 --seed 0
.venv\Scripts\python experiments\edge_response.py --profile M1 --N 100 --background-sweeps 1000 --seed 0
.venv\Scripts\python experiments\edge_response_batch.py --sizes 20 50 100 --seeds 0 1 2 3 4
.venv\Scripts\python experiments\m2_incremental_history.py --help
.venv\Scripts\python experiments\joint_pair_triangle_continuation.py --help
.venv\Scripts\python experiments\joint_touch_replay.py --help
```

Long edge-response runs report active-slot completion, processed responses, and
current FIFO length to standard error every 60 seconds, plus final completion.
Batch runs print a separator before every child experiment with its batch index,
profile, N, seed, background sweeps, and output directory.

Edge-response output uses bounded periodic snapshots rather than continuous event
logs. `--snapshot-interval-sweeps` controls snapshot frequency and
`--worker-count` controls concurrent reusable response tendrils (default one).

See `docs/architecture.md`, `docs/iboot-contract.md`, and
`docs/legacy-inventory.md` before adding simulation behavior.

See `experiments/README.md` for resumable short-chunk M2 history generation and
joint closed-pair/closed-triple continuation and update-touch diagnostics.
