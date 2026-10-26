#!/usr/bin/env python3
"""Export a small, explicit subset of a completed OFFLINE OpenCode demo."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.output.resolve()
    status = json.loads((run / "status.json").read_text())
    if (
        status.get("optimizer_backend") != "opencode-offline-fixture"
        or status.get("status") != "completed"
    ):
        raise SystemExit(
            "Only completed, explicitly offline OpenCode runs can use this exporter"
        )
    if out.exists():
        raise SystemExit(
            "Choose a new export directory; existing evidence is never overwritten"
        )
    selected = [
        run / name
        for name in (
            "status.json",
            "runtime.json",
            "optimizer-settings.json",
            "opencode-runtime.json",
            "history.json",
            "feedback-continuity.json",
            "budget.json",
            "execution.json",
            "harness-check/check.json",
        )
    ]
    if (run / "audit.json").is_file():
        selected.append(run / "audit.json")
    for directory in sorted((run / "proposals").glob("iter-*")):
        selected += [
            directory / name
            for name in (
                "optimizer-note.md",
                "candidate.diff",
                "result.json",
                "acceptance.json",
                "gateway-summary.json",
                "feedback-continuity.json",
                "runtime/runtime-receipt.json",
            )
            if (directory / name).is_file()
        ]
        selected += list((directory / "candidate").glob("*"))
    for directory in sorted((run / "feedback").glob("iter-*")):
        selected.append(directory / "result.json")
    for reference in ("clean.json", "identity.json"):
        selected.append(run / reference)
    for batch in sorted((run / "batches").iterdir()):
        selected += [
            batch / "report.json",
            batch / "episode-0/episode.json",
            batch / "episode-0/screenshot-2.png",
            batch / "episode-0/observation-2.json",
        ]
    manifest = []
    for path in selected:
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run)
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        manifest.append(
            {
                "path": str(relative),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
    (out / "export-manifest.json").write_text(
        json.dumps(
            {
                "raw_run": str(run),
                "offline_only": True,
                "selection": "Small receipts, candidate files/diffs/notes, actual options screenshots and observations. Full provider payloads, sessions, previews, and tested source remain in raw_run.",
                "files": manifest,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "files": len(manifest),
                "bytes": sum(x["bytes"] for x in manifest),
                "output": str(out),
            }
        )
    )


if __name__ == "__main__":
    main()
