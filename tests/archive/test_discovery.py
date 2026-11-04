import json

import pytest

from webhelix.archive.cli import main
from webhelix.checkout.discovery import prepare_discovery, execute_discovery
from webhelix.checkout.contract import FIXTURES, read, save


def test_current_retention_policy_does_not_rewrite_captured_evaluation(
    archive_root, tmp_path
):
    import hashlib
    import time
    from webhelix.checkout.contract import save
    from webhelix.checkout.experiment import prepare

    source = (archive_root / "evaluation.json").read_bytes()
    run = prepare_discovery(tmp_path / "run", scripted=True, archive_root=archive_root)
    settings = read(run / "runtime.json")
    settings["run_deadline_epoch"] = time.time() + 300
    save(run / "runtime.json", settings)
    project = prepare(run, "fixture")
    current = read(project / "EVALUATION.json")
    receipt = read(run / "evaluation-policy.json")
    assert (archive_root / "evaluation.json").read_bytes() == source
    assert (run / "archive/evaluation.json").read_bytes() == source
    assert receipt["source_evaluation_sha256"] == hashlib.sha256(source).hexdigest()
    assert current == receipt["effective_evaluation"]
    assert current["measurement_protocol"] == "artifact-outcome-v1"
    assert current["acceptance"]["activation_is_descriptive_not_a_gate"]
    assert not current["acceptance"]["screenshot_comparison_required"]
    for name, value in json.loads(source).items():
        if name != "acceptance":
            assert current[name] == value


def test_preparation_freezes_protected_archive_and_preserves_current_harness(
    archive_root, tmp_path
):
    run = prepare_discovery(tmp_path / "run", scripted=True, archive_root=archive_root)
    settings = read(run / "runtime.json")
    status = read(run / "status.json")
    assert settings["archive_root"] == str(run / "archive")
    assert settings["environment"] == "arena-archive"
    assert settings["condition"] == "archive"
    assert settings["optimizer_backend"] == "opencode"
    assert settings["optimizer_settings"]["model"] == "gpt-5.4-mini-2026-03-17"
    assert settings["victim"]["max_input_bytes"] == 2_000_000
    assert settings["victim"]["model"] == "gpt-6.1-sol"
    assert settings["victim"]["reasoning_effort"] == "medium"
    assert settings["max_real_episodes"] == 0
    assert status["maximum_scheduled_episodes"] == 7
    assert status["maximum_optimizer_sessions"] == 2
    assert status["protected_environment_hashes"]["assets/core.css"]
    assert not (run / "budget.json").exists()
    initial = (run / "archive/assets/core.css").read_text()
    (archive_root / "assets/core.css").write_text("changed original outside frozen run")
    assert (run / "archive/assets/core.css").read_text() == initial


def test_archive_payload_allowance_does_not_change_checkout_defaults(tmp_path):
    run = prepare_discovery(tmp_path / "checkout", scripted=True)
    assert read(run / "runtime.json")["victim"]["max_input_bytes"] == 500_000


def test_frozen_archive_mutation_blocks_before_browser_or_provider(
    archive_root, tmp_path, monkeypatch
):
    run = prepare_discovery(tmp_path / "run", scripted=True, archive_root=archive_root)
    (run / "archive/assets/core.css").write_text("body{display:none}")
    monkeypatch.setattr(
        "webhelix.checkout.discovery.DockerRuntime.preflight",
        lambda: pytest.fail("Mutated archive reached Docker"),
    )
    with pytest.raises(ValueError, match="changed"):
        execute_discovery(run)
    assert read(run / "status.json")["status"] == "prepared"
    assert not (run / "budget.json").exists()


def test_archive_cli_defaults_to_no_model_preparation(archive_root, tmp_path):
    run = tmp_path / "run"
    main(
        [
            "discovery",
            "--prepare",
            "--archive-root",
            str(archive_root),
            "--run-dir",
            str(run),
        ]
    )
    assert read(run / "status.json")["scripted"] is True
    assert read(run / "runtime.json")["policy"] == "scripted-correct"


def test_archive_cli_accepts_custom_three_iteration_configuration(
    archive_root, tmp_path
):
    pilot = read(FIXTURES / "discovery-pilot.json")
    pilot.update(
        iterations=3, max_real_episodes=10, max_optimizer_calls=60, max_actions=40
    )
    pilot["victim"]["model"] = "unit-victim"
    optimizer = read(FIXTURES / "opencode-discovery.example.json")
    optimizer["model"] = "unit-optimizer"
    pilot_path, optimizer_path = tmp_path / "pilot.json", tmp_path / "optimizer.json"
    save(pilot_path, pilot)
    save(optimizer_path, optimizer)
    run = tmp_path / "run"
    main(
        [
            "discovery",
            "--prepare",
            "--archive-root",
            str(archive_root),
            "--run-dir",
            str(run),
            "--pilot-config",
            str(pilot_path),
            "--optimizer-config",
            str(optimizer_path),
        ]
    )
    settings, status = read(run / "runtime.json"), read(run / "status.json")
    assert status["scripted"] is True
    assert status["maximum_scheduled_episodes"] == 10
    assert status["maximum_optimizer_requests"] == 60
    assert settings["iterations"] == 3 and settings["max_actions"] == 40
    assert settings["victim"]["model"] == "unit-victim"
    assert settings["optimizer_settings"]["model"] == "unit-optimizer"
    assert settings["victim"]["max_input_bytes"] == 2_000_000
    assert settings["max_real_episodes"] == 0
    assert not (run / "budget.json").exists()


def test_archive_cli_does_not_execute_checkout_run(tmp_path):
    run = prepare_discovery(tmp_path / "run", scripted=True)
    with pytest.raises(SystemExit):
        main(["discovery", "--execute", "--run-dir", str(run)])
    assert read(run / "status.json")["status"] == "prepared"


def test_real_preparation_never_infers_offline_accounting_as_authorization(
    archive_root, tmp_path
):
    key = tmp_path / "private.env"
    key.write_text("WEBHELIX_API_KEY=fixture-not-real\n")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"offline_fixture": True, "authorized_usd": 10}))
    with pytest.raises(ValueError, match="fixture budget"):
        prepare_discovery(
            tmp_path / "run",
            scripted=False,
            archive_root=archive_root,
            env_file=key,
            spending_plan=plan,
        )


def test_archive_cli_baseline_only_dispatch_and_prepare_rejection(
    archive_root, tmp_path, monkeypatch
):
    run = prepare_discovery(tmp_path / "run", scripted=True, archive_root=archive_root)
    calls = []
    monkeypatch.setattr(
        "webhelix.checkout.discovery.execute_discovery",
        lambda root, **kw: calls.append((root, kw)) or {"status": "baseline_completed"},
    )
    main(["discovery", "--execute", "--baseline-only", "--run-dir", str(run)])
    assert calls == [(run, {"baseline_only": True})]
    with pytest.raises(SystemExit):
        main(
            [
                "discovery",
                "--prepare",
                "--baseline-only",
                "--run-dir",
                str(tmp_path / "other"),
                "--archive-root",
                str(archive_root),
            ]
        )
    assert not (tmp_path / "other").exists()


def test_one_iteration_preparation_preserves_total_episode_allowance(
    archive_root, tmp_path
):
    secret = tmp_path / "private.env"
    secret.write_text("WEBHELIX_API_KEY=fixture-not-real\n")
    run = prepare_discovery(
        tmp_path / "run",
        scripted=False,
        env_file=secret,
        archive_root=archive_root,
        iteration_limit=1,
    )
    settings, status = read(run / "runtime.json"), read(run / "status.json")
    assert settings["iterations"] == status["maximum_optimizer_sessions"] == 1
    assert settings["episodes_per_condition"] == 3
    assert status["maximum_scheduled_episodes"] == 4
    assert settings["max_optimizer_calls"] == status["maximum_optimizer_requests"] == 20
    assert settings["max_real_episodes"] == status["maximum_real_episode_starts"] == 7
    assert status["baseline_episodes"] == 1
    assert settings["victim"]["model"] == "gpt-6.1-sol"
    assert not (run / "budget.json").exists()
    assert not (run / "batches").exists()


@pytest.mark.parametrize("limit", [True, False, 0, -1, 3, 1.0, "1"])
def test_iteration_limit_cannot_increase_or_invalidate_frozen_pilot(tmp_path, limit):
    run = tmp_path / "run"
    with pytest.raises(ValueError, match="Iteration limit"):
        prepare_discovery(run, scripted=True, iteration_limit=limit)
    assert not run.exists()


def test_iteration_override_does_not_bypass_original_configuration_check(
    tmp_path, monkeypatch
):
    from webhelix.checkout import discovery

    original = discovery._pilot_settings

    def invalid(*args, **kwargs):
        settings = original(*args, **kwargs)
        settings["max_optimizer_calls"] = 999
        return settings

    monkeypatch.setattr(discovery, "_pilot_settings", invalid)
    with pytest.raises(ValueError, match="Discovery limits must match"):
        prepare_discovery(tmp_path / "run", scripted=True, iteration_limit=1)
    assert not (tmp_path / "run").exists()


def test_archive_cli_iteration_limit_is_preparation_only(
    archive_root, tmp_path, monkeypatch
):
    run = tmp_path / "run"
    main(
        [
            "discovery",
            "--prepare",
            "--iterations",
            "1",
            "--archive-root",
            str(archive_root),
            "--run-dir",
            str(run),
        ]
    )
    assert read(run / "runtime.json")["iterations"] == 1
    assert read(run / "status.json")["maximum_scheduled_episodes"] == 4
    assert read(run / "status.json")["maximum_real_episode_starts"] == 0
    monkeypatch.setattr(
        "webhelix.checkout.discovery.execute_discovery",
        lambda *a, **kw: pytest.fail(
            "Execute cannot override the frozen iteration count"
        ),
    )
    with pytest.raises(SystemExit):
        main(["discovery", "--execute", "--iterations", "1", "--run-dir", str(run)])
    assert read(run / "status.json")["status"] == "prepared"
