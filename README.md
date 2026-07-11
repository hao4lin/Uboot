# Uboot

Uboot is an independent Python simulator for exploring how universe-level
objects and laws might bootstrap from minimal structures.

Its software lifecycle is independent from Iboot. Its operating principles are
informed by Iboot: select small weakly coupled modules by task direction,
recompose them before execution, preserve minimum sufficient invariants, and
retain backward validation paths.

## Status

This repository currently contains only the clean project skeleton. The legacy
experiment tree at `C:\Users\cogg\universe` has been inventoried but no source,
data, report, or artifact has been migrated.

## Development

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

See `docs/architecture.md`, `docs/iboot-contract.md`, and
`docs/legacy-inventory.md` before adding simulation behavior.
