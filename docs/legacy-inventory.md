# Legacy experiment inventory

Inventory date: 2026-07-11

Source: `C:\Users\cogg\universe`

Source Git status: not a Git repository

No legacy content was modified or copied during bootstrap.

## Observed scale

Including the local virtual environment:

- 8,731 files in 839 directories
- approximately 352 MB
- `.venv` alone: 7,944 files and approximately 286 MB

Excluding `.venv` and top-level Python cache:

- 765 files and approximately 65 MB
- 25 Python source files
- 431 CSV files
- 177 image files
- 61 Markdown files

Notable source files include `em1.py` (219,102 bytes), the `u2` through `u8`
series, the `r1` through `r4` analysis series, and timestamped sources under
`archive/`. Generated result directories and reports substantially outnumber
maintainable source files.

## Migration boundary

Nothing should be bulk imported. A later migration pass should classify each
candidate as one of:

1. maintainable source;
2. reproducible experiment entry point;
3. minimal test fixture;
4. generated artifact;
5. historical archive.

Only the first three categories are candidates for Git, and each promoted item
requires provenance plus focused validation.
