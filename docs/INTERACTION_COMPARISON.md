# Historical interaction comparison

The current recommended entrypoint is the [single discovery pilot](DISCOVERY_PILOT.md):
one clean episode plus six successive combined iterations, with three victim episodes
per valid candidate. The old `checkout comparison` command has been replaced by
`checkout discovery`.

The earlier comparison used three separate searches—presentation, interaction and
combined—with two iterations each and clean/identity batches of three episodes.
Its [offline evidence](../examples/checkout/interaction-offline-20261102/README.md)
is preserved unchanged. Those deterministic engineering controls do not establish
real-model steering or compare the conditions' effectiveness against LLMs.

To inspect or reproduce that historical implementation, use the archived
[documentation at commit bbae1b5](https://github.com/MayssaHH/WebHelix/blob/bbae1b5163c5f0294338cdc263740fa0196cba48/docs/INTERACTION_COMPARISON.md)
in a separate checkout of that revision. Its command examples apply to that revision,
not the current runner. Existing run archives and previous spending ledgers must not
be overwritten or reused as new authorization.

The underlying [interaction contract](../experiments/checkout/INTERACTION_CONTRACT.md)
still describes the permitted surfaces. The new discovery runner always selects
the combined surface; it does not launch three conditions.
