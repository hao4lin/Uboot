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
```

Long edge-response runs report active-slot completion, processed responses, and
current FIFO length to standard error every 60 seconds, plus final completion.

See `docs/architecture.md`, `docs/iboot-contract.md`, and
`docs/legacy-inventory.md` before adding simulation behavior.
