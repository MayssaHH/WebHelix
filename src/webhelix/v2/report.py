"""Offline, portable evidence export. Never renders a candidate or calls a model."""

import hashlib
import html
import json
import shutil
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

from .contract import FILES, read, save


def stage_images(batch):
    found = {}
    episode = batch / "episode-0"
    for state in sorted(
        episode.glob("backend-*.json"), key=lambda p: int(p.stem.split("-")[-1])
    ):
        stage = read(state)["stage"]
        image = episode / state.name.replace("backend-", "screenshot-").replace(
            ".json", ".png"
        )
        if stage in ("rooms", "options", "review") and image.exists():
            found.setdefault(stage, image)
    return found


def overview(clean, candidate, destination, label="candidate"):
    left, right = stage_images(clean), stage_images(candidate)
    canvas = Image.new("RGB", (1048, 1280), "#e8edf2")
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 10), "CLEAN: recorded episode 0", fill="black")
    draw.text((536, 10), label.upper() + ": recorded episode 0", fill="black")
    for i, stage in enumerate(("rooms", "options", "review")):
        y = 38 + i * 412
        for x, images in ((12, left), (536, right)):
            draw.text((x, y), stage.upper(), fill="black")
            if stage in images:
                with Image.open(images[stage]) as image:
                    canvas.paste(image.convert("RGB").resize((500, 375)), (x, y + 20))
            else:
                draw.text(
                    (x, y + 45), "No recorded observation of this stage", fill="black"
                )
    canvas.save(destination)


def export(run, output):
    run, output = Path(run).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in (
        "status.json",
        "project-start.json",
        "budget.json",
        "retention.json",
        "isolation.json",
        "autohelix-history.jsonl",
        "optimizer-budget.json",
    ):
        if (run / name).exists():
            shutil.copy2(run / name, output / name)
    # An optimizer failure can precede evaluation. Preserve the attempt without
    # manufacturing a candidate score or an acceptance/rejection decision.
    attempts = []
    for result in sorted((run / "proposals").glob("*/agent-result.json")):
        name = result.parent.name
        target = output / "optimizer-attempts" / name
        shutil.copytree(result.parent, target)
        for filename in ("prompt.txt", "agent.log"):
            source = run / "autohelix-logs" / name / filename
            if source.exists():
                shutil.copy2(
                    source,
                    target / ("agent-log.txt" if filename == "agent.log" else filename),
                )
        attempts.append({"iteration": int(name.split("-")[-1]), **read(result)})
    save(output / "optimizer-attempts.json", attempts)
    reports = sorted((run / "batches").glob("*/report.json"))
    clean = next((p.parent for p in reports if read(p)["label"] == "clean"), None)
    rows, cards, manifest = [], [], {}
    totals = Counter()
    for source in reports:
        report = read(source)
        batch = source.parent
        label = report["label"]
        target = output / batch.name
        target.mkdir()
        shutil.copy2(source, target / "report.json")
        for name in ("acceptance.json", "proposal-note.md"):
            if (batch / name).exists():
                shutil.copy2(batch / name, target / name)
        for name in FILES:
            for path in (batch / "candidate" / name, batch / (name + ".diff")):
                if path.exists():
                    dest = (
                        target
                        / ("candidate" if path.parent.name == "candidate" else "")
                        / path.name
                    )
                    dest.parent.mkdir(exist_ok=True)
                    shutil.copy2(path, dest)
        # Keep the full numeric report for every episode, but only episode 0's
        # step-by-step image/observation trace in this small portable export.
        first = batch / "episode-0"
        if first.exists():
            dest = target / "episode-0"
            dest.mkdir()
            for path in sorted(first.iterdir()):
                if not path.name.startswith(
                    (
                        "observation-",
                        "screenshot-",
                        "action-",
                        "backend-",
                        "request-",
                        "response-",
                        "episode.",
                    )
                ):
                    continue
                manifest[str(path.relative_to(run))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                if path.name.startswith("request-"):
                    request = read(path)
                    for message in request.get("input", []):
                        content = message.get("content")
                        if isinstance(content, list):
                            for part in content:
                                if part.get("type") == "input_image":
                                    part["image_url"] = "artifact:" + path.name.replace(
                                        "request-", "screenshot-"
                                    ).replace(".json", ".png")
                    save(dest / path.name, request)
                else:
                    shutil.copy2(path, dest / path.name)
        if label.startswith("iter-"):
            n = label.split("-")[-1]
            for src, name in (
                (run / f"feedback-{n}.json", "feedback.json"),
                (run / f"autohelix-logs/iter-{n}/prompt.txt", "optimizer-prompt.txt"),
            ):
                if src.exists():
                    shutil.copy2(src, target / name)
        m = report["metrics"]
        totals.update(
            {k: m[k] for k in ("input_tokens", "output_tokens", "latency_seconds")}
        )
        acceptance = (
            read(batch / "acceptance.json")
            if (batch / "acceptance.json").exists()
            else {}
        )
        decision = (
            ("accepted" if acceptance["accepted"] else "rejected")
            if acceptance
            else "diagnostic"
        )
        rows.append(
            f"| [{label}]({batch.name}/report.json) | {m['episodes']} | {m['counts']} | {m['violation_rate']:.3f} | {decision} |"
        )
        image = ""
        if clean and (label.startswith("iter-") or label == "identity"):
            overview(clean, batch, target / "overview.png", label)
            image = f'<a href="{batch.name}/overview.png"><img src="{batch.name}/overview.png" width="100%"></a>'
        note = (
            (batch / "proposal-note.md").read_text()
            if (batch / "proposal-note.md").exists()
            else "No optimizer note for this condition."
        )
        cards.append(
            f'<section><h2>{html.escape(label)}: {decision}</h2><p>{html.escape(str(m["counts"]))}</p>{image}<details><summary>Optimizer proposal note (before evaluation)</summary><pre>{html.escape(note)}</pre></details><p><a href="{batch.name}/report.json">Full evaluation</a> · <a href="{batch.name}/episode-0/episode.json">First episode</a></p></section>'
        )
    history = (
        [
            json.loads(line)
            for line in (run / "autohelix-history.jsonl").read_text().splitlines()
        ]
        if (run / "autohelix-history.jsonl").exists()
        else []
    )
    measured_optimizer = [
        r["usage"] for r in history if r["iteration"] > 0 and r.get("usage")
    ]
    usage = {
        "victim": dict(totals),
        "optimizer": {
            name: sum(r.get(name, 0) for r in measured_optimizer)
            if measured_optimizer
            else None
            for name in ("input_tokens", "output_tokens")
        },
        "reported_cost_usd": None,
        "optimizer_attempts": len(attempts),
        "optimizer_usage_reported": len(measured_optimizer),
        "cost_note": "Dollar cost not reported. Upstream zero defaults are not evidence of free usage.",
    }
    save(output / "usage.json", usage)
    save(output / "original-trace-hashes.json", manifest)
    status = read(run / "status.json")
    (output / "README.md").write_text(
        "# Reservation experiment evidence\n\n"
        f"Run status: `{status['status']}`. Real episode reservations: **{status['reserved_episodes']}/30**. Source revision: `{status['source_revision']}`.\n\n"
        f"Completed optimization iterations: **{status.get('completed_iterations', 0)}**. Optimizer attempts: **{len(attempts)}**. Failures before evaluation are in `optimizer-attempts/`; they have no candidate score or retention decision.\n\n"
        "These are recorded browser observations, not reconstructed candidate previews. All episodes are included in each report; episode 0 includes a portable detailed trace. Candidate proposal notes precede evaluation; separate feedback records contain measured outcomes.\n\n"
        "| Condition | Recorded outcomes | Counts | Violation rate | Retention |\n|---|---:|---|---:|---|\n"
        + "\n".join(rows)
        + "\n\n"
        "Open `index.html` locally or a condition's `overview.png` on GitHub. The left column is the clean episode, and the right is the named condition at the first observation of each stage. An identity comparison is the same page, not an optimizer-generated change. Missing stages are labeled.\n\n"
        "Request bodies replace only the base64 image URL with `artifact:screenshot-N.png`; that image is included. Exact original request bodies and all episodes remain in the local run. Original trace hashes are recorded. No authentication headers are exported. Absolute paths in reports identify original local artifacts.\n\n"
        "See `usage.json` for separate victim and optimizer token counts. Dollar cost was not reported. Strict empirical improvement is an engineering retention rule, not proof of causality, novelty or agent specificity. Arbitrary new prose still needs research review.\n"
    )
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>WebHelix reservation evidence</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;background:#fafafa;color:#142335}section{border-top:2px solid #bbc;padding:20px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Recorded reservation experiment</h1><p>Actual first-episode observations. Proposal notes and evaluation records are separate.</p>'
        + f'<p><strong>Status: {html.escape(status["status"])}, completed optimization iterations: {status.get("completed_iterations", 0)}</strong></p><p>{html.escape(status.get("error", ""))}</p><p><a href="optimizer-attempts.json">Optimizer attempts and errors</a></p>'
        + "".join(cards)
    )
    print("Offline evidence exported:", output)
