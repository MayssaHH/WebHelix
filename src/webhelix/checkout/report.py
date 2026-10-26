"""Self-contained index into saved checkout evidence; no model calls or replay."""

from html import escape
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def build_report(run):
    run = Path(run).resolve()

    def data(path):
        return json.loads(path.read_text())

    def link(path, label=None):
        relative = os.path.relpath(path, run)
        return (
            f'<a href="{escape(relative, quote=True)}">{escape(label or path.name)}</a>'
        )

    blocks = [
        "<!doctype html><meta charset='utf-8'><title>WebHelix checkout evidence</title>",
        "<style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:0 20px;color:#172033}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd2da;padding:10px;text-align:left}img{max-width:100%;border:1px solid #ccd2da}section{margin:36px 0}pre{white-space:pre-wrap;background:#f2f5f8;padding:16px}.frames{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}</style>",
        "<h1>Checkout experiment evidence</h1><p>This report reads saved artifacts. Scripted runs are engineering controls; they do not measure model susceptibility.</p>",
    ]
    for name in (
        "status.json",
        "controls.json",
        "history.json",
        "feedback-continuity.json",
        "budget.json",
        "runtime.json",
        "opencode-runtime.json",
        "harness-check/check.json",
    ):
        path = run / name
        if path.exists():
            blocks.append(link(path) + " &nbsp; ")
    rows, batches = [], []
    for path in sorted((run / "batches").glob("*/report.json")):
        report = data(path)
        batches.append((path.parent, report))
        m = report.get("metrics", {})
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{escape(str(value))}</td>"
                for value in (
                    path.parent.name,
                    report.get("policy"),
                    m.get("episodes"),
                    m.get("steering_rate"),
                    m.get("legitimate_success_rate"),
                    m.get("evaluation_ok"),
                )
            )
            + "<td>"
            + link(path)
            + "</td></tr>"
        )
    blocks.append(
        "<h2>Measured batches</h2><table><tr><th>Batch</th><th>Policy</th><th>Episodes</th><th>Unauthorized rate</th><th>Legitimate success</th><th>Evaluation valid</th><th>Evidence</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    for batch, report in batches:
        blocks.append(
            f"<section><h2>{escape(batch.name)}</h2><p>Candidate SHA-256: {escape(report.get('candidate_hash', 'invalid/unavailable'))}</p>"
        )
        for path in sorted((batch / "candidate").glob("*")) + sorted(
            batch.glob("*.diff")
        ):
            blocks.append(link(path) + " &nbsp; ")
        blocks.append("<div class='frames'>")
        seen = set()
        for obs_path in sorted(
            (batch / "episode-0").glob("observation-*.json"),
            key=lambda p: int(p.stem.rsplit("-", 1)[1]),
        ):
            obs = data(obs_path)
            stage = (
                obs.get("stage")
                or urlsplit(obs.get("url", "")).path.strip("/")
                or "product"
            )
            if stage in seen:
                continue
            seen.add(stage)
            number = obs_path.stem.rsplit("-", 1)[1]
            image = obs_path.with_name(f"screenshot-{number}.png")
            if image.exists():
                source = escape(os.path.relpath(image, run), quote=True)
                blocks.append(
                    f"<figure><figcaption>{escape(stage or 'Page')} — actual episode 0 observation</figcaption><a href='{source}'><img src='{source}' loading='lazy'></a>{link(obs_path)}</figure>"
                )
        blocks.append("</div><p>Episodes: ")
        for path in sorted(batch.glob("episode-*/episode.json")):
            blocks.append(link(path, path.parent.name) + " &nbsp; ")
        blocks.append("</p></section>")
    blocks.append("<h2>Optimizer requests and feedback</h2>")
    for directory in (run / "proposals", run / "feedback"):
        for path in sorted(directory.glob("iter-*/*")):
            if path.is_file() and path.suffix in {
                ".json",
                ".md",
                ".diff",
                ".txt",
                ".jsonl",
            }:
                blocks.append("<p>" + link(path, str(path.relative_to(run))) + "</p>")
    for directory in sorted((run / "proposals").glob("iter-*")):
        for relative in (
            "runtime/opencode-config.json",
            "runtime/runtime-receipt.json",
            "gateway/summary.json",
        ):
            path = directory / relative
            if path.is_file():
                blocks.append("<p>" + link(path, str(path.relative_to(run))) + "</p>")
    destination = run / "index.html"
    destination.write_text("\n".join(blocks))
    return destination
