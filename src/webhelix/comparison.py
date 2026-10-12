"""Offline before/after reports. Never runs an optimizer, victim or evaluator."""

import difflib
import hashlib
import html
import json
import shutil
from collections import Counter
from pathlib import Path

from .site import LocalSite
from .spec import InvalidCandidate, ground_truth, read_json, validate_spec


STYLE = """
body{font:16px/1.5 system-ui,sans-serif;color:#172536;background:#f4f6fa;margin:0}
main{max-width:1560px;margin:auto;padding:30px}h1{font-size:34px;margin:5px 0}
h2{font-size:23px;margin-top:0}h3{font-size:18px}.muted{color:#526177}
.tag{background:#e3eaf8;padding:6px 12px;border-radius:20px;display:inline-block}
.notice{background:#fff2d4;border-left:5px solid #cb8c13;padding:16px;margin:20px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:22px;align-items:start}
.panel{background:white;border:1px solid #d6dde7;border-radius:12px;padding:20px;margin:16px 0}
iframe{width:100%;height:1160px;border:1px solid #d6dde7;background:white}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6fa;padding:15px;font-size:13px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #ddd;padding:9px}
summary{cursor:pointer;font-weight:600}img{max-width:100%;border:1px solid #ddd}
a{color:#155cb5}.metric{font-size:22px;font-weight:650}@media(max-width:850px){.grid{grid-template-columns:1fr}}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def find_batches(run):
    """Use completed harness decisions, not arbitrary exploratory evaluations."""
    result = {}
    for path in (run / "batches").glob("*/acceptance.json"):
        decision = read_json(path)
        if decision.get("autohelix_result") is None:
            continue
        iteration = decision["iteration"]
        if iteration in result:
            raise ValueError(f"Ambiguous final evaluation for iteration {iteration}")
        result[iteration] = path.parent
    return result


def structure(spec):
    return " | ".join(" → ".join(group) for group in spec["groups"])


def describe_change(before, after, target):
    """Facts about structure only; never invent a model's motivation."""
    old, new = sum(before["groups"], []), sum(after["groups"], [])
    return (
        f"The first {target} card moves from position {old.index(target) + 1} to {new.index(target) + 1}. "
        f"Its appearances change from {Counter(old)[target]} to {Counter(new)[target]}. "
        f"The number of groups changes from {len(before['groups'])} to {len(after['groups'])}. "
        "All product facts and selection actions remain fixed."
    )


def export_condition(root, batch, output, name):
    report = read_json(batch / "report.json")
    raw = (batch / "candidate.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != report["raw_hash"]:
        raise ValueError("Saved candidate does not match the evaluated bytes")
    products, _, _, _ = ground_truth(root)
    # Revalidate even if the saved report claims validity.
    try:
        spec = validate_spec(root, batch / "candidate.json")
    except InvalidCandidate:
        if report["validation"]["valid"]:
            raise
        spec = None
    view = (
        LocalSite(products, spec).render()
        if spec is not None
        else "<!doctype html><h1>Invalid candidate — no page rendered</h1>"
    )
    (output / f"{name}.html").write_text(view)
    (output / f"{name}.json").write_bytes(raw)
    # Paths in the source reports may belong to a previous machine. Use the batch itself.
    episode = batch / "episode-0"
    if not episode.exists():
        episode = batch  # exported flat examples
    for source, destination in [
        (episode / "screenshot-0.png", output / f"{name}-observed.png"),
        (episode / "observation-0.json", output / f"{name}-observation.json"),
    ]:
        if source.exists():
            shutil.copy2(source, destination)
    return report, spec


def condition_panel(report, name, heading, spec):
    m = report["metrics"]
    episodes = report["episodes"]
    rows = "".join(
        "<tr>"
        f"<td>{index + 1}</td><td>{esc(e.get('backend', {}).get('confirmed') or 'None')}</td>"
        f"<td>{esc(e['outcome'])}</td></tr>"
        for index, e in enumerate(episodes)
    )
    initial = episodes[0].get(
        "normalized_observation", "No browser observation recorded."
    )
    return f"""<section class="panel"><h2>{esc(heading)}</h2>
<p class="muted">Victim policy: {esc(report["policy"])}. {m["episodes"]} recorded outcomes.</p>
<p class="metric">Target: {m["target_selection_rate"]:.0%} · Correct: {m["legitimate_success_rate"]:.0%}</p>
<p>{esc(structure(spec) if spec else "Invalid specification")}</p>
<iframe sandbox="" src="{name}.html" title="{esc(heading)} reconstructed page"></iframe>
<p class="muted">Read-only reconstruction from saved candidate, using the fixed renderer. Buttons are inactive in this report.</p>
<table><thead><tr><th>Episode</th><th>Confirmed product</th><th>Outcome</th></tr></thead><tbody>{rows}</tbody></table>
<details><summary>Victim text observation, normalized</summary><pre>{esc(initial)}</pre>
<p>Transient browser IDs are removed here. Exact IDs are in the recorded observation.</p></details>
<details><summary>First episode action trace</summary><pre>{esc(json.dumps(episodes[0].get("steps", []), indent=2))}</pre></details>
</section>"""


def compare_iteration(root, run, iteration, output):
    root, run, output = Path(root), Path(run), Path(output)
    batches = find_batches(run)
    if iteration <= 0 or iteration not in batches:
        raise ValueError("Requested candidate has no completed harness evaluation")
    history = [
        json.loads(line)
        for line in (run / "autohelix-history.jsonl").read_text().splitlines()
    ]
    before_iteration = max(
        r["iteration"] for r in history if r["iteration"] < iteration and r["accepted"]
    )
    before_batch, after_batch = batches[before_iteration], batches[iteration]
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(
            "Choose an empty output directory to preserve prior reports"
        )
    output.mkdir(parents=True, exist_ok=True)
    before, before_spec = export_condition(root, before_batch, output, "before")
    after, after_spec = export_condition(root, after_batch, output, "after")
    products, task, correct, target = ground_truth(root)
    prices = {p["id"]: p["price_cents"] / 100 for p in products}
    config = read_json(run / "project-start.json")["config"]
    optimizer = config["agent"]["type"]
    scripted = optimizer == "mock"
    author = (
        "Scripted optimizer (predetermined change)"
        if scripted
        else f"{optimizer} optimizer; see saved run logs for authorship"
    )
    notes_path = run / "autohelix-notes" / f"iter-{iteration}.md"
    notes = (
        notes_path.read_text()
        if notes_path.exists()
        else "No iteration note recorded. No model hypothesis is inferred."
    )
    (output / "optimizer-note.txt").write_text(notes)
    decision = read_json(after_batch / "acceptance.json")["autohelix_result"]
    retained = "Accepted" if decision["accepted"] else "Rejected"
    improvement = (
        after["metrics"]["target_selection_rate"]
        - before["metrics"]["target_selection_rate"]
    )
    description = (
        describe_change(before_spec, after_spec, target)
        if after_spec
        else "Invalid candidate; the validator stopped browser execution."
    )
    diff = "".join(
        difflib.unified_diff(
            before["episodes"][0].get("normalized_observation", "").splitlines(True),
            after["episodes"][0].get("normalized_observation", "").splitlines(True),
            fromfile="before-victim-observation",
            tofile="after-victim-observation",
        )
    )
    (output / "observation.diff").write_text(diff)
    metadata = {
        "iteration": iteration,
        "before_iteration": before_iteration,
        "candidate_author": author,
        "victim_policy": after["policy"],
        "candidate_hash": after.get("candidate_hash"),
        "accepted": decision["accepted"],
        "target_rate_change_from_retained": improvement,
        "target_rate_change_from_clean": after["metrics"]["target_rate_delta"],
        "description": description,
        "hypothesis_source": "saved iteration note"
        if notes_path.exists()
        else "missing",
        "before": before["metrics"],
        "after": after["metrics"],
    }
    (output / "comparison.json").write_text(json.dumps(metadata, indent=2))
    notice = (
        "Scripted control: this candidate was not generated by an LLM. It does not establish that a victim model was steered."
        if scripted or after["policy"] == "scripted"
        else "Recorded experiment: a changed selection rate is a measurement, not proof of a general dark pattern."
    )
    recorded = ""
    for name in ["before", "after"]:
        if (output / f"{name}-observed.png").exists():
            recorded += f'<section><h3>{name.title()} — original episode screenshot</h3><img src="{name}-observed.png" alt="Recorded {name} browser viewport">'
            if (output / f"{name}-observation.json").exists():
                recorded += f'<p><a href="{name}-observation.json">Exact recorded browser observation</a></p>'
            recorded += "</section>"
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>WebHelix — iteration {iteration}</title>
<style>{STYLE}</style></head><body><main>
<span class="tag">WebHelix · iteration {iteration} · {esc(retained)}</span>
<h1>What changed, and what did the victim choose?</h1>
<p><strong>Victim task:</strong> {esc(task["request"])}</p>
<p><strong>Correct choice:</strong> {esc(correct)} (${prices[correct]:.2f}). <strong>Optimizer's target:</strong> {esc(target)} (${prices[target]:.2f}).</p>
<div class="notice">{esc(notice)}</div>
<section class="panel"><h2>Candidate proposal</h2><p><strong>Author:</strong> {esc(author)}</p>
<p>{esc(description)}</p>
<p><strong>Measured change:</strong> {improvement:+.1%} target-selection rate versus the previously retained candidate. Decision: {esc(retained)}.</p>
<details open><summary>Recorded optimizer note — not an inferred explanation</summary><pre>{esc(notes)}</pre></details>
<p class="muted">The JSON specifies the layout. Both panels below are HTML rendered from that specification.</p></section>
<div class="grid">{condition_panel(before, "before", f"Before — retained iteration {before_iteration}", before_spec)}
{condition_panel(after, "after", f"After — proposed iteration {iteration}", after_spec)}</div>
<section class="panel"><h2>Evidence</h2><p>The victim uses accessibility/text observations. Visual prominence in these panels alone does not establish a model effect.</p>
<details><summary>Compare the text actually available to the victim</summary><pre>{esc(diff or "No normalized observation difference.")}</pre></details>
<details><summary>Original recorded screenshots and observations</summary><div class="grid">{recorded}</div></details>
<p><a href="comparison.json">Comparison data</a> · <a href="before.json">Before specification</a> · <a href="after.json">After specification</a> · <a href="optimizer-note.txt">Saved note</a></p>
</section></main></body></html>"""
    (output / "index.html").write_text(document)
    return metadata


def compare_run(root, run):
    """Reporting cannot turn a completed experiment into a failed evaluation."""
    run = Path(run)
    try:
        iterations = sorted(find_batches(run))
    except Exception as exc:
        (run / "comparison-error.json").write_text(json.dumps({"error": str(exc)}))
        return
    for iteration in iterations:
        if iteration == 0:
            continue
        out = run / "comparisons" / f"iter-{iteration}"
        if (out / "index.html").exists():
            continue
        try:
            compare_iteration(root, run, iteration, out)
        except Exception as exc:
            out.mkdir(parents=True, exist_ok=True)
            (out / "report-error.json").write_text(json.dumps({"error": str(exc)}))
