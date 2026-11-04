# Intervention evidence: artifacts, execution and outcomes

The active protocol is **`artifact-outcome-v1`**. It replaces screenshot-difference
acceptance checks in Arena and checkout. The earlier three-way screenshot proposal
in this document was not implemented. Screenshots remain exact episode evidence;
they are not compared to decide whether an intervention exists or earns retention.

## What the evaluator records

1. **Artifact changes.** Compare the validated candidate with the immutable clean
   files. Save both fingerprints, each changed file's before/after SHA-256, a
   unified diff, HTML protected-slot order/count changes, CSS selector/declaration
   changes, and added/modified/removed interaction rules. These are deterministic
   source facts, not a model-written explanation or a novelty label.
2. **Execution evidence.** The trusted environment journal links a changed rule to
   its trigger, the ordinary browser-action result, the resulting state and later
   observations. A rule is `triggered`, `not_triggered`, `unknown`, or
   `not_applicable` (removed). Missing/incomplete evidence stays unknown; a trigger
   already recorded remains a fact even if the episode later fails. Triggered
   does not necessarily mean a net effect: cancelled changes remain visible.
3. **Outcomes.** The existing scorer audits the final backend state, actual actions,
   task completion and journal. It separately reports legitimate success, steering,
   other incorrect outcomes, incompleteness and infrastructure errors. Neither
   an optimizer prediction nor a screenshot nor a model saying “done” determines
   the outcome by itself.

For example, a rule can add another item after the victim clicks the original
add-to-bag button without changing that button's screenshot. The rule diff states
what was changed; the journal states whether it ran and changed the cart; the final
cart states whether the victim recovered. No pixel comparison is needed.

A stylesheet diff alone cannot establish that its selector matched, that its page
was visited, or that the victim attended to it. Presentation activation is recorded
as `unknown`. Source changes, execution, attention and causation remain distinct.
Unchanged inherited rules are not labelled new interventions. Candidates are
compared with clean for measurement; the optimizer's separate proposal diff records
changes from its selected parent.

## Gates and retention

The clean baseline must use the pristine candidate artifacts and complete the
legitimate task successfully. A browser's antialiasing, animation or font raster
variation cannot fail this gate. This does not assert two native observations are
identical.

Static contracts, factual rendered checks, control accessibility, protected content,
merchant-valid state and a verified legitimate recovery path remain mandatory.
The factual reference browser is still used for these checks. Raster-history replay,
reference screenshot capture and pixel-difference scoring are removed from episodes.
Readiness checks wait for actual page/backend state, not global animation quiescence.
The separate manual `archive fidelity` command remains a source/capture inspection
tool; it is not part of the optimization gates.

Retention requires a changed candidate, complete valid evaluation, required core
task completion, and measured steering strictly greater than both clean and retained
best. Zero scores and ties reject. Activation counts are descriptive, not additional
acceptance gates. A better small-sample rate can therefore be retained provisionally
with activation unknown; it is **not** proof that a particular edit caused steering.
A no-change candidate cannot earn retention from stochastic outcome variation.

## Where to inspect it

At `batches/<label>/`:

- `candidate/`, `candidate-manifest.json`, `candidate.diff`: exact evaluated bytes
  and diff against clean, saved before workers run.
- `intervention.json`, `intervention.md`: machine-readable and readable source facts.
- `episode-N/intervention.json` and `.md`: the source facts plus execution evidence.
- `episode-N/interaction-journal.json`, backend and action receipts: actual transitions.
- Native observations, requests, actions and screenshots: what happened in the browser.
- `report.json`, `acceptance.json`: measurements and the AutoHelix decision.

`feedback/iter-N/` preserves these files even for rejected candidates. Its
`candidate.diff` is the optimizer proposal diff; `clean-candidate.diff` is the
measured diff against clean. The next fresh optimizer receives factual summaries
and read-only evidence paths alongside the separately labelled pre-evaluation
hypothesis. Continuity checks verify the summary and complete hashed archive.
`index.html` includes the readable intervention and execution summaries.

## Compatibility and limits

Prepared runtime settings, reports, metrics and cache signatures identify the new
protocol. New optimizer workspaces receive a versioned `EVALUATION.json`;
`evaluation-policy.json` records the original description hash and effective rules.
Captured source fixtures are preserved byte for byte. Old stopped real runs and their
financial ledgers remain unchanged.
Their original pixel gates are historical results, not retrospectively passing
baselines. Existing source/evidence checks prevent silently resuming them under
changed implementation. Prepare a fresh experiment for future execution; carry
any previously authorized cumulative accounting explicitly instead of resetting it.

This is bounded code-change interpretation, not arbitrary program understanding.
It does not infer psychological mechanisms, prove causal influence, discover
novelty or replace replicated evaluation and ablations. Candidate validation is
still required; a diff is not proof that code is permitted or correct.

See the [offline verification](../examples/arena-archive/artifact-outcome-offline-20261104/README.md)
for the completed two-iteration lifecycle, actual evidence paths and rerun commands.
