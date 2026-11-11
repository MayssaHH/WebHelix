# OpenCode preview deadlines

`wh-preview` gives the optimizer engineering feedback about its current candidate:
first the full rendered validation, then a fresh scripted browser journey that
produces observations and screenshots. Neither phase calls a victim model or
measures steering success.

The fact-coverage repair added checks of all 26 captured product pages. The old
120-second total preview limit could therefore interrupt a valid candidate after
validation had already passed. Offline measurement of the saved third candidate
completed both phases in about **145 seconds**. Giving each phase its own bounded
allowance fixes this specific timing problem without dropping checks.

## One preview, several enclosing deadlines

```text
Optimizer session: 600 seconds, including all tools and model requests
  └─ OpenCode bash: 300-second default
       └─ wh-preview HTTP client: 280-second socket-operation timeout
            └─ Host preview: 260-second overall deadline
                 ├─ Rendered validation: at most 120 seconds
                 └─ Fresh scripted journey: at most 120 seconds
```

The overall preview deadline includes snapshotting, browser work, cleanup and
artifact preparation. Each worker receives the smaller of its phase allowance
and the remaining overall time after reserving two seconds for termination. The
260 seconds are not added to the optimizer's 600-second session.

The HTTP setting is a socket-operation timeout, not a separate wall-clock cap.
The host deadline and enclosing native shell timeout provide the execution
bounds. OpenCode's default is set through
`OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS=300000`; the optimizer prompt also
asks for `timeout=300000` when calling `wh-preview`. An explicit per-call shell
timeout can override that default, so it is not an enforced minimum.

## Do not begin a preview that cannot fit

Before starting browser work, WebHelix checks both the remaining optimizer
session time and the remaining whole-run time. With the current defaults, both
must have at least **340 seconds** left:

```text
260 seconds for the preview + 20 for transport + 60 for completion = 340 seconds
```

The completion reserve leaves time to finish the proposal and note. It does not
guarantee that every possible model response will finish in that time. At most
three previews may be requested in a session; this count limit does not promise
that three full-length previews fit.

If too little time remains, the helper receives `preview_not_started` with the
reason and measured remaining time. No browser starts, `validation.valid` is
`null`, and this is not a candidate rejection. The optimizer can still finish its
note using existing evidence; normal model-request and spending limits continue
to apply.

## Failures remain visible

- A protected fact violation remains `invalid_candidate`, and the scripted
  journey does not start.
- A worker timeout or session cancellation remains `infrastructure_error`, not
  an invalid candidate or a successful preview. Cancellation stops the worker
  process group and retains the available diagnostic evidence.
- Per-phase receipts record the configured limit, effective allowance, elapsed
  time, outcome and failure phase. Admission receipts record why a preview was
  started or declined.
- A final evidence-write failure must not leave a saved success manifest. A
  failed diagnostic write also must not replace the original worker error.

These controls concern preview execution. They do not change the research task,
authoring permissions, fact-preservation rules, candidate scoring, optimizer
request limits, victim episode limits or API spending authorization.

## Configuration and evidence

Current optimizer example configurations use:

```json
{
  "preview_timeout_seconds": 260,
  "preview_validation_timeout_seconds": 120,
  "preview_journey_timeout_seconds": 120,
  "preview_finalization_reserve_seconds": 60
}
```

An older configuration explicitly setting `preview_timeout_seconds` to 120
still means a 120-second total. Historical run files are not rewritten. Prepare
a new run with the updated configuration to use the new allowances.

The helper is packaged inside the OpenCode Docker image. Runtime preflight checks
its SHA-256 against the repository and refuses a stale helper, even when the CLI
version matches. Rebuild the image using the
[runtime setup instructions](OPENCODE_CHECKOUT.md#installation) after updating
that file. The pinned OpenCode version remains 1.19.4.

[Offline verification evidence](../examples/arena-archive/preview-deadlines-20261111/README.md)
records the completed measurements and failure controls. These are engineering
checks with scripted browser policies and, where the native harness is exercised,
an offline provider fixture. They are not new optimizer research proposals or
real-victim results. Measured completion on these pages does not guarantee that
every future candidate will finish within the bounds.

Implementation: [timing and admission](../src/webhelix/opencode/timing.py),
[backend wiring](../src/webhelix/opencode/backend.py),
[preview phases](../src/webhelix/opencode/preview.py),
[gateway lifecycle](../src/webhelix/opencode/gateway.py),
[worker cleanup](../src/webhelix/checkout/evaluate.py),
[native runtime](../src/webhelix/opencode/runtime.py), and
[packaged helper](../experiments/checkout/opencode-runtime/wh-preview.py).
Regression checks are in [the OpenCode tests](../tests/opencode/).
