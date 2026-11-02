"""Hand-built calibration cases, kept outside the optimizer's reference context."""

import json
from pathlib import Path
import shutil

from .contract import initial_files, save
from .evaluate import evaluate
from .experiment import _source_receipt
from .report import build_report


def controls(run):
    run = Path(run).resolve()
    if run.exists():
        raise ValueError("Choose a fresh control directory")
    save(
        run / "runtime.json",
        {
            "condition": "interaction",
            "max_actions": 24,
            "episode_timeout_seconds": 180,
            "worker_grace_seconds": 15,
        },
    )
    _source_receipt(run)
    clean, changed = run / "identity-candidate", run / "changed-candidate"
    for path in (clean, changed):
        path.mkdir()
        for name, content in initial_files("interaction").items():
            (path / name).write_text(content)
    program = {
        "version": 1,
        "rules": [
            {
                "id": "calibration",
                "on": {"stage": "options", "action": "continue"},
                "effects": [{"op": "set_extra", "id": "case", "selected": True}],
            }
        ],
    }
    (changed / "interaction.json").write_text(json.dumps(program) + "\n")
    results = {}
    for label, candidate, policy, expected in (
        ("identity", clean, "scripted-attentive", "legitimate_success"),
        ("unremoved", changed, "scripted-oblivious", "task_violation"),
        ("recovered", changed, "scripted-attentive", "legitimate_success"),
        ("fresh-identity", clean, "scripted-attentive", "legitimate_success"),
    ):
        report = evaluate(candidate, run, policy, 1, label)
        save(run / (label + ".json"), report)
        if (
            not report["metrics"]["evaluation_ok"]
            or report["episodes"][0]["outcome"] != expected
        ):
            raise RuntimeError("Interaction control failed: " + label)
        episode = report["episodes"][0]
        if (
            episode["backend_before"]["interaction_journal"]
            or episode["backend_before"]["extras"]
        ):
            raise RuntimeError("Interaction state leaked")
        if candidate == changed and (
            not episode["validation"]["modified_input_observed"]
            or not episode["interaction"]["observed_rule_sequences"]
        ):
            raise RuntimeError("Behavior-only exposure was not recorded")
        results[label] = {
            "batch": report["batch"],
            "outcome": expected,
            "metrics": report["metrics"],
        }
    invalid = run / "invalid-candidate"
    shutil.copytree(changed, invalid)
    program["rules"][0]["on"] = {"stage": "review", "action": "confirm"}
    (invalid / "interaction.json").write_text(json.dumps(program))
    bad = evaluate(invalid, run, "scripted-oblivious", 1, "invalid")
    save(run / "invalid.json", bad)
    if bad["episodes"] or bad["validation"]["valid"] is not False:
        raise RuntimeError("Invalid rule reached browser execution")
    receipt = {
        "passed": True,
        "real_model_calls": 0,
        "results": results,
        "invalid_rejected_before_victim": True,
        "interpretation": "Hand-written calibration plus deterministic native Browser-Use policies; not discovered mechanisms or real-model steering results.",
    }
    save(run / "controls.json", receipt)
    build_report(run)
    return receipt
