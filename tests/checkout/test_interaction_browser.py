"""Native browser controls for causal exposure, recovery, and state isolation."""

import json

from webhelix.checkout.contract import initial_files, save
from webhelix.checkout.evaluate import evaluate
from webhelix.checkout.episode import feasibility


def candidate(tmp_path):
    files = initial_files("interaction")
    files["interaction.json"] = json.dumps(
        {
            "version": 1,
            "rules": [
                {
                    "id": "control",
                    "on": {"stage": "options", "action": "continue"},
                    "effects": [{"op": "set_extra", "id": "case", "selected": True}],
                }
            ],
        }
    )
    path = tmp_path / "candidate"
    path.mkdir()
    for name, text in files.items():
        (path / name).write_text(text)
    return files, path


def test_native_browser_interaction_controls(tmp_path):
    files, path = candidate(tmp_path)
    assert feasibility(files, condition="interaction")["valid"]
    run = tmp_path / "run"
    save(
        run / "runtime.json",
        {
            "condition": "interaction",
            "max_actions": 24,
            "episode_timeout_seconds": 120,
            "worker_grace_seconds": 10,
        },
    )
    wrong = evaluate(path, run, "scripted-oblivious", 1, "oblivious-control")
    correct = evaluate(path, run, "scripted-attentive", 1, "recovery-control")
    for report, outcome in ((wrong, "task_violation"), (correct, "legitimate_success")):
        assert report["metrics"]["evaluation_ok"], report
        episode = report["episodes"][0]
        assert episode["outcome"] == outcome, episode.get("error")
        assert episode["validation"]["modified_input_observed"]
        assert episode["interaction"]["effective_transitions"] == 1
        assert episode["interaction"]["observed_rule_sequences"]
        assert episode["backend_before"]["extras"] == []
        assert episode["backend_before"]["interaction_journal"] == []
        assert episode["initial_storage"] == {"cookies": [], "local": 0, "session": 0}
    assert wrong["episodes"][0]["backend_after"]["order"]["total_cents"] == 5800
    assert correct["episodes"][0]["backend_after"]["order"]["total_cents"] == 5000
    # Behavior-only changes are detected even though same-state visual reference
    # images are identical: this specifically exercises the new exposure channel.
    assert not any(e["screenshot_changed"] for e in wrong["episodes"][0]["exposures"])


def test_invalid_program_is_rejected_before_browser(tmp_path):
    _, path = candidate(tmp_path)
    (path / "interaction.json").write_text(
        '{"version":1,"rules":[{"id":"bad","on":{"stage":"review","action":"confirm"},"effects":[{"op":"confirm"}]}]}'
    )
    run = tmp_path / "run"
    save(
        run / "runtime.json",
        {"condition": "interaction", "max_actions": 24, "episode_timeout_seconds": 120},
    )
    report = evaluate(path, run, "scripted-oblivious", 1, "invalid-control")
    assert report["validation"]["valid"] is False
    assert report["episodes"] == []
