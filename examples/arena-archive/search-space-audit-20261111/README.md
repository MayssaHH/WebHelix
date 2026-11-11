# Arena optimizer search-space audit

**No model calls. No production behavior, saved experiment or captured source
was changed.** This audit checks the current authoring contract and reads the
latest three-proposal Sol/Luna experiment.

Read the [full audit](../../../docs/ARENA_SEARCH_SPACE_AUDIT.md) for capabilities,
limits, feedback, scoring and exact source references.

The main findings are:

- Five files support passive HTML/CSS plus bounded cart interaction rules.
  The optimizer is not restricted to layout, but cannot freely program a website.
- All three historical proposals changed only `interaction.json`, using fewer
  than half or exactly half of the 20 available model requests. They explored
  an extra item and two substitution variants, with no measured steering.
- A private browser negative control exposed a validation coverage defect:
  task-relevant ingredient text could be hidden while the current checks passed.
  This is not a legitimate research candidate or a steering result.
- Large inline contexts and preview timeouts are practical issues to examine;
  this audit does not prove that either caused the narrow exploration.

Evidence included here:

- [summary.json](summary.json): sanitized browser finding, source hashes and
  historical proposal/accounting facts.
- [static-probes.json](static-probes.json): 42 static capability probes, all with
  the expected result. Static acceptance does not imply rendered validity.
- [audit_contract.py](audit_contract.py): rerunnable static audit. It reads the
  protected capture, builds cases in memory and does not execute candidate code.

After installing WebHelix, run from the repository root with an existing faithful
capture and a **new** output filename outside the capture:

```sh
source scripts/env.sh
uv run --locked python examples/arena-archive/search-space-audit-20261111/audit_contract.py \
  --archive-root /workspace/webhelix-runs/arena-sol-luna-three-iterations-retry-20261104/archive \
  --output /workspace/webhelix-runs/arena-search-space-audit-NEW/static-probes.json
```

The original invocation used `.venv/bin/python` with those arguments and output
`/workspace/webhelix-runs/arena-search-space-audit-20261111/static-probes.json`.
Raw browser evidence and the browser probe are in that directory's `fact-coverage/`
subdirectory. It includes paired HTML/screenshots, assertion results and the
six-action scripted feasibility receipt. The archive, negative-control candidate
and merchant content are not redistributed here.

The browser probe used local Playwright with
`PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright`. It made zero model
requests and verified the original capture and candidate remained unchanged.
