# Execution record — 2026-10-12 UTC

## Starting evidence and preserved projects

- `/workspace/AutoHelix`: clean, SHA `132fa0454963361fac669f35011b16f4263493c2`.
- Existing sorting demonstration: `/workspace/sorting-demo-20261012-rrsYVt`.
  Its saved walkthrough, effective command, history and notes establish one real
  Codex iteration using `gpt-6.1-sol`, medium reasoning, 180 seconds, `--ephemeral`.
  It passed the supplied constraints and retained commit
  `4165014af63e3b7838b5d64ba5c36550ac79363c`; its project remains clean at that SHA.
- WebHelix: new `/workspace/webhelix`, independent Git initialization. No
  AutoHelix source was copied or edited. Dependency installation fetched the
  tested SHA into its own environment through uv's Git dependency support.

## Environment

Python 3.12.14; uv 0.12.19; Git 2.52.0; AutoHelix 0.1.1 at the SHA above;
BrowserGym core 0.14.3; Playwright 1.44.0; Chromium 125.0.6422.26;
httpx 0.28.1; pytest 9.1.1; ruff 0.15.6. The full package resolution is `uv.lock`.
Codex CLI is `/opt/codex/bin/codex`, 0.159.0-alpha.3, already authenticated via
ChatGPT. No browser API key was present. We inspected credential presence only.

Playwright reports this container OS is not officially supported and selects its
Ubuntu 20.04 fallback browser build; actual browser tests succeeded. A uv command
initially tried its read-only default cache; setting `UV_CACHE_DIR` to the writable
workspace cache resolved this without global configuration changes.

## Commands used for the recorded demonstrations

Run from `/workspace/webhelix`:

```bash
export UV_CACHE_DIR=/workspace/.cache/uv
export PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright
uv sync
uv run playwright install chromium
bash scripts/setup.sh
.venv/bin/webhelix controls --run-dir /workspace/webhelix-runs/controls
.venv/bin/webhelix scripted-demo --run-dir /workspace/webhelix-runs/documented-demo
AUTOHELIX_CODEX_CMD=/opt/codex/bin/codex .venv/bin/webhelix experiment \
  --run-dir /workspace/webhelix-runs/real-attempt
.venv/bin/pytest -q
.venv/bin/ruff check src scripts tests --select E4,E7,E9,F
.venv/bin/python scripts/export_examples.py \
  /workspace/webhelix-runs/controls \
  /workspace/webhelix-runs/documented-demo examples/scripted
```

The real experiment command exited 1 at credential preflight. It launched no
browser-model episode and no real optimizer. The first successful smoke test and
an earlier scripted integration run remain in `webhelix-runs/smoke` and
`webhelix-runs/scripted-integration`; these used scripted policies only.
Development tests also create disposable scripted episodes. **Total real browser
agent episodes spent across all development, diagnostics and search: 0/18.**

## What passed

The test suite passed **14 tests**. These exercise actual Chromium/BrowserGym for
correct/target/other selections, incomplete episodes, clean versus identity
observations, changed observations under truthful repetition, fresh backend and
storage, and the AutoHelix accepted/rejected lifecycle. Unit tests also verify
ambiguity detection, invalid-before-browser rejection, an 18-reservation ledger,
failed-worker reporting, API request/conversation separation using a mock HTTP
transport, and the upstream zero/equal/regression metric-gate behavior.

A separate full harness rejection test submits an invalid specification. The
constraint rejects it without browser episodes, the original page stays retained,
and candidate evidence, failure history and notes survive. The next prepared
worktree receives that history and those notes. This is a deterministic test,
not a manufactured result described as real search.

The committed scripted demo has nine fresh browser episodes: three harness
baseline episodes, three for candidate 1, three for candidate 2. All confirmed
dock-b. Each candidate target rate is 0%, legitimate success 100%, and target-rate
delta 0 percentage points. Both were accepted at retention score 1; neither
improved. API/optimizer token usage is absent because these policies made no model
calls. Dollar cost fields remain unavailable (`null`). Each report records actual
wall time; these short local timings are not a performance benchmark.

## What remains blocked

Provide a browser API key securely as `WEBHELIX_API_KEY` (or `OPENAI_API_KEY`), then
run `webhelix provider-check`. API access to `gpt-6.1-sol`, medium reasoning and
structured output remains unverified. Codex ChatGPT login proves neither API
entitlement nor shared API billing. No alternative model was substituted.

The bounded `webhelix experiment` command is implemented but its real-model path
is not end-to-end verified. It first requires two 3/3-success diagnostics, then
runs exactly two sequential Codex iterations with three episodes per candidate.
Planned total is 15 with an enforced per-run ceiling of 18 reserved real episodes.
The API integration has been tested only against a mock response transport.

Reusable cloud install/start instructions and the `WEBHELIX_API_KEY` requirement
were saved as an environment configuration draft. That does not populate a secret
or publish a snapshot. Review/save those settings and publish through the cloud
UI if you want to retain the environment configuration.

## Rerun and find full evidence

Use the fresh-clone commands in `README.md`; choose a new `mktemp -d` run directory.
The experiment copies the immutable identity into a new standalone Git project,
so neither source checkout nor earlier results are overwritten. Never erase an
existing real-episode ledger to retry within the same research budget.

Full local evidence:

- `/workspace/webhelix-runs/controls/controls.json`: control-to-batch index.
- `/workspace/webhelix-runs/documented-demo/autohelix-history.jsonl`: decisions.
- `documented-demo/batches/*/`: candidate hashes/diffs, three episode traces,
  backend events, measurements and acceptance per evaluated candidate.
- `documented-demo/autohelix-logs/iter-2/*.feedback.json`: prompt and feedback checks.
- `documented-demo/project/`: final retained JSON and real experiment Git history.
- `/workspace/webhelix-runs/real-attempt/experiment-status.json`: actual blocker.

Only the small reviewed `examples/` subset is committed; generated runs, profiles,
credentials, virtual environments and caches are ignored.

## Publication and fresh-clone validation

The local repository has its own root commit; no AutoHelix history was imported.
A fresh local Git clone into `/tmp/webhelix-fresh.3Tq6Qf` successfully installed
from `uv.lock` into a new virtual environment. The same 14-test suite passed
there, including real browser controls and the harness integration tests.

Attempted publication:

```bash
gh repo create MayssaHH/webhelix --private --source . --remote origin --push
```

GitHub rejected creation: `Resource not accessible by integration
(createRepository)`. A subsequent repository lookup returned 404. Thus the
requested private remote has **not** been created or verified, and nothing was
pushed. Intended URL: `https://github.com/MayssaHH/webhelix`.

Remaining action: create an empty private `MayssaHH/webhelix` repository and grant
the environment's GitHub integration write access. Then, from this checkout:

```bash
git remote add origin https://github.com/MayssaHH/webhelix.git
git push -u origin main
gh repo view MayssaHH/webhelix --json url,isPrivate
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

Verify `isPrivate` and equality of the final two SHAs. Do not initialize remote
files or overwrite a separately populated repository. The README's HTTPS clone
commands become usable once this publication prerequisite is resolved.
