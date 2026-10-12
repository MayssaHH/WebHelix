# Local `.env` attempt — 2026-10-12 UTC / 2026-10-13 Beirut

> Historical attempt. The funded [real-model experiment](real/README.md) subsequently
> ran two Codex iterations and reached **16/18** cumulative reservations. Budget and
> recovery instructions below describe the earlier state; do not execute them as
> a continuation of the now-finished experiment.

Source revision: `d1be1d948a40893e1ceb6a5dbf5348cdf1bcd5df`.
Run: `/workspace/webhelix-runs/real-model-resume-h31xz_ev`.
Prior evidence: `/workspace/webhelix-runs/real-model-AsiEz7`.

The user supplied a key privately in the ignored local `.env` file. The explicit
launcher clears inherited credential overrides for its child process and loads
the file through uv. Responses authentication preflight passed. Codex remained
authenticated using ChatGPT. No key or authentication header is recorded here.

```bash
cd /workspace/webhelix
export PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright
export UV_CACHE_DIR=/workspace/.cache/uv
export AUTOHELIX_CODEX_CMD=/opt/codex/bin/codex
bash scripts/with-dotenv.sh provider-check
# Before launching, the new run directory received the previous budget.json,
# retaining its three reservations, plus prior-attempt.txt and execution-start.json.
bash scripts/with-dotenv.sh experiment \
  --run-dir /workspace/webhelix-runs/real-model-resume-h31xz_ev \
  > /workspace/webhelix-runs/real-model-resume-h31xz_ev/experiment-console.txt 2>&1
```

## Observed results

The three clean-page episodes each sent one generation request and received HTTP
429. All are `infrastructure_error`, with no model action, no selected/confirmed
product, and no returned token usage. The classifier preserved HTTP status but
did not recognize the error type/code, so it does not establish a specific 429
subtype. The user subsequently confirmed that the API account had no credit.
A diagnostic request with invalid input and zero output budget returned a normal
HTTP 400 parameter error; it requested no valid generation and launched no browser.

Browser settings: OpenAI Responses, `gpt-6.1-sol`, medium reasoning,
1,024 maximum output tokens, four actions per episode, zero automatic retries.
Generation support for these model settings remains unverified. The optimizer
was configured for Codex CLI with the same model and reasoning, but was not invoked.

The experiment exited 1 before identity diagnostics, AutoHelix baseline or search.
No candidate was generated, evaluated by AutoHelix, accepted or rejected. Zero
success counts in the report reflect infrastructure failures; they are not
evidence that the victim cannot solve the task or that a manipulation worked.

Recorded episode wall times: 3.1374 s, 2.0599 s, 2.0650 s; total 7.2623 s.
Usage arrays are empty. Aggregate token fields are zero sums of empty data, not
verified billing measurements. Dollar cost remains unknown (`null`).

## Evidence and remaining budget

Under the run directory, inspect `execution-start.json`, `experiment-status.json`,
`experiment-console.txt`, `clean-baseline.json`, and `batches/clean-*/episode-*/`.
Each episode preserves its exact request body without credentials, observation,
screenshot, sanitized HTTP response status and backend state. The shared
`budget.json` has **six reservations**: the three older 401 attempts and three
new 429 attempts. **12 of the maximum 18 remain.**

After this run, the evaluator was changed to stop an API batch immediately after
its first infrastructure failure. Unstarted episodes are explicitly recorded.
The real experiment now reuses its three successful identity measurements as
AutoHelix's identical initial baseline, with byte/model/fixture checks and a
`baseline-reuse.json` record. This removes a duplicate three-episode batch; it
does not create additional measurements or change candidate scoring/acceptance.
Scripted integration demonstrations retain their original separate baseline.
Validation after these changes: **28 tests passed in 44.07 seconds**, including
actual BrowserGym controls, scripted AutoHelix acceptance/rejection, cache reuse
and mismatch rejection, and the one-reservation infrastructure-failure path.
Ruff and `git diff --check` passed. These checks made no real model calls.

After credit is available, use the [budget-preserving recovery commands](API_ATTEMPT.md#resume-after-the-credential-works).
The remaining schedule is 3 clean + 3 identity + 3 for each of two candidates.
Any further failure remains counted and may prevent completing that schedule
within the authorized budget; the ledger must not be reset.
