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

## Joint movement-touch diagnostics

`joint_touch_replay.py` passively observes active selections and actual retarget
events while reusing `phase2.snapshot_state` for the paired snapshots. It writes
`joint_fixed_point_update_touches.csv`, compatible phase-two system/object
snapshots, and a manifest with start/final dynamics hashes. The observer is not
part of the engine state and does not consume RNG values.

Exact N=1000 replay from the frozen checkpoint:

```powershell
.venv\Scripts\python experiments\joint_touch_replay.py `
  --checkpoint artifacts\checkpoints\m2_generation_baseline_v1\N1000_seed20260712_w1_s60000 `
  --tracking-sweeps 1000 --snapshot-interval-sweeps 1 `
  --start-snapshot-id 0 --pair-ids 616 629 `
  --triangle-ids 602 979 993 `
  --output-dir artifacts\joint_touch_replay_n1000
```

N=100 same-configuration rerun from initialization:

```powershell
.venv\Scripts\python experiments\joint_touch_replay.py `
  --N 100 --seed 20260712 --warmup-sweeps 4220 `
  --tracking-sweeps 5780 --snapshot-interval-sweeps 10 `
  --start-snapshot-id 422 --pair-ids 4 83 `
  --triangle-ids 12 32 48 `
  --output-dir artifacts\joint_touch_replay_n100
```

Analyze either paired replay without rewriting the continuation tracker:

```powershell
.venv\Scripts\python experiments\joint_pair_triangle_continuation.py `
  --input-dir artifacts\joint_touch_replay_n1000 `
  --start-snapshot-id 0 --pair-ids 616 629 `
  --triangle-ids 602 979 993 `
  --touch-observations artifacts\joint_touch_replay_n1000\joint_fixed_point_update_touches.csv `
  --output-dir artifacts\joint_diagnostics_n1000
```

This adds `joint_fixed_point_track_diagnostics.csv`, the movement-only
`joint_fixed_point_movement_events.csv`, and JSON/Markdown touch summaries. A
zero denominator is reported as `N/A`. Because the historical N=100 output has
no event log or intermediate checkpoint, its old descending snapshots remain
valid for snapshot-only continuation evidence, while update counts come from the
new same-process replay and its paired snapshots.

## Two-snapshot pair-triangle microtrace

`pair_triangle_microtrace.py` analyzes exactly one S0-to-S1 interval. It calls
the frozen engine's existing `step_tick()` from an outer wrapper and classifies
the closed pair/triple state after every completed tick. The existing optional
update observer only collects committed active/response retarget details inside
that tick. It does not return a control value, call the RNG, or mutate engine
state.

N=100 adjacent interval selected from the paired touch replay:

```powershell
.venv\Scripts\python experiments\pair_triangle_microtrace.py `
  --N 100 --seed 20260712 --warmup-sweeps 4220 `
  --snapshot-interval-sweeps 10 `
  --start-snapshot-id 422 --end-snapshot-id 423 `
  --pair-ids 4 83 --triangle-ids 12 32 48 `
  --snapshot-dir artifacts\joint_touch_replay_n100_v3 `
  --output-dir artifacts\microtrace_n100_s422_s423
```

N=1000 adjacent interval from the frozen 60,000-sweep checkpoint:

```powershell
.venv\Scripts\python experiments\pair_triangle_microtrace.py `
  --root-checkpoint artifacts\checkpoints\m2_generation_baseline_v1\N1000_seed20260712_w1_s60000 `
  --snapshot-interval-sweeps 1 `
  --start-snapshot-id 0 --end-snapshot-id 1 `
  --pair-ids 616 629 --triangle-ids 602 979 993 `
  --snapshot-dir artifacts\joint_touch_replay_n1000_v1 `
  --output-dir artifacts\microtrace_n1000_s0_s1
```

The default `structure-only` detail writes only touched or recognition-changing
ticks to the event CSV, while the summary still counts every atomic tick. Use
`--write-all-atomic-events` or `--event-detail-level all` only for a deliberately
small interval; `--max-event-rows` bounds detail output. Consecutive identical
micro states are always run-length encoded in the state-runs CSV.

Every run executes the interval twice: once directly and once with microtrace.
It refuses output unless start/final dynamics hashes, final tick, RNG state,
counters, raw targets, response aggregates, phase-two readiness, and complete
pair/triple static signatures agree. When `--snapshot-dir` is supplied, the
requested endpoint ticks and selected object memberships must also match the
saved snapshots.

## Fixed-point exposure relation tracing

`fixed_point_exposure_trace.py` is an independent analysis wrapper around the
frozen engine. At the start slice it indexes every closed pair and triangle with
stable analysis-only IDs. An internal support slot is any exact raw slot witness
used by the object's existing M2 closure support. An exposed slot must originate
at an object member, must not be one of those support slots, and must target
outside that object. The trace retains raw member/slot/target implementations,
their direct object links, and detailed two-hop paths.

N=100, snapshot 422 to 423:

```powershell
.venv\Scripts\python experiments\fixed_point_exposure_trace.py `
  --N 100 --seed 20260712 --warmup-sweeps 4220 `
  --snapshot-interval-sweeps 10 `
  --start-snapshot-id 422 --end-snapshot-id 423 `
  --root-pair-ids 4 83 --root-triangle-ids 12 32 48 `
  --snapshot-dir artifacts\joint_touch_replay_n100_v3 `
  --object-radius 2 --full-object-index `
  --verification-full-recompute `
  --output-dir artifacts\exposure_trace_n100_s422_s423_v2
```

N=1000, snapshot 0 to 1:

```powershell
.venv\Scripts\python experiments\fixed_point_exposure_trace.py `
  --root-checkpoint artifacts\checkpoints\m2_generation_baseline_v1\N1000_seed20260712_w1_s60000 `
  --snapshot-interval-sweeps 1 `
  --start-snapshot-id 0 --end-snapshot-id 1 `
  --root-pair-ids 616 629 --root-triangle-ids 602 979 993 `
  --snapshot-dir artifacts\joint_touch_replay_n1000_v1 `
  --object-radius 2 --full-object-index `
  --verification-full-recompute `
  --output-dir artifacts\exposure_trace_n1000_s0_s1_v2
```

The default detail file contains changed events only; `--event-detail-level all`
or `--write-all-atomic-events` is intended only for small intervals. Every run
still counts all ticks and writes run-length encoded state history. With
`--verification-full-recompute`, every incremental state is checked against a
fresh full reconstruction. The CLI also runs an unobserved replay and refuses
output unless the dynamics hash, RNG state, counters, raw targets, and static
object evidence agree.

For both reference intervals the strict support definition exhausts every member
slot: there are no exposed slots, hence no direct or two-hop exposure relations.
This is reported as `no_exposed_slots_under_strict_support_definition`; zero
denominators remain `N/A`. See
`docs/fixed_point_exposure_relation_report.md` for the interpretation.
