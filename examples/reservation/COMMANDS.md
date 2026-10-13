# Reservation correction: executed commands

Working directory: `/workspace/webhelix`.
Implementation frozen for this run at `b38c43a48a2c52f150469041ec9adcbd2c30b35a`.
AutoHelix dependency: `132fa0454963361fac669f35011b16f4263493c2`.
Original AutoHelix checkout, sorting example and v1 evidence were preserved.

Environment used:

```bash
export UV_CACHE_DIR=/workspace/.cache/uv
export PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright
```

Validation executed before real inference:

```bash
uv run --locked webhelix reservation controls \
  --run-dir /workspace/webhelix-runs/reservation-controls-initial
uv run --locked pytest -q \
  > /workspace/webhelix-runs/reservation-tests-full.txt 2>&1
uv run --locked ruff format src/webhelix/v2 tests/v2
uv run --locked ruff check src/webhelix/v2 tests/v2
uv run --locked pytest -q tests/v2/test_reservation.py \
  -k 'real_autohelix_rejects or multimodal or strict_gate' \
  > /workspace/webhelix-runs/reservation-integration-final.txt 2>&1
```

The full suite passed 50 tests. After adding durable proposal snapshots, version/hash
records and the portable report, the three affected integration tests passed again.
Earlier development failures are retained in `reservation-tests-initial.txt`; the
browser control test needed worker-process isolation from Playwright's process-wide
event-loop state. The application's actual episode workers were already isolated.

Codex preflight, run from `/workspace/webhelix-runs/isolation-v2-probe/work`:

```bash
/workspace/webhelix/scripts/codex-isolated.sh --version
/workspace/webhelix/scripts/codex-isolated.sh login status
```

These commands and the real experiment used the platform-approved outer execution
path because the enclosing cloud sandbox prevents Bubblewrap UID mappings. The
optimizer itself remains inside Bubblewrap. `isolation.json` records the separate
write-denial/credential-masking probe performed before any paid episode.

Actual real experiment command:

```bash
bash scripts/with-dotenv.sh reservation experiment \
  --run-dir /workspace/webhelix-runs/reservation-real-20261013-v2 \
  > /workspace/webhelix-runs/reservation-real-20261013-v2.console.txt 2>&1
```

The launcher loads the existing ignored `.env` privately; its contents are not
recorded. `project-start.json` contains the effective AutoHelix configuration.
Victim settings and protected-source hashes are in `status.json`.
The evaluator's worker commands and exact model requests are saved per episode.
No optimizer or victim model was substituted. Both request `gpt-6.1-sol`, medium.

Versions checked: Git 2.52.0, uv 0.12.19, Bubblewrap 0.12.0, Playwright 1.44.0,
Codex CLI 0.159.0-alpha.3. Python and Python dependency versions are in `status.json`.

Budget: this new experiment has its own protected maximum of 30 episode reservations.
The completed v1 ledger remains 16/18. No automatic retry or ledger reset occurs.
All generation calls are authorized by the user's approval to implement and execute
the correction. Token usage is recorded separately for victim and optimizer;
no dollar cap is enforced, and missing dollar accounting must not be called free.
