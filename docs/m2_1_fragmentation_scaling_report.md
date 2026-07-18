# Corrected M2 fragmentation rerun plan

All version-1 fragmentation numbers are invalid for the distinct-target model.
The retained scanner computes checkpoint-only candidate SCC, density, closed
object, first-passage, and scaling summaries without changing dynamics.

The first replacement run should use one seed and sizes 100, 200, 500, and 1000
through 30,000 sweeps. It must verify `duplicate_slot_ratio == 0` at every saved
checkpoint before its topology results are accepted. The outputs remain outside
Git under a new `m2_distinct_v2` artifact directory.

Only after this scan establishes the corrected fragmentation trajectory should
a new warm-up length be selected for the replacement N=1000 checkpoint. The old
60,000-sweep boundary is not assumed to remain appropriate.
