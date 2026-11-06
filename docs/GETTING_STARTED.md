# Install, inspect and reproduce WebHelix

The [main README](../README.md) explains the research question and current evidence.
This guide is for running the software. Commands assume a terminal on the machine
that will host the website and browser. A local terminal and a cloud-workspace
terminal refer to different files and processes.

## Requirements and installation

Use Linux, Git, Python **3.12**, [uv](https://docs.astral.sh/uv/) and Chromium's
operating-system libraries. The pinned OpenCode runtime additionally requires
Docker on **Linux amd64**. Website preview and browser-only checks do not require
the OpenCode container.

```bash
git clone --branch main https://github.com/MayssaHH/WebHelix.git webhelix
cd webhelix
source scripts/env.sh
bash scripts/setup.sh
```

Setup installs the locked Python dependencies and matching Chromium build, then
validates the included fixtures. It does not call a model. If Chromium reports
missing system libraries, an administrator can install them with
`uv run --locked playwright install-deps chromium`, then repeat setup.

`scripts/env.sh` sets local cache paths and disables browser-agent telemetry/cloud
synchronization. Source it from the repository root in each new terminal.

AutoHelix is downloaded by `uv` as a Git dependency at the revision recorded in
[`pyproject.toml`](../pyproject.toml) and [`uv.lock`](../uv.lock). Its source is not
copied into this repository or patched. Dependency installation needs internet
access and access to the dependency repository.

For optimizer execution, install the pinned OpenCode image:

```bash
uv run --locked python scripts/setup-opencode.py
```

This builds and verifies the container without model calls. The
[OpenCode installation guide](OPENCODE_CHECKOUT.md#installation) explains the
runtime lock, isolation and installation receipt. Older experiment commands in
that guide describe their original checkout schedules.

## Preview without model calls

The fictional checkout is included in Git and can be inspected immediately:

```bash
uv run --locked webhelix checkout validate
uv run --locked webhelix checkout serve
```

Open the printed local URL. On a remote host, use its port-preview or forwarding
feature. Ctrl-C stops the server. This manual preview is not a measured victim
episode and is not the archived Arena environment.

For Arena Flowers, use the [archive guide](ARENA_ARCHIVE.md#setup-view-and-verify-without-models).
The captured merchant pages/assets are intentionally outside Git. A fresh clone
does not contain the original capture: replay requires its preserved archive, or
a new capture into an unused directory. A new capture can differ from the published
experiment; preserve its manifest and source bytes.

## Verify locally without paid models

Run the test suite from the repository root:

```bash
uv run --locked pytest -q
```

For deterministic checkout integration controls, use fresh child output directories:

```bash
webhelix_checks=$(mktemp -d /tmp/webhelix-checks.XXXXXX)
uv run --locked webhelix checkout interaction-controls --run-dir "$webhelix_checks/controls"
uv run --locked webhelix checkout discovery --prepare --scripted --run-dir "$webhelix_checks/discovery"
uv run --locked webhelix checkout discovery --execute --run-dir "$webhelix_checks/discovery"
```

The search uses Docker and local deterministic model responses. These controls
verify execution, scoring, isolation and feedback; they do not measure an LLM's
susceptibility. For the archived-site controls, follow the Arena guide instead.
Existing run directories are not silently overwritten.

## Real models and accounting

Model configuration is explicit and independent for the optimizer and victim.
The interactive coding assistant's selected model does not select either role.
The latest reported Arena experiment used Sol/OpenCode and Luna/Browser-Use;
checked-in default examples still describe earlier pairings. Use the saved
[experiment configurations](../examples/arena-archive/sol-luna-three-iterations-retry-20261104/README.md)
to identify the exact configuration behind a result, and review a new run's
prepared settings before execution.

The [Arena guide](ARENA_ARCHIVE.md#real-model-pilot-and-baseline-inspection) and
[checkout discovery guide](DISCOVERY_PILOT.md) document preparation and execution.
Preparation makes no model calls. Real execution requires provider access, an
experiment-specific spending plan, verified pricing and explicit request,
episode, action and timeout limits. No model is silently substituted. One clean
competence episode precedes search; a failed baseline stops it.

Keep credentials in the ignored `.env` using the variable named by each model
configuration. [`.env.example`](../.env.example) contains placeholders only.
Codex/ChatGPT login and API billing are separate. Both roles can use the same API
account while retaining separate conversations and usage records. Historical
run budgets do not authorize new calls.

See [financial authorization](DISCOVERY_PILOT.md#financial-authorization) for capped
and explicitly uncapped modes. Local token-based accounting is not a verified
provider invoice or an account-wide cap. Failed requests and started episodes
remain recorded; uncertain dispatches can retain budget reservations.

The older product-selection, reservation and reservation proposal-adapter entrypoints
additionally require `WEBHELIX_VICTIM_RUN`: an absolute path to a separately prepared
spending directory containing `runtime.json`, `spending-plan.json` and its accounting
ledger. `runtime.json` includes the public victim configuration and positive
`max_real_episodes`/`max_actions` limits. Do not mix this with those workflows'
historical `{maximum, reservations}` episode ledgers. Checkout and Arena discovery
pass their configured ledger directly.

## Inspect evidence

Start with each run's `index.html`, history, candidate diffs, optimizer notes,
feedback and evaluation summaries. Exact observations, actions, screenshots,
backend journals and provider call receipts support those summaries. A prepared
request alone does not establish a completed model response. Diagnostic snapshots
are not automatically victim inputs.

[Intervention evidence](ARENA_EXPOSURE_REVIEW.md) explains how edits and rule effects
are connected to outcomes; [adaptive feedback](ADAPTIVE_SEARCH.md) explains what the
next optimizer receives. Rejected proposals and failed evaluations remain in the
record. Historical examples retain their original agent identities and must not
be relabelled as measurements of the current Browser-Use setup.

Keep credentials, browser profiles, captured merchant material and full private
traces outside commits. The repository's `.gitignore` covers local environments,
captures, caches and generated run directories.
