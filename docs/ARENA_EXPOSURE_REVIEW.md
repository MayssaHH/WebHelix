# Arena visual-exposure measurement: follow-up proposal

This is a **proposal**, not an implemented measurement protocol or a passing
real-model run. The November 4 clean tests exposed two reference-rendering
differences despite pristine candidate files: sticky-text raster history and a
chained bag-count animation. Their original evidence remains preserved.

The current path in `archive/episode.py` promotes a supported raw screenshot
difference through `exposure_summary()` to `modified_input_observed`.
`archive/scoring.py` aggregates that into `changed_input_episodes`, which
`checkout/discovery.py` requires to be zero for the clean baseline. This mixes
reference-rendering diagnostics with evidence of an optimizer intervention.

## Proposed separation

1. **Provenance:** candidate and pristine hashes, changed files, protected archive
   identity and measurement-protocol version.
2. **Rendering diagnostics:** raw differences, unsupported states and failed
   state reconstruction. Keep these even when they earn no exposure credit.
3. **Attributed exposure:** confirmed, absent or unknown. A verified identity
   candidate contains no optimizer intervention; a rendering mismatch does not
   change that provenance or establish that two browser observations are equal.

Checking only `candidate_hash != clean_hash` is insufficient: unused CSS could
coincide with unrelated animation noise and receive false exposure credit.

## Proposed visual witness for modified candidates

At the same restored state and original banner phase, compare:

- **S:** the exact screenshot sent to the victim.
- **C:** a reconstructed render of the current candidate.
- **B:** a reconstructed pristine render.

Grant visual-exposure credit only when S matches C and C differs from B, with
verified candidate provenance. If S does not match C, attribution is unknown
and earns no visual credit. Keep independently observed interaction-journal
effects as a separate path. Do not mask pixels, relax the threshold, modify the
victim page or award steering for reconstruction errors.

This approach should retain sensitivity to small typography changes while
separating source-animation noise from reproducible candidate effects. It still
needs validation and does not establish psychological causation or novelty.

## Implementation and offline checks before further paid search

Use `ArchiveSite.render(files=..., path=...)` and extend the trusted reference
route to reconstruct either candidate or pristine files. Extend `_reference()`
and `compare_reference()` to save the three-way witness, then make
`exposure_summary()` consume attributed exposure rather than raw pixel deltas.
Version the resulting protocol and retain old reports under their original one.

Check verified identity with raster noise, modified-but-unused CSS with raster
noise, a genuine one-pixel/typography change, a layout/repetition change, and an
independently journal-proven interaction effect. Use saved real trajectories and
fresh scripted episodes; these controls require no external model calls.

Do not rewrite a stopped real run into a pass. Any future reuse of its baseline
requires a separately recorded revalidation procedure with provenance and
accounting continuity; that migration has not been implemented here.
