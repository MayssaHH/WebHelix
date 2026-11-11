# Arena validation: offline diagnosis and reference-state repair

This follow-up investigates the intermittent validation failure in the
[three-proposal Arena pilot](../examples/arena-archive/sol-luna-three-iterations-retry-20261104/README.md).
It makes **zero optimizer or victim model calls**. The saved candidate, merchant
capture, task, scoring, and original experiment outcomes remain unchanged.

## What failed

The third candidate's original recovery check stopped just after removing the
substituted Baby Blue bouquet. The backend cart was empty. The candidate page
correctly hid its empty subtotal panel, but the validator reported:

```text
Source fact hidden or changed: source-fact-0
```

The old failure capture saved the candidate page, but not the reference page or
the exact fact arrays used for comparison. Therefore the historical evidence
cannot establish the original reference's state or precise execution timing.

## A matching failure mechanism reproduced

A controlled local test using the unchanged captured merchant scripts reproduced
the exact error:

1. A merchant controller reads an empty-cart cookie as the string `"0"`.
2. Fetching the current cart produces the number `0`.
3. A later controller constructor can read the cookie again and overwrite that
   number with the string, depending on script/request timing.
4. The merchant drawer treats the two values differently. The reference can show
   an empty subtotal panel while the candidate, after actual removal, hides it.
5. Comparing those different interface states wrongly blames the candidate for
   omitting a protected fact.

The controlled test delays delivery of the original consent-controller script
until the initial cart fetch finishes. It changes scheduling, not merchant bytes.
With the original replay bridge, comparison raises the exact error above. With
the repaired bridge, the same check passes. The local backend records no cart
mutation events in this controlled comparison.

This establishes a real, reproducible defect matching the historical symptom.
It does not recover the missing historical reference DOM or prove that this was
the only possible cause of that incident.

## Repair and diagnostics

The protected bridge's `__webhelixReplayRestore` now refreshes the **read-only
reference** from the local cart backend after initialization and before restoring
its interface state. Empty counts are therefore normalized before the drawer is
opened. The method remains inaccessible on the candidate page. No candidate
interaction, victim instruction, merchant asset or factual assertion is relaxed.

Future rendered-validation failures preserve:

- The original exception, candidate hash, backend state and preceding scripted
  recovery actions.
- The exact candidate/reference fact arrays used by the failed comparison.
- The intended reference state, plus later diagnostic state from both pages,
  including cart-count value and type.
- Candidate and reference HTML and screenshots, with capture failures recorded
  separately. Screenshots are evidence, not an acceptance gate.
- Phase timings for browser startup, navigation, readiness, reference restoration,
  fact comparison, scripted actions and cleanup. Operations outside a named phase
  have only a last-known-phase record, not an invented precise attribution.

The failure receipt is written before additional browser capture. Later diagnostic
write or screenshot errors do not replace the primary validation error. A worker
killed before capture finishes may still leave partial evidence; running phase
records must not be interpreted as completed checks.

Arena evaluation cache signatures now include the protected validation/replay
implementation identity. A pre-repair verdict cannot silently serve as a verdict
under the new validator. Normal discovery already freezes runtime source; old
experiments and their ledgers remain immutable, and future searches need a fresh
prepared run.

## Offline evidence

The saved third candidate was replayed without changing its files. Sequential
checks produced these results:

| Check | Result |
|---|---|
| Controlled reference race, original bridge | Exact `source-fact-0` error reproduced |
| Same controlled race, repaired bridge | Fact comparison passed; no backend mutations |
| Clean archived website | Passed; legitimate recovery in six scripted actions |
| Saved third candidate, three fresh recovery checks | All passed; legitimate recovery in thirteen actions each |
| Deliberately hidden protected facts | Rejected; paired failure evidence saved |
| Deliberately clipped protected facts | Rejected; paired failure evidence saved |

These are deterministic engineering checks, not additional LLM episodes or
steering measurements. The previous zero-steering results are unchanged.

The portable regression tests use small synthetic controller stubs with the
actual protected bridge. They cover the string/number disagreement, reference-only
access, unchanged candidate state, genuine hidden/changed facts, interrupted
diagnostics and validation-cache compatibility. They do not redistribute merchant
source.

Final focused verification: **153 tests passed in 92.48 seconds**, covering the
archive, optimizer preview and shared evaluation paths. Python lint, JavaScript
syntax and whitespace checks also passed. Full saved-archive replays preceded
the final logging/cache safeguards; the focused suite verifies the final source.

## Separate preview timeout finding

The earlier optimizer previews shared a **120-second deadline** between rendered
feasibility and a scripted Browser-Use journey. Their feasibility receipts reported
about 51–53 seconds; process startup and other work left approximately 64–66 seconds
for the journey. The parent timed out around completion. One partial child receipt
recorded legitimate recovery and cleanup, but the parent deadline still failed.

Those timeouts remain infrastructure errors. This change adds durable per-phase
allowances, elapsed times and worker outcomes; it does not increase the deadline,
retry the preview, or reinterpret a partial result as success. It does not establish
that all previews now fit within the existing allowance. Timing evidence should
guide a separate decision about preview work or its configured allowance.

## Inspect and reproduce

From the repository root, after installation:

```sh
source scripts/env.sh
uv run --locked pytest -q tests/archive tests/opencode/test_preview.py tests/checkout/test_evaluate.py
```

For this workspace's existing Chromium cache, the executed equivalent is:

```sh
PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright \
  .venv/bin/pytest -q tests/archive tests/opencode/test_preview.py tests/checkout/test_evaluate.py
```

Full private evidence is under
`/workspace/webhelix-runs/arena-validation-diagnosis-20261111/`: controlled
before/after reproduction, paired page captures, `replay_saved.py`, per-case
worker receipts, timing files, test logs and hashes. The replay requires the
preserved November capture, which is not included in Git. The original failed
attempt and the earlier successful revalidation artifacts remain in their original
directories. Only sanitized summaries are published.

Relevant implementation: [reference restoration](../src/webhelix/archive/replay.js),
[rendered validation](../src/webhelix/archive/episode.py),
[failure diagnostics](../src/webhelix/archive/validation.py),
[preview phase accounting](../src/webhelix/opencode/preview.py), and
[cache identity](../src/webhelix/checkout/evaluate.py).
