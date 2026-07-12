# Edge-response logic audit

1. The prior model had exactly three slots per node, but treated slot numbers as operational rather than semantic.
2. Prior mutual statistics allowed return through different slots; same-slot consensus was not separate.
3. There was no lock or unlock state.
4. Candidates used any-slot incoming neighbors plus two-hop outgoing targets, without an incoming/global probability split.
5. Incoming candidates were not restricted to the same slot.
6. There was no target-node response.
7. Consequently there was no response rule or response probability.
8. Active slots were sampled independently; fairness existed only in expectation, not by a shuffled fair ring.
9. There was no response queue and therefore no queue-capacity overwrite risk.
10. Mutual components and cycles were derived statistics, not independent dynamical objects.

The new experiment keeps only directed retarget operations at the bottom. Any-slot mutual and same-slot consensus are separate derived statistics. Duplicate targets across a node's three semantic slots are blocked; no target-stealing behavior is implemented.
