# First real-model attempt — 2026-10-12 UTC

> Historical attempt. The funded [real-model experiment](real/README.md) subsequently
> ran two Codex iterations and reached **16/18** cumulative reservations. Budget and
> recovery instructions below describe the earlier state; do not execute them as
> a continuation of the now-finished experiment.

**Follow-up:** local `.env` authentication now works. A later run encountered HTTP
429 with no API credit; the cumulative budget is now **6/18**. See
[the follow-up record](DOTENV_ATTEMPT.md). Historical evidence below remains intact.

This follows the scripted evidence in `EXECUTION.md`. It is an authentication
failure, not a model-steering result or an optimization iteration.

Source revision: `e0f5992e1657e2ee4599189077fb87329e9751c9`.
Run: `/workspace/webhelix-runs/real-model-AsiEz7`.
AutoHelix remains pinned to `132fa0454963361fac669f35011b16f4263493c2`.

```bash
cd /workspace/webhelix
export PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright
export UV_CACHE_DIR=/workspace/.cache/uv
export AUTOHELIX_CODEX_CMD=/opt/codex/bin/codex
.venv/bin/webhelix provider-check
.venv/bin/webhelix experiment \
  --run-dir /workspace/webhelix-runs/real-model-AsiEz7
```

The initial provider check used only `GET /v1/models/gpt-6.1-sol`, which returned
200. Three fresh clean-page episodes then each attempted one Responses generation
request and received HTTP 401. The experiment exited 1 before identity diagnostics,
AutoHelix baseline, or optimizer invocation. Each outcome is `infrastructure_error`;
there are no model actions or confirmed selections. Token usage and dollar cost
are unavailable, not established as zero. Browser model settings were
`gpt-6.1-sol`, medium reasoning, 1,024 maximum output tokens and no retries.

A diagnostic established that the model lookup also returned 200 without
authentication. An empty `POST /v1/responses` returned `invalid_api_key`. Thus the
historical status file's `model_access: verified` field overstates the check; it
does **not** establish authenticated generation access. Preserve that raw artifact
alongside this correction. The updated preflight now performs both checks, stops
on this authentication failure without launching browsers, and reports actual
generation support as unverified. Failed generation responses now retain only
HTTP status and allowlisted diagnostic codes; free-form error messages are not
saved because they can echo credentials.

After the user reported replacing the key, another preflight still returned
`401 invalid_api_key`. Configuration metadata showed a saved `WEBHELIX_API_KEY`
binding with `api.openai.com` as its destination and no reported binding error.
This does not establish whether the newest value reached the live proxy. No
additional browser episode was launched during these authentication checks.

## Evidence

Under the run directory:

- `execution-start.json`: source and environment provenance.
- `experiment-console.txt` and `experiment-status.json`: failure and stop point.
- `clean-baseline.json`: three failed episodes, including the error denominator.
- `batches/clean-e48c3615be/episode-{0,1,2}/`: exact API request bodies without
  authentication, observations, screenshots and backend state.
- `authentication-diagnostic.json`: sanitized endpoint status and error codes.
- `budget.json`: **3 of 18 reservations used**, including failed attempts.

The sorting demonstration previously ran one real Codex CLI iteration with
`gpt-6.1-sol` and medium reasoning. Codex remains authenticated via ChatGPT;
the separate browser API credential is the current blocker for WebHelix.

## Resume after the credential works

Run from this checkout after API billing is enabled for the local `.env` key.
Do not paste keys into commands or chat. This creates new evidence while retaining
all six prior reservations. Do not run both directories concurrently.

```bash
cd /workspace/webhelix
source scripts/env.sh
export AUTOHELIX_CODEX_CMD=/opt/codex/bin/codex
bash scripts/with-dotenv.sh provider-check
# Continue only if the check above succeeds.
webhelix_previous=/workspace/webhelix-runs/real-model-resume-h31xz_ev
webhelix_resume=$(mktemp -d /workspace/webhelix-runs/real-model-resume.XXXXXX)
cp "$webhelix_previous/budget.json" "$webhelix_resume/budget.json"
printf '%s\n' "$webhelix_previous" > "$webhelix_resume/prior-attempt.txt"
bash scripts/with-dotenv.sh experiment --run-dir "$webhelix_resume" \
  > "$webhelix_resume/experiment-console.txt" 2>&1
```

The remaining 12 reservations exactly cover clean 3 + identity 3 + two candidates
of 3 episodes each. The identity measurements now also supply AutoHelix's initial
baseline, with explicit provenance and matching candidate/configuration checks.
This is reuse of existing evidence, not a new independent sample. Further failures remain counted;
they must not be hidden by resetting the ledger or increasing the limit.
