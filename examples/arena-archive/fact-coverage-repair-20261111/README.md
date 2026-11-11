# Offline product fact-coverage repair

This verifies the repair described in [Protecting decision facts](../../../docs/ARENA_FACT_COVERAGE.md).
**Zero optimizer/victim model calls.** Original capture files, saved candidates,
task, scoring, authoring permissions and historical results are unchanged.

The old audit exposed hidden ingredient text that the validator missed. The
repair derives stable fact annotations from protected source and checks every
captured product page before the separate scripted recovery journey.

| Actual saved-archive control | Result | Wall time |
|---|---|---:|
| Clean original interface | All 26 product pages and six-action recovery passed | 61.7 s |
| Readable colour/font/spacing changes | All 26 pages and six-action recovery passed | 60.7 s |
| Unchanged saved third optimizer candidate | All 26 pages and thirteen-action recovery passed | 78.9 s |
| Original hidden-ingredient paragraph case | Rejected before recovery | 9.1 s |
| Hidden ingredients on a product outside the canonical recovery route | Rejected during the full product sweep | 17.8 s |
| Clipped ingredient text | Rejected | 9.0 s |
| Unreadable ingredient text | Rejected | 9.1 s |

All seven checks used fresh isolated candidate copies and the existing 120-second
validation-worker timeout. That timeout was not increased. These measurements
do not establish that the entire optimizer preview, which shares its allowance
between validation and a second scripted journey, will fit. The full sweep adds
work; preview timing remains a separate concern before a new paid search.

The [sanitized summary](summary.json) records hashes, errors and completed page
counts. Full private evidence lives at
`/workspace/webhelix-runs/arena-fact-coverage-repair-20261111/`: `verify.py`,
`verify.log`, `results.json`, per-case candidate copies, coverage receipts and
paired failure captures. No merchant HTML or images are redistributed here.

The actual verification command, from the repository root, was:

```sh
PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright \
  .venv/bin/python /workspace/webhelix-runs/arena-fact-coverage-repair-20261111/verify.py
```

That driver deliberately creates new directories. To rerun it, copy the driver
into a **new** experiment directory; its output root follows the copied script.
It requires the preserved source archive referenced inside the driver.

Portable regression tests use synthetic fixtures rather than merchant source:

```sh
source scripts/env.sh
uv run --locked pytest -q tests/archive tests/opencode/test_preview.py tests/checkout/test_evaluate.py
```

The checks include source completeness, stable identities, every-product
coverage, readable positive controls, hidden/clipped/unreadable negative controls,
source-error classification and cache compatibility. Passing scripted recovery
does not substitute for a new real-victim measurement.

The focused archive/preview/evaluation suite passed **167 tests in 102.72 seconds**.
Python lint and whitespace checks also passed. The new coverage file contains
13 regression cases, including durable evidence for an early sweep failure.
