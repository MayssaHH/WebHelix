# Isolated OpenCode runtime

`lock.json` pins OpenCode **1.19.4**, the official release archive SHA-256,
Linux/amd64 architecture, Python base-image digest and Git package version.
OpenCode is MIT-licensed. The installer downloads the upstream executable; its
source is not copied into WebHelix.

From the WebHelix checkout, with a working Docker daemon:

```bash
python scripts/setup-opencode.py --cache .cache/opencode
```

This builds `webhelix-opencode:1.19.4`, verifies its CLI version with networking
disabled, and records the image ID in `.cache/opencode/installation.json`.
Installation contacts GitHub, Docker Hub and Debian package mirrors. The build
can use the environment's existing proxy settings; they are Docker's transient
build arguments, not stored credentials. If a pinned package is no longer
available, installation fails rather than silently substituting a version.
The pinned image and archive determine OpenCode; transitive Debian package
availability still depends on the package mirror. Save the image ID with runs.

The experiment launches the CLI through the unchanged upstream AutoHelix
`OpenCodeAgent`. It runs as the invoking user's UID/GID in a disposable container
with a read-only root filesystem, dropped capabilities, no privilege escalation,
resource limits, and a new internal Docker network. It mounts only:

| Container path | Access |
| --- | --- |
| `/work/candidate` | Writable active candidate files |
| `/work/reference` | Read-only task, contract, context and evidence |
| `/work/notes` | Writable pre-evaluation note |
| `/work/previews` | Writable deterministic preview artifacts |
| `/runtime` | Read-only scoped gateway configuration |

No host checkout, `.env`, Docker socket, Git metadata, provider credential or
victim credential is mounted. Temporary home/storage is fresh per session. Host
proxy settings are explicitly cleared inside the container: model and preview
requests go directly to the scoped local gateway. External Internet connectivity
is unavailable. Docker isolation is required; an editable-file list alone is not
a security boundary. The internal network can reach its host bridge interface;
it is not a general guarantee that unrelated services bound to all host interfaces
are unreachable. Do not expose unrelated sensitive services there.

`wh-preview` calls the trusted renderer through that gateway, saves images and
the victim-equivalent visible observations under `/work/previews`, and prints
their paths for the agent's image/file tools. It never calls the victim model.
The model must read an image before its receipt can establish visual inspection.

Automatic updates, model-catalog fetches, project configs, remote tools,
subagents, automatic compaction and plugins are disabled. The configured primary
and small-model names are identical; the gateway independently rejects other
models and accounts for every forwarded request. A CLI step limit is additional
to those request and spending limits.

Before trusted code reads final candidate files or notes, the runtime forcibly
removes the container, terminating background commands. Cleanup preserves
sanitized configuration, bounded CLI diagnostic logs, image/version receipts,
notes and previews, and removes the scoped-token file. A cleanup failure is
reported as an error, never silently counted as successful cleanup.
