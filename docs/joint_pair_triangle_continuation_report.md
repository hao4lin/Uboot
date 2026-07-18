# Joint pair-triple continuation report

Run date: 2026-07-18. The implementation is offline and does not modify the
frozen M2 candidate rule, response scheduler, RNG calls, or static closed-object
definition.

## Inputs and object definition

The tracker consumes the existing `system_snapshots.csv` plus
`closed_object_snapshots.csv` format, including its `phase2_` filename variant.
A pair or triple is an actual closed candidate strongly connected component with
respectively two or three members. Raw mutual pairs, same-meaning mutual pairs,
candidate two-cycles, and raw-mutual triangles remain support statistics and are
not silently substituted for the closed-object definition.

The complete system file supplies the God-slice order even when a slice has no
pair or triple rows. Generated incremental history uses the absolute engine tick
as `snapshot_id`; tick and sweep are retained separately. Index spacing is not
interpreted as physical time.

## Validation results

Ruff passed and the full suite passed with 69 tests. New tests cover pair/triple
retention and migration, nonmatching intermediate-slice collapse, recursive
current-object updates, ambiguity termination, structural conflict, independent
ascending/descending initialization, invalid starts, empty-object God slices,
chunk-boundary deduplication, and incremental dynamics identity.

For the existing N=1000 interval-one phase-two output, automatic selection at
snapshot 0 chose pair `616|629` and triple `602|979|993`. The ascending scan
accepted all 1,001 recorded slices with no skipped slices, ambiguity, conflict,
or member change. The descending side contained only the starting boundary
slice. This independently recovers the earlier finding that the closed-object
member partition was stable over sweeps 60,000 through 61,000; internal support
rewiring is outside this member-continuation statistic.

For the existing N=100, 10,000-sweep validation output, automatic selection at
snapshot 422 chose pair `4|83` and triple `12|32|48`. The ascending scan accepted
all remaining 579 slices without member change or a skipped slice. The
descending scan found no joint continuation across the earlier 422 slices. A
search of adjacent recorded slices found no uniquely matched pair/triple joint
continuation with member replacement. Thus the current real data validate the
stable and absent-continuation branches; migration behavior is covered by
focused fixtures but has not yet been observed in these artifacts.

## Strict incremental history

The first real N=1000 smoke chunk continued the historical 60,000-sweep
checkpoint by one sweep and wrote two boundary snapshots plus a new checkpoint.
It ended at dynamics hash
`fe6fbde6296b63a50265599cc62a4f98f1be77999b72a4aad242211f1c23fb20`.

Testing a second conventional checkpoint resume exposed the historical
incoming-set layout limitation: it differed from an uninterrupted continuation
at sweep 60,002. The new runner therefore fixes the 60,000-sweep checkpoint as a
root and, on each invocation, replays it without statistics to the last recorded
boundary before appending a new chunk. With this rule the second chunk ended at
`1ff8118581b0de9a8f2d33c400dece4e42a8fbfeeb20f0962442e215264c38db`,
exactly matching a direct root-to-60,002 run. No sorting or replacement candidate
rule was introduced.

Every chunk records replay cost, recording runtime, parent/root hashes,
start/final dynamics hashes, full static object snapshots, and an independently
inspectable output checkpoint. Adjacent chunk boundary slices are duplicated on
disk for recovery and deduplicated only by the offline reader after verifying
that their contents match.

## Evidence boundary

Established:

- incremental history can be appended without overwriting prior chunks;
- every new boundary is verified against deterministic replay from one root;
- joint tracking reads both historical phase-two outputs and accumulated chunk
  directories;
- snapshot statistics leave the tested dynamics unchanged.

Not established:

- a pair or triple member migration occurs in the existing recorded real data;
- the chosen stable structures have a physical time interpretation;
- repeated loading of an arbitrary saved checkpoint preserves the frozen
  baseline's hidden Python-set layout. Such checkpoints remain inspection and
  branch artifacts; strict generation uses root replay.
