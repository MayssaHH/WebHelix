"""Export only fictional, scripted evidence; never export real API requests here."""

import argparse
import json
import shutil
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("controls", type=Path)
parser.add_argument("demo", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
replacements = [
    (str(args.controls.resolve()), "<CONTROLS_DIR>"),
    (str(args.demo.resolve()), "<DEMO_DIR>"),
    (str(Path.cwd()), "<SOURCE_DIR>"),
]


def sanitized(text):
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def copy_text(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sanitized(source.read_text()))


controls = json.loads((args.controls / "controls.json").read_text())
copy_text(args.controls / "controls.json", args.output / "controls.json")
for name in ["clean", "identity", "modified", "target", "incomplete", "invalid"]:
    batch = Path(controls["reports"][name])
    report = json.loads((batch / "report.json").read_text())
    assert report["policy"] == "scripted"
    dest = args.output / name
    copy_text(batch / "report.json", dest / "report.json")
    copy_text(batch / "candidate.json", dest / "candidate.json")
    if name in ["clean", "modified"]:
        episode = batch / "episode-0"
        for path in episode.glob("observation-*.json"):
            copy_text(path, dest / path.name)
        copy_text(episode / "episode.json", dest / "episode.json")
        shutil.copy2(episode / "screenshot-0.png", dest / "screenshot-0.png")
for name in ["summary.json", "autohelix-history.jsonl", "project-start.json"]:
    copy_text(args.demo / name, args.output / "integration" / name)
for report_path in (args.demo / "batches").glob("*/report.json"):
    report = json.loads(report_path.read_text())
    assert report["policy"] == "scripted"
    decision = json.loads((report_path.parent / "acceptance.json").read_text())
    dest = args.output / "integration" / f"iteration-{decision['iteration']}"
    for name in [
        "report.json",
        "candidate.json",
        "candidate.diff",
        "acceptance.json",
        "change-from-accepted.diff",
    ]:
        if (report_path.parent / name).exists():
            copy_text(report_path.parent / name, dest / name)
for path in (args.demo / "autohelix-logs/iter-2").glob("*.feedback.json"):
    copy_text(path, args.output / "integration" / "iteration-2-feedback.json")
print("Exported reviewed scripted evidence only.")
