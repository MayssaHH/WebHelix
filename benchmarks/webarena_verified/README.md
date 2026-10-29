# WebArena-Verified: Shopping Admin deployment pilot

**Historical deployment result: Shopping Admin ran in this workspace; two scripted
controls passed the unchanged upstream evaluator and backend reset was verified.** This page records the original
deployment checks. The later [optimization pilot](../../experiments/shopping_admin/README.md)
adds the HTML/CSS layer and two completed scripted AutoHelix iterations.
No benchmark LLM baseline or real-model optimization has run.

Historical development branch: `feature/benchmark-search`, based on
`2c74cb027ac52b5638adec3a1c5d4a66a1c06fdb`.
The current browser integration has since migrated to Browser-Use. The results
and version table below retain their original BrowserGym provenance. Current
setup uses the root dependency lock and [main README](../../README.md). No paid
Browser-Use benchmark result is established by these historical controls.

See the [recorded example](../../examples/benchmark-admin/README.md) for screenshots,
actual browser actions, and a result summary. Raw evidence is at
`/workspace/webhelix-runs/benchmark-admin-20261020/` on the experiment machine.

## What ran

| Control | Observed result |
| --- | --- |
| Task 458: “Reduce the price of the product on the current page by $5” | Two browser actions changed $32 to $27 and saved. Upstream score **1**; independent database read confirmed $27. |
| Unsupported success claim for task 458, with no browser action | Upstream score **0**. A completion claim alone is insufficient. |
| Recreate the owned container from its pristine image | Different container ID; database price returned to $32. |
| Reopen the clean product page after reset | Observations equal after removing only temporary DOM IDs and BrowserGym IDs. Values, labels, text, control order, and options are retained. |
| Task 709: “Show the orders report from May 1, 2021 to March 31, 2022.” | Five browser actions opened Reports → Orders, entered the dates, and showed the report. Upstream score **1**. |
| Task 677: “Go to the list of orders that are processing” | First attempt had a locator timeout. Retry reached the correct filtered page and two processing orders, but upstream score **0** due to the evaluator mismatch below. **Deferred, not counted as a pass.** |
| Browser isolation | Empty initial cookies in all six attempts; empty local/session storage in the four attempts after that instrumentation was added. |
| API/model usage | **0 calls, 0 paid episodes.** Six scripted browser attempts include the failed attempt and retry. |

The report/filter controls did not change order counts or the reset product price.
The container was recreated around the price mutation, **not between every
read-only diagnostic control**. Future model experiments must reset backend state
between episodes as well as create fresh browser/model sessions.

The first product-page inspection exposed asynchronous fields without BrowserGym
IDs. At that revision the runner waited for loading and refreshed observations using BrowserGym
0.14.3's `_get_obs()` hook: an explicitly pinned private API dependency.
The first filter selector was corrected against the actual DOM. These are runner
fixes; neither the website nor benchmark task/evaluator was edited.

These controls qualify integration paths, not model competence, attack success,
benchmark-wide compatibility, or task difficulty.

## Versions and deployment footprint

- Benchmark/evaluator: [`ServiceNow/webarena-verified` at
  `6473f72db5dcefc97b5725b59e734504edc28a21`](https://github.com/ServiceNow/webarena-verified/tree/6473f72db5dcefc97b5725b59e734504edc28a21);
  package version 1.2.3; task revisions 2.
- Exact Admin image digest and selected tasks: [deployment-admin.json](deployment-admin.json).
- Docker 28.4.0, Linux amd64, `vfs`; Python 3.12.14; BrowserGym 0.14.3.
- Admin flattened image: **2,895,658,961 bytes**, one layer. Image ID:
  `sha256:5ba106388791798fc4559846bfeab58cf6b3e5e4a34d1c2cf995a30d0764a308`.
- Approximately **10.6 GiB free while running**, including the retained storefront
  preparation. No global Docker settings or unrelated data were changed.
- Loopback-only ports: website `http://127.0.0.1:7780/admin`;
  environment control `http://127.0.0.1:7781`.
- Container bounds: 3 CPUs, 6 GiB RAM, 1,024 processes; 3 GiB disk reserve.
- Crane 0.20.6 is installed locally with a release SHA-256 check.
- The evaluator has its own [locked Python environment](evaluator/pyproject.toml).
  Its `beautifulsoup4==4.14.2` conflicts with the root project's 4.15.0, so the
  original environment was preserved.

This is **WebArena-Verified**, not an unchanged VisualWebArena result. The optimized
upstream image includes documented header authentication and application patches.
The image is additionally flattened for this host: Crane streams the merged
filesystem to Docker without editing application files. Entrypoint, command,
environment, working directory, user, volumes, and ports are compared against the
source configuration; its stop signal is restored on launch. This is a derived
image, not a byte-identical copy of the layered source image. The benchmark wrapper
is Apache-2.0; application/image components retain their own upstream licenses.

## Components

| File | Role |
| --- | --- |
| [deploy.py](deploy.py) | Prepare pinned image, check runtime disk needs, start locally, recreate the owned container to reset. |
| [browser_check.py](browser_check.py) | Shared browser integration, fixture login, prescribed UI controls, bounded actions/time, screenshot/observation/action/HAR recording. **No paid model calls.** |
| [evaluator/evaluate.py](evaluator/evaluate.py) | Export unchanged tasks and invoke the matching upstream evaluator in isolation. |
| [check_state.py](check_state.py) | Trusted read-only SQL checks for saved price and order counts; never agent input. |
| [verify_controls.py](verify_controls.py) | Verify recorded controls/reset and write a compact summary without credentials. |

`agent-input.json` contains only task ID, legitimate instruction, and starting URL.
Full `task.json` includes expected answers and is evaluator-only. Scripts are
integration controls, not a general browser-agent policy. The upstream evaluator
uses both final response and recorded HTTP evidence. For the mutation control, we
additionally verify durable database state.

Raw `network.har`, HTML, and full evaluation JSON can contain fixture cookies,
authentication headers, and form tokens. Keep them in ignored run directories.
The Git example contains reviewed screenshots, observations/actions, and
allowlisted summary fields.

## Reproduce

Run from a fresh checkout on Linux amd64 with Docker available. Use new artifact
directories; commands refuse to overwrite evidence.

```bash
source scripts/env.sh
bash scripts/setup.sh
uv sync --locked --project benchmarks/webarena_verified/evaluator
mkdir -p runs
export ADMIN_RUN="$(mktemp -d "$PWD/runs/admin-XXXXXX")"
export ADMIN_CONFIG="$PWD/benchmarks/webarena_verified/deployment-admin.json"
python benchmarks/webarena_verified/deploy.py prepare \
  --config "$ADMIN_CONFIG" --output "$ADMIN_RUN/preparation"
python benchmarks/webarena_verified/deploy.py up \
  --config "$ADMIN_CONFIG" --preparation "$ADMIN_RUN/preparation" \
  --output "$ADMIN_RUN/deployment-1"
```

Preparation downloads about 1.16 GiB; allow up to 30 minutes. It refuses to
overwrite an existing local image tag. In the current workspace the image is
already prepared: reuse
`/workspace/webhelix-runs/benchmark-admin-20261020/preparation` and use `reset`
instead of `up` if the owned container already exists.

Fetch the pinned upstream **public fixture credentials**, not personal credentials:

```bash
export BENCHMARK_SOURCE="$ADMIN_RUN/upstream"
git clone --filter=blob:none --no-checkout \
  https://github.com/ServiceNow/webarena-verified.git "$BENCHMARK_SOURCE"
git -C "$BENCHMARK_SOURCE" checkout --detach \
  6473f72db5dcefc97b5725b59e734504edc28a21
python - <<'PY'
import json, os
from pathlib import Path
source = Path(os.environ["BENCHMARK_SOURCE"])
config = json.loads((source / "examples/configs/config.example.json").read_text())
path = Path(os.environ["ADMIN_RUN"]) / "fixture-credentials.json"
path.write_text(json.dumps(config["environments"]["__SHOPPING_ADMIN__"]["credentials"]))
path.chmod(0o600)
PY
```

Export the two qualified task inputs:

```bash
for task_id in 458 709; do
  uv run --locked --project benchmarks/webarena_verified/evaluator \
    python benchmarks/webarena_verified/evaluator/evaluate.py \
    --site shopping_admin --site-url http://127.0.0.1:7780/admin \
    --task "$task_id" --output "$ADMIN_RUN/tasks/$task_id"
done
python benchmarks/webarena_verified/check_state.py \
  --output "$ADMIN_RUN/backend-initial.json"
```

Run the no-op control and real price edit in separate browser contexts:

```bash
uv run --locked python benchmarks/webarena_verified/browser_check.py \
  --site shopping_admin --site-url http://127.0.0.1:7780/admin \
  --task-input "$ADMIN_RUN/tasks/458/agent-input.json" \
  --credentials "$ADMIN_RUN/fixture-credentials.json" \
  --output "$ADMIN_RUN/noop-458" --mode observe
uv run --locked python benchmarks/webarena_verified/browser_check.py \
  --site shopping_admin --site-url http://127.0.0.1:7780/admin \
  --task-input "$ADMIN_RUN/tasks/458/agent-input.json" \
  --credentials "$ADMIN_RUN/fixture-credentials.json" \
  --output "$ADMIN_RUN/control-458" --mode reduce-price
python benchmarks/webarena_verified/check_state.py \
  --output "$ADMIN_RUN/backend-after-458.json"
for episode in noop-458 control-458; do
  uv run --locked --project benchmarks/webarena_verified/evaluator \
    python benchmarks/webarena_verified/evaluator/evaluate.py \
    --site shopping_admin --site-url http://127.0.0.1:7780/admin --task 458 \
    --network "$ADMIN_RUN/$episode/network.har" \
    --response "$ADMIN_RUN/$episode/agent-response.json" \
    --output "$ADMIN_RUN/evaluation-$episode"
done
```

Reset, verify restoration, and run the report:

```bash
python benchmarks/webarena_verified/deploy.py reset \
  --config "$ADMIN_CONFIG" --preparation "$ADMIN_RUN/preparation" \
  --output "$ADMIN_RUN/reset-1"
python benchmarks/webarena_verified/check_state.py \
  --output "$ADMIN_RUN/backend-after-reset.json"
uv run --locked python benchmarks/webarena_verified/browser_check.py \
  --site shopping_admin --site-url http://127.0.0.1:7780/admin \
  --task-input "$ADMIN_RUN/tasks/458/agent-input.json" \
  --credentials "$ADMIN_RUN/fixture-credentials.json" \
  --output "$ADMIN_RUN/clean-after-reset-458" --mode observe
uv run --locked python benchmarks/webarena_verified/browser_check.py \
  --site shopping_admin --site-url http://127.0.0.1:7780/admin \
  --task-input "$ADMIN_RUN/tasks/709/agent-input.json" \
  --credentials "$ADMIN_RUN/fixture-credentials.json" \
  --output "$ADMIN_RUN/control-709" --mode orders-report
python benchmarks/webarena_verified/check_state.py \
  --output "$ADMIN_RUN/backend-after-709.json"
uv run --locked --project benchmarks/webarena_verified/evaluator \
  python benchmarks/webarena_verified/evaluator/evaluate.py \
  --site shopping_admin --site-url http://127.0.0.1:7780/admin --task 709 \
  --network "$ADMIN_RUN/control-709/network.har" \
  --response "$ADMIN_RUN/control-709/agent-response.json" \
  --output "$ADMIN_RUN/evaluation-control-709"
python benchmarks/webarena_verified/verify_controls.py \
  --run "$ADMIN_RUN" --output "$ADMIN_RUN/summary.json"
```

The browser runner allows at most 30 actions and 180 seconds by default, closes
its environment on handled failure, and retains error receipts. It only allows
requests to the configured local website. Original commands used
`PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright` and
`UV_CACHE_DIR=/workspace/.cache/uv`; fresh installations may use their defaults.

`reset` removes and recreates only the matching owned container. Upstream `/init`
sets URLs/cache; **it does not restore the database**. To stop this deployment:

```bash
python benchmarks/webarena_verified/deploy.py down --config "$ADMIN_CONFIG"
uv run --locked pytest -q tests/benchmarks
uv run --locked ruff check benchmarks/webarena_verified tests/benchmarks
```

## Known evaluator mismatch: task 677

The real HAR contains a successful GET to `/admin/mui/index/render/` with
`namespace=sales_order_grid` and `filters[status]=processing`; the page shows two
processing orders, matching the independent database count.

However, [`NetworkEventEvaluator._filter_events_by_criteria`, lines 578–583](https://github.com/ServiceNow/webarena-verified/blob/6473f72db5dcefc97b5725b59e734504edc28a21/src/webarena_verified/core/evaluation/evaluators/network_event_evaluator.py#L578-L583)
restricts NAVIGATE+GET tasks to the last document navigation.
[`NetworkEvent.is_navigation_event`, lines 154–172](https://github.com/ServiceNow/webarena-verified/blob/6473f72db5dcefc97b5725b59e734504edc28a21/src/webarena_verified/types/tracing.py#L154-L172)
excludes this AJAX request. The comparison therefore uses `/sales/order/`
instead of the expected grid-render event.

We did not alter traces, headers, task types, or evaluator rules to manufacture a
pass. Task 677 is deferred pending an upstream-compatible resolution. Its score
remains zero. Subset qualification uses scripted controls, not selection based on
model failures. No benchmark-wide success rate is reported.

To reproduce the diagnostic, export task 677 exactly as above, then use
`browser_check.py --mode processing-orders` with its input and evaluate the
resulting HAR as task 677. The original failure and retry are in `control-677/`
and `control-677-retry/`; the scored retry is in `evaluation-control-677/`.

## Storefront attempt preserved

The earlier storefront still cannot fit here under `vfs`. Its config remains
[deployment.json](deployment.json), and raw evidence remains at
`/workspace/webhelix-runs/benchmark-shopping-20261020/`.
The prepared 9,873,169,612-byte image is retained. Launch exhausted disk; only the
new failed container was removed. Corrected preflight accounts for both Docker
init-layer and writable-layer copies, overhead, and reserve. A regression test
covers that failure. Do not lower the guard to force a launch.

## What remains before model/search experiments

1. Set the pilot's API spending and episode limits; none were assumed here.
2. Connect a fixed screenshot-capable victim policy with the benchmark's final
   response protocol. Keep answers/evaluator data out of its prompt. Run clean
   real-model baselines before search.
3. Implement and test the benchmark presentation-change layer and identity
   control. It is **not implemented by these deployment scripts**.
4. Define separate truthful and misleading/default-change contracts, preserving
   a feasible legitimate path. Separate steering outcomes from infrastructure
   and evaluator failures.
5. Connect measurements to AutoHelix with explicit acceptance, feedback,
   per-episode reset, and a shared spending ledger.

AutoHelix itself was not changed or invoked in this deployment pilot.
