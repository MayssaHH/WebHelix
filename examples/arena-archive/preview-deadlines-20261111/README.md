# Offline preview-deadline verification

The broader fact checks were correct, but the optimizer preview still shared
120 seconds between validation and a separate scripted browser journey. The
saved third candidate needs about 145 seconds for that complete path. This
verification measures both phases, then exercises the actual OpenCode helper.

**No model API calls, new research proposals or paid victim episodes were run.**
The browser uses deterministic controls. Native OpenCode receives scripted
provider responses through `httpx.MockTransport`, with synthetic zero usage.
These results establish engineering completion, not steering effectiveness.

## Results

| Check | Elapsed time | Result |
|---|---:|---|
| Clean page, standalone scripted journey | 38.383 s | Legitimate task completed |
| Saved candidate 3, standalone scripted journey | 69.370 s | Legitimate task completed |
| Clean page, full preview | 94.929 s | Validation and fresh journey completed |
| Saved candidate 3, full preview | 144.983 s | Validation and fresh journey completed |
| Hidden ingredient fact | 11.621 s | Invalid candidate; no journey started |
| Deliberate 1-second validation deadline | 2.087 s | Infrastructure timeout; no journey started |
| Cancellation requested after 3 seconds | 3.237 s | Worker cancelled; infrastructure error, no journey started |
| Actual OpenCode → helper → full preview → screenshot read | 151.660 s | Passed; preview itself took 142.103 s |

Both valid full previews completed the fact sweep across all **26 captured
product pages**. The candidate's validation worker took 80.196 seconds and its
journey worker took 63.814 seconds. Their combined time explains why merely
checking that validation finishes within 120 seconds was insufficient.

The new bounds are 120 seconds per browser phase inside 260 seconds overall,
with helper HTTP timeout 280 seconds and native shell default 300 seconds.
The optimizer session remains 600 seconds. A preview requires at least 340
seconds remaining in both session and run, including transport and note-writing
reserves. See [the deadline guide](../../../docs/OPENCODE_PREVIEW_TIMING.md).

The native check deliberately **omitted** a per-call shell timeout. OpenCode's
actual tool description advertised the new 300,000 ms default, the helper
recorded 280 seconds, and the preview image reached the offline provider fixture.
The source and copied candidate hashes remained identical. The four provider
requests in that fixture are local test exchanges, not four paid model calls.

Regression tests also cover late-preview refusal without stopping note-writing,
session cancellation and process-group termination, original worker-error
preservation, failure to write the final receipt, stale packaged helpers, and
explicit older configurations retaining their 120-second total.

The combined OpenCode, archive and worker-evaluation suite passed **342 tests in
130.07 seconds**. Ruff and whitespace checks also passed. A final read-only code
review found no remaining material issue in this repair.

## Evidence

- [summary.json](summary.json): measured timings, phase receipts, candidate
  hashes, coverage counts and runtime identity.
- [native-timeout-evidence.json](native-timeout-evidence.json): relevant native
  tool description and helper settings.
- Full local evidence: `/workspace/webhelix-runs/arena-preview-deadlines-20261111/`.
  `standalone/` holds separate journeys; `full/` holds the two full previews and
  failure controls; `native/` holds actual CLI, helper and gateway evidence.
  Each preview retains its frozen candidate, `preview.json`, worker logs and
  available browser artifacts. Raw captured merchant pages/images stay outside Git.

The original archive and saved candidate remain under
`/workspace/webhelix-runs/arena-sol-luna-three-iterations-retry-20261104/`.
No historical results or candidate files were rewritten.

## Commands and reproduction

The local diagnostic drivers were run sequentially from `/workspace/webhelix`:

```bash
export PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright
.venv/bin/python /workspace/webhelix-runs/arena-preview-deadlines-20261111/measure_journeys.py
.venv/bin/python /workspace/webhelix-runs/arena-preview-deadlines-20261111/full_previews.py
env -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  DOCKER_HOST=unix:///var/run/docker.sock \
  .venv/bin/python /workspace/webhelix-runs/arena-preview-deadlines-20261111/native_check.py
```

The first two standalone journeys used a diagnostic 180-second limit to measure
completion. Production phase limits were selected afterward; they are 120
seconds each. The saved candidate was copied unchanged for the native check.
These commands record what ran; do not rerun the diagnostic drivers in place.
Choose fresh output paths to preserve the recorded evidence. The native driver
also enforces this with an explicit existing-directory check.

The packaged helper was refreshed on the previously verified OpenCode 1.19.4
image using an offline Docker layer. Preflight verified the unchanged CLI version
and new helper hash. Initial build attempts and their logs are retained locally:
the first hit a read-only Buildx configuration directory; a full rebuild with
network disabled could not reinstall Git. The successful helper-only refresh
used a workspace Buildx directory, preserved Docker credentials/configuration,
and needed no network. The repository Dockerfile and CLI pin did not change.

Portable regression checks use generated test fixtures, not the private capture:

```bash
uv run --locked pytest -q tests/opencode tests/archive tests/checkout/test_evaluate.py
uv run --locked ruff check src/webhelix/opencode src/webhelix/checkout/evaluate.py \
  tests/opencode tests/checkout/test_evaluate.py \
  experiments/checkout/opencode-runtime/wh-preview.py
```

To repeat a full preview against an available local capture, use a **new output
directory** and the existing candidate directory. This command cannot call an LLM:

```python
from webhelix.opencode.preview import preview

result = preview(
    "/path/to/candidate",
    "/path/to/new-preview-output",
    condition="archive",
    settings={"environment": "arena-archive", "archive_root": "/path/to/archive"},
)
print(result["status"], result["timing"])
```

A future candidate can still exceed a phase limit. That remains an explicit
infrastructure failure; passing these engineering checks does not guarantee
future completion or justify treating scripted behavior as real-victim evidence.
