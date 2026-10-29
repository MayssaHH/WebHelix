# Benchmark deployment feasibility

> Historical deployment audit: package checks, version numbers and measured
> results below describe the 20 October environment. BrowserGym is not a dependency
> of current WebHelix. Current browser-agent setup is in the [main README](../README.md);
> the benchmark application and independent evaluator remain separately pinned.

Initial read-only checks and execution follow-up on 2026-10-20.
**Follow-up result: Shopping Admin is deployed and two scripted task controls
pass the pinned upstream evaluator.** The storefront launch exhausted disk under
`vfs`; its failed container was removed. The user selected the smaller Admin site,
which fits with approximately 10.6 GiB free while running. See the
[execution record](../benchmarks/webarena_verified/README.md) for commands, reset
checks, and the separate task-677 evaluator mismatch. No paid model requests,
model baselines, or benchmark optimization iterations were run.

Development branch: `feature/benchmark-search`, starting from
`2c74cb027ac52b5638adec3a1c5d4a66a1c06fdb`.
The previous reservation implementation and evidence remain unchanged.

## Agreed direction

- Prioritize visual manipulations.
- Separate truthful presentation changes from a condition permitting explicitly
  defined misleading wording/default changes.
- Prefer this workspace for deployment if the selected site fits.
- API spending and alternative hosting resources are not yet specified.
- The initial VisualWebArena route was too large. The user selected the smaller
  WebArena-Verified Shopping Admin site. Tasks 458 and 709 pass integration
  controls; task 677 is deferred for a documented evaluator mismatch.

## Host at the initial audit

Read-only checks used `docker info`, `docker system df`, and `df`.

| Property | Observed value |
| --- | --- |
| Docker server | 28.4.0 |
| Platform | Linux x86_64 |
| CPUs reported by Docker | 5 |
| Memory reported by Docker | 35,743,760,384 bytes, about 33.3 GiB |
| Docker storage driver | `vfs` |
| Docker data directory | `/var/lib/docker` |
| Free filesystem space | Approximately 30 GiB |
| Existing Docker images/containers/volumes | None |

`/var/lib/docker` and `/workspace` are on the same reported filesystem. Installing
images and saving experiment artifacts therefore compete for the available space.
Reported memory/CPU values do not prove sustained resource guarantees.
Workspace persistence and enlargement options have not been established.

## Website footprint checks

These are archive/registry sizes, **not** complete installed-space requirements.
Image expansion, storage-driver overhead, databases, resets, and experiment
artifacts require additional capacity.

| Site | Observed download artifact | Consequence |
| --- | --- | --- |
| Classifieds | Setup ZIP: 25,366,023 bytes; Linux amd64 application image: 76,861,304,894 bytes (about 71.6 GiB); MySQL image additional | The small ZIP is not the application image. The application image alone exceeds available disk. |
| Forum | `postmill-populated-exposed-withimg.tar`: 53,435,097,088 bytes (about 49.8 GiB) | The published archive exceeds available disk. |
| Shopping | Size not yet established from the inspected mirrors | No fit claim is justified. Archive.org metadata returned no files; HEAD requests to the published CMU mirror returned HTTP 503 over HTTPS and HTTP 403 over HTTP. |

Classifieds' Docker Hub tag currently resolves to:

```text
jykoh/classifieds:latest
linux/amd64 digest:
sha256:a2a794da92f62a8d7ffd02314e4fab40ba6c7fc08f568371608f88f0ef605e43
```

The setup template also specifies `mysql:8.1`; its reported Linux amd64 image size
is 166,123,241 bytes. A future deployment must pin inspected image digests rather
than depend on a moving `latest` tag.

Do not run the complete upstream setup scripts unchanged for a single-site pilot:
the inspected helper script expects shopping, forum, and Classifieds archives.
Do not prune unrelated Docker data or change the daemon's storage driver as an
implicit setup step.

## Dependency and reset requirements

PyPI exposes `browsergym-visualwebarena==0.14.3`, matching the project's
`browsergym-core==0.14.3`. Its metadata lists:

- `browsergym-core==0.14.3`
- `browsergym-webarena`
- `libvisualwebarena==0.0.15`
- `requests`
- `torch`

This establishes package availability, not a resolved, installed, tested dependency
set. Resolve benchmark dependencies in isolation and inspect their evaluator
requirements before modifying the project lockfile. `torch` in a dependency list
does not by itself establish that a GPU is required.

The BrowserGym integration is an adapted, unofficial VisualWebArena port. It
supports a separately configured full-reset service. Per-episode isolation must
cover backend state as well as browser state; verify the selected site's reset
behavior rather than assuming a fresh browser is sufficient.

Existing OpenAI-hosted optimizer/victim inference does not require a local GPU.
Any benchmark-specific visual evaluator requirements remain to be checked against
the selected task subset.

## Lighter benchmark route identified on follow-up

The original-image measurements above do **not** establish that all realistic
browser benchmarks require a larger host. WebArena-Verified publishes optimized
environment images. Its official environment documentation reports the following
image sizes; Docker Hub separately reports the Linux amd64 download sizes.

| Environment | Published optimized image size (GB, upstream units) | Registry download size (GiB) |
| --- | --- | --- |
| Shopping Admin | 2.9 | 1.160 |
| Shopping storefront | 13.3 | 5.049 |
| Reddit/forum | 8.41 | 4.259 |

Registry digests checked:

```text
am1n3e/webarena-verified-shopping_admin
sha256:d0531dd27ed98d0c459ff9e88118bf2ed8b660b0ed99c38837db46c065a5be13

am1n3e/webarena-verified-shopping
sha256:3e8cb9b945ea9b1c94ab26dba53e8d12dd0406abbf4bf686fd3bb2b6a5908feb

am1n3e/webarena-verified-reddit
sha256:0594908059a03e5f610005689440a95c05e776e734006197c7b1eba12734cb46
```

A single optimized environment is a plausible candidate for this workspace.
At the initial audit, actual installed fit had **not** been tested: include `vfs` layer overhead,
writable container data, reset storage, Python dependencies, browser caches, and
run artifacts. Do not infer total disk requirements from compressed image size.
Shopping Admin has the smallest measured image among these candidates; task
suitability must also be checked before selecting it.

The subsequent storefront attempt showed that even a flattened 9.2 GiB image
needs more runtime space on `vfs`: Docker creates an init-layer copy and a writable
container copy. The corrected preflight now rejects the launch before container
creation. Shopping Admin was subsequently prepared and deployed: its flattened
image is 2,895,658,961 bytes. Reset restored a product price from $27 to $32.

The BrowserGym WebArena-Verified integration documents filtering tasks to the
available site(s); installing every site is unnecessary for a single-site task
subset. We verified a custom BrowserGym 0.14.3 runner plus the matching upstream
evaluator in a separate locked environment. This does not establish compatibility
for the stock BrowserGym WebArena-Verified wrapper or every benchmark task.

This is a switch to **WebArena-Verified**, not evidence that these
images are drop-in replacements for unchanged VisualWebArena results. Upstream
documents application changes such as header authentication and forum vote-system
fixes. Pin the chosen environment and use its matching task/evaluator versions.
Visual manipulation experiments are possible on screenshot-based tasks without
claiming that the benchmark was originally designed around visual reasoning.

Other options:

- MiniWoB++ supports local files or a simple HTTP server, but its small interaction
  tasks are a weaker match for the requested realistic workflows.
- WorkArena uses hosted ServiceNow instances, reducing local site-storage needs.
  Access and permission to modify the research interface must be established.
- A reduced local replica or static snapshot can support an intermediate
  experiment, but is an adaptation rather than an unchanged benchmark task.

## Information needed if the original large-image route is retained

The current workspace is the user's preferred host, conditional on fit. For the
two original VisualWebArena sites with established footprints, either more Docker
storage or another host is required. Additional hardware is **not yet a requirement**
for the optimized WebArena-Verified alternative above.

1. Whether this workspace's backing disk can be enlarged, and to what capacity.
2. If another host is necessary: Linux distribution/architecture, available CPU,
   RAM, and **free space on Docker's actual data filesystem**.
3. Whether Docker/Compose is available and whether container management is allowed.
4. How the runner can reach that host (same machine, private network, or an
   authorized SSH tunnel), and whether it requires a VPN.
5. Whether storage and services can persist across sessions and be reset for
   repeated episodes.

Do not send passwords, private SSH keys, or API tokens in chat. Public internet
exposure of benchmark sites is not required. API spending limits are a separate
decision needed before real-model evaluations, not before this read-only audit.

## Sources checked

- [Official VisualWebArena deployment instructions](https://github.com/web-arena-x/visualwebarena/blob/main/environment_docker/README.md)
- [BrowserGym VisualWebArena integration](https://github.com/ServiceNow/BrowserGym/blob/main/browsergym/visualwebarena/README.md)
- [Helper Classifieds Compose template](https://github.com/gasse/webarena-setup/blob/main/visualwebarena/classifieds_docker-compose.yml.template)
- [Helper image loading script](https://github.com/gasse/webarena-setup/blob/main/visualwebarena/01_docker_load_images.sh)
- [Classifieds registry metadata](https://hub.docker.com/v2/repositories/jykoh/classifieds/tags/latest)
- [MySQL registry metadata](https://hub.docker.com/v2/repositories/library/mysql/tags/8.1)
- [Classifieds archive metadata](https://archive.org/metadata/classifieds_docker_compose)
- [Forum archive metadata](https://archive.org/metadata/postmill-populated-exposed-withimg)
- [BrowserGym package metadata](https://pypi.org/pypi/browsergym-visualwebarena/0.14.3/json)
- [WebArena-Verified environment sizes and setup](https://github.com/ServiceNow/webarena-verified/blob/main/docs/environments/index.md)
- [WebArena-Verified forum changes](https://github.com/ServiceNow/webarena-verified/blob/main/docs/environments/reddit.md)
- [BrowserGym WebArena-Verified single-site filtering](https://github.com/ServiceNow/BrowserGym/blob/main/browsergym/webarena_verified/README.md)
- [Optimized Shopping Admin registry metadata](https://hub.docker.com/v2/repositories/am1n3e/webarena-verified-shopping_admin/tags/latest)
- [Optimized shopping registry metadata](https://hub.docker.com/v2/repositories/am1n3e/webarena-verified-shopping/tags/latest)
- [Optimized forum registry metadata](https://hub.docker.com/v2/repositories/am1n3e/webarena-verified-reddit/tags/latest)
- [BrowserGym MiniWoB++ setup](https://github.com/ServiceNow/BrowserGym/blob/main/browsergym/miniwob/README.md)
- [WorkArena instance access](https://github.com/ServiceNow/WorkArena#-getting-started)

These URLs describe upstream state when checked. Their contents may change.
