# Experiments

Keep reproducible experiment entry points here. Each experiment should declare
its configuration, seed where applicable, expected outputs, and artifact path.
Do not commit generated outputs.

`endogenous_bootstrap.py` uses one single-slot rewrite attempt as one step. Its
random initialization is the only exogenous target-selection phase; all later
rewrites use relation-derived candidates. During a long run it writes step
progress and the most recent stage statistics to standard error once per minute,
without an additional scan. Generated CSV, JSON, snapshot, and Markdown files
belong under the ignored `artifacts/` tree.

Heavy snapshots additionally measure candidate-set sizes and closed strongly
connected components of the candidate graph. These measurements are derived
entirely from the heavy snapshot and do not alter or instrument each rewrite.

The edge-response M1 experiment uses a bounded discrete-tick worker scheduler.
Each occupied worker advances at most once before one active-slot update; newly
created tasks cannot advance until the next tick. Its snapshot candidate graph
is the exact direct-candidate function used by M1 selection, not the older
relation-endogenous `IN + OUT2` graph. M0 bypasses this response system.

## Incremental M2 history

`m2_incremental_history.py` continues the frozen M2 baseline from an existing
checkpoint in short, immutable chunks. The first invocation fixes
`--source-checkpoint` as `root_checkpoint.txt`. Later invocations with the same
`--run-dir` replay that fixed root without statistics to the last verified
history boundary, then append the next chunk. Repeating the same command advances
rather than restarting or overwriting data.

Each completed chunk writes:

- complete system and closed-object snapshots, including absolute tick-based
  `snapshot_id`, sweep, sorted member IDs, and static support fields;
- a manifest with parent/output checkpoint hashes and start/final dynamics hashes;
- a new independently inspectable checkpoint at the chunk boundary;
- one append-only row in `history_index.csv`.

Chunk boundary snapshots intentionally appear in both adjacent chunk files.
Offline readers deduplicate an identical boundary and reject conflicting data.
`latest_checkpoint.txt` identifies the newest saved state for inspection, but
generation deliberately replays from the fixed root. The frozen engine's target
selection depends on Python set layout, which cannot be serialized strictly
enough for repeated cross-process continuation at N=1000. Root replay preserves
one checkable trajectory without changing or sorting candidate selection. One
lock file prevents two writers from advancing the same run directory.

Example foreground invocation:

```powershell
.venv\Scripts\python experiments\m2_incremental_history.py `
  --run-dir artifacts\m2_incremental_main `
  --source-checkpoint artifacts\checkpoints\m2_generation_baseline_v1\N1000_seed20260712_w1_s60000 `
  --chunk-sweeps 3000 `
  --snapshot-interval-sweeps 1
```

The existing interval-one N=1000 measurement took about 167 seconds per 1,000
sweeps on machine COGG, so 3,000 sweeps is a reasonable first approximately
ten-minute chunk. Runtime and output volume depend strongly on snapshot interval;
adjust `--chunk-sweeps` after inspecting the first `history_index.csv` row. Later
invocations also report replay cost; reduce chunk size if replay plus recording
becomes too long.

Detached Windows invocation (safe to close the launching terminal afterward):

```powershell
$root = Resolve-Path .
$runDir = Join-Path $root "artifacts\m2_incremental_main"
$logDir = Join-Path $runDir "logs"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force $logDir | Out-Null
$arguments = @(
  "experiments\m2_incremental_history.py",
  "--run-dir", $runDir,
  "--source-checkpoint", (Join-Path $root "artifacts\checkpoints\m2_generation_baseline_v1\N1000_seed20260712_w1_s60000"),
  "--chunk-sweeps", "3000",
  "--snapshot-interval-sweeps", "1"
)
$job = Start-Process `
  -FilePath (Join-Path $root ".venv\Scripts\python.exe") `
  -ArgumentList $arguments `
  -WorkingDirectory $root `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $logDir "$stamp.out.log") `
  -RedirectStandardError (Join-Path $logDir "$stamp.err.log") `
  -PassThru
$job.Id
```

Do not start a second process for the same run directory while the first is
active. A normal exit removes `.incremental.lock`; after a forced termination,
verify that the recorded process has stopped before manually removing that lock.

## Joint pair-triple continuation

`joint_pair_triangle_continuation.py` is an offline analysis. It consumes either
one existing snapshot output directory or an entire incremental run directory.
It tracks one actual closed candidate-SCC pair and one actual closed
candidate-SCC triple independently in ascending and descending snapshot-index
directions. Slices without a unique joint continuation are collapsed without
altering source files; ambiguity or structural overlap stops only that direction.

```powershell
.venv\Scripts\python experiments\joint_pair_triangle_continuation.py `
  --input-dir artifacts\m2_incremental_main `
  --start-snapshot-id 180000000 `
  --auto-select-candidate `
  --seed 20260718 `
  --max-auto-attempts 100 `
  --output-dir artifacts\joint_tracks\track_001
```

The generated `snapshot_id` is the absolute engine tick within one checkpoint
lineage. Raw snapshot spacing is an index interval, not physical time.
