import time

import pytest

from webhelix.checkout.discovery import prepare_discovery, execute_discovery
from webhelix.checkout.contract import FIXTURES, initial_files, fingerprint, read, save
from webhelix.checkout.evaluate import evaluate
from webhelix.search.budget import Budget, BudgetBlocked
from webhelix.intervention import describe_changes


def test_identity_is_same_across_conditions():
    assert (
        len(
            {
                fingerprint(initial_files(c))
                for c in ("presentation", "interaction", "combined")
            }
        )
        == 1
    )


def test_live_prepare_is_offline_and_null_budget_blocks_before_browser(
    tmp_path, monkeypatch
):
    secret = tmp_path / "private.env"
    secret.write_text("WEBHELIX_API_KEY=not-a-real-key\n")
    run = prepare_discovery(tmp_path / "run", scripted=False, env_file=secret)
    assert read(run / "status.json")["status"] == "prepared"
    assert not (run / "budget.json").exists()

    def forbidden():
        pytest.fail("Docker/model work reached before spending-plan validation")

    monkeypatch.setattr(
        "webhelix.checkout.discovery.DockerRuntime.preflight", forbidden
    )
    with pytest.raises(BudgetBlocked):
        execute_discovery(run)
    assert read(run / "status.json")["status"] == "prepared"
    assert not (run / "batches").exists()
    with pytest.raises(ValueError, match="fresh"):
        prepare_discovery(run, scripted=True)


def test_single_search_configuration_has_no_identity_or_arm(tmp_path):
    run = prepare_discovery(tmp_path / "run", scripted=True)
    settings, status = read(run / "runtime.json"), read(run / "status.json")
    assert settings["condition"] == "combined" and settings["iterations"] == 2
    assert settings["episodes_per_condition"] == 3
    assert settings["max_optimizer_calls"] == 40
    assert settings["max_real_episodes"] == 0
    assert status["maximum_scheduled_episodes"] == 7
    assert settings["optimizer_settings"]["model"] == "gpt-5.4-mini-2026-03-17"
    assert settings["optimizer_settings"]["context_window_tokens"] == 400000
    assert settings["victim"]["model"] == "gpt-6.1-sol"
    assert settings["max_actions"] == 24
    assert settings["memory_policy"] == "summary-with-readonly-evidence-v1"
    assert settings["optimizer_feedback_protocol"] == "behavioral-v1"
    assert settings["measurement_protocol"] == "artifact-outcome-v1"
    assert status["baseline_episodes"] == 1
    assert not (run / "identity.json").exists() and not (run / "arms").exists()
    assert fingerprint(initial_files("combined")) == fingerprint(
        {p.name: p.read_text() for p in (run / "reference/candidate").iterdir()}
    )


def custom_discovery_configs(tmp_path):
    pilot = read(FIXTURES / "discovery-pilot.json")
    pilot.update(
        iterations=3, max_real_episodes=10, max_optimizer_calls=24, max_actions=40
    )
    pilot["victim"].update(model="unit-victim", reasoning_effort="low")
    optimizer = read(FIXTURES / "opencode-discovery.example.json")
    optimizer.update(
        model="unit-optimizer",
        reasoning_effort="high",
        max_requests_per_session=8,
        context_window_tokens=200_000,
    )
    pilot_path, optimizer_path = tmp_path / "pilot.json", tmp_path / "optimizer.json"
    save(pilot_path, pilot)
    save(optimizer_path, optimizer)
    return pilot_path, optimizer_path


def test_custom_three_iteration_live_preparation_freezes_model_and_session_settings(
    tmp_path,
):
    pilot, optimizer = custom_discovery_configs(tmp_path)
    private = tmp_path / "private.env"
    private.write_text("WEBHELIX_API_KEY=unit-test-not-a-real-key\n")
    run = prepare_discovery(
        tmp_path / "run",
        scripted=False,
        env_file=private,
        pilot_config=pilot,
        optimizer_config=optimizer,
    )
    settings, status = read(run / "runtime.json"), read(run / "status.json")
    assert settings["iterations"] == status["maximum_optimizer_sessions"] == 3
    assert settings["max_real_episodes"] == status["maximum_scheduled_episodes"] == 10
    assert settings["max_optimizer_calls"] == status["maximum_optimizer_requests"] == 24
    assert settings["max_actions"] == 40
    assert settings["victim"]["model"] == "unit-victim"
    assert settings["victim"]["reasoning_effort"] == "low"
    assert settings["optimizer_settings"] == read(optimizer)
    assert read(run / "optimizer-settings.json") == read(optimizer)
    assert not (run / "budget.json").exists() and not (run / "batches").exists()


def test_custom_config_files_are_not_reread_during_execution(tmp_path, monkeypatch):
    from webhelix.checkout.discovery import main

    pilot, optimizer = custom_discovery_configs(tmp_path)
    run = tmp_path / "run"
    main(
        [
            "--prepare",
            "--scripted",
            "--run-dir",
            str(run),
            "--pilot-config",
            str(pilot),
            "--optimizer-config",
            str(optimizer),
        ]
    )
    frozen = read(run / "runtime.json")
    pilot.unlink()
    optimizer.unlink()
    seen = []

    def clean(candidate, root, policy, episodes, label, *, purpose):
        seen.append(read(root / "runtime.json"))
        return successful_clean(root)

    monkeypatch.setattr("webhelix.checkout.discovery.evaluate", clean)
    monkeypatch.setattr(
        "webhelix.checkout.discovery.DockerRuntime.preflight",
        lambda: pytest.fail("Baseline must not launch Docker"),
    )
    result = execute_discovery(run, baseline_only=True)
    assert result["status"] == "baseline_completed"
    assert seen[0]["victim"] == frozen["victim"]
    assert seen[0]["optimizer_settings"] == frozen["optimizer_settings"]
    assert seen[0]["iterations"] == 3 and seen[0]["max_actions"] == 40


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_real_episodes", 11),
        ("max_optimizer_calls", 25),
        ("episodes_per_condition", 1),
        ("max_actions", 41),
        ("max_actions", 0),
        ("max_actions", True),
    ],
)
def test_custom_pilot_does_not_bypass_schedule_or_action_bounds(tmp_path, field, value):
    pilot, optimizer = custom_discovery_configs(tmp_path)
    settings = read(pilot)
    settings[field] = value
    save(pilot, settings)
    run = tmp_path / "run"
    with pytest.raises(ValueError):
        prepare_discovery(
            run, scripted=True, pilot_config=pilot, optimizer_config=optimizer
        )
    assert not run.exists()


def test_invalid_custom_optimizer_is_rejected_before_preparation(tmp_path):
    pilot, optimizer = custom_discovery_configs(tmp_path)
    settings = read(optimizer)
    settings["max_requests_per_session"] = 21
    save(optimizer, settings)
    with pytest.raises(ValueError, match="max_requests_per_session"):
        prepare_discovery(
            tmp_path / "run",
            scripted=True,
            pilot_config=pilot,
            optimizer_config=optimizer,
        )
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("entrypoint", ["checkout", "archive"])
@pytest.mark.parametrize(
    "flag",
    [
        "--pilot-config",
        "--optimizer-config",
        "--env-file",
        "--spending-plan",
        "--scripted",
    ],
)
def test_discovery_execution_rejects_prepare_overrides(
    tmp_path, monkeypatch, capsys, entrypoint, flag
):
    from webhelix.archive.cli import main as archive_main
    from webhelix.checkout.discovery import main as checkout_main

    monkeypatch.setattr(
        "webhelix.checkout.discovery.execute_discovery",
        lambda *a, **kw: pytest.fail("Execution override was silently ignored"),
    )
    args = ["--execute", "--run-dir", str(tmp_path / "missing"), flag]
    if flag != "--scripted":
        args.append(str(tmp_path / "unused.json"))
    with pytest.raises(SystemExit) as exc:
        (archive_main if entrypoint == "archive" else checkout_main)(
            (["discovery"] if entrypoint == "archive" else []) + args
        )
    assert exc.value.code == 2
    assert "previously frozen run settings" in capsys.readouterr().err


def test_failed_clean_smoke_stops_before_optimizer(tmp_path, monkeypatch):
    run = prepare_discovery(tmp_path / "run", scripted=True)
    monkeypatch.setattr(
        "webhelix.checkout.discovery.DockerRuntime.preflight", lambda: {}
    )
    calls = []

    def failed(candidate, root, policy, episodes, label, *, purpose):
        calls.append((episodes, label, purpose))
        return {"metrics": {"evaluation_ok": True, "legitimate_success_rate": 0}}

    monkeypatch.setattr("webhelix.checkout.discovery.evaluate", failed)
    monkeypatch.setattr(
        "webhelix.checkout.discovery.prepare",
        lambda *a: pytest.fail("No optimizer/project after failed baseline"),
    )
    with pytest.raises(RuntimeError, match="Clean smoke check failed"):
        execute_discovery(run)
    assert calls == [(1, "clean", "clean_smoke")]
    assert read(run / "status.json")["status"] == "stopped"
    assert not (run / "proposals").exists()
    with pytest.raises(ValueError, match="already attempted"):
        execute_discovery(run)


def test_prepared_runtime_changes_block_before_any_execution(tmp_path, monkeypatch):
    run = prepare_discovery(tmp_path / "run", scripted=True)
    settings = read(run / "runtime.json")
    settings["max_real_episodes"] = 99
    save(run / "runtime.json", settings)
    monkeypatch.setattr(
        "webhelix.checkout.discovery.DockerRuntime.preflight",
        lambda: pytest.fail("Changed plan reached Docker"),
    )
    with pytest.raises(ValueError, match="configuration changed"):
        execute_discovery(run)


def test_live_context_accounting_must_match_optimizer_metadata(tmp_path, monkeypatch):
    secret = tmp_path / "private.env"
    secret.write_text("WEBHELIX_API_KEY=not-a-real-key\n")
    run = prepare_discovery(tmp_path / "run", scripted=False, env_file=secret)
    plan = read(run / "spending-plan.json")
    plan.update(authorized_usd=10, verified_on="test")
    for role, model in (
        ("optimizer", "gpt-5.4-mini-2026-03-17"),
        ("victim", "gpt-6.1-sol"),
    ):
        plan["roles"][role].update(
            model=model,
            input_usd_per_million=1,
            output_usd_per_million=1,
            maximum_context_input_tokens=1050000,
            pricing_source="test",
            context_source="test",
        )
    save(run / "spending-plan.json", plan)
    monkeypatch.setattr(
        "webhelix.checkout.discovery.DockerRuntime.preflight",
        lambda: pytest.fail("Mismatched context reached Docker"),
    )
    with pytest.raises(ValueError, match="context bounds disagree"):
        execute_discovery(run)
    assert read(run / "status.json")["status"] == "prepared"
    assert not (run / "budget.json").exists()


def test_one_api_episode_is_only_for_pristine_single_use_smoke(tmp_path, monkeypatch):
    run = prepare_discovery(tmp_path / "run", scripted=True)
    candidate = run / "reference/candidate"
    calls = []
    monkeypatch.setattr(
        "webhelix.checkout.evaluate._evaluate",
        lambda *a: calls.append(a) or {"mock": True},
    )
    with pytest.raises(ValueError, match="three fresh episodes"):
        evaluate(candidate, run, "api", 1)
    # Even the named exception cannot be used to evaluate an optimizer change.
    css = candidate / "style.css"
    clean_css = css.read_text()
    css.write_text(clean_css + "\nmain {border:2px solid blue;}\n")
    with pytest.raises(ValueError, match="pristine"):
        evaluate(candidate, run, "api", 1, "clean", purpose="clean_smoke")
    assert not calls and not (run / "clean-smoke-attempt.json").exists()
    css.write_text(clean_css)
    evaluate(candidate, run, "api", 1, "clean", purpose="clean_smoke")
    assert len(calls) == 1
    with pytest.raises(ValueError, match="already attempted"):
        evaluate(candidate, run, "api", 1, "clean", purpose="clean_smoke")


def test_single_run_counts_all_seven_started_episodes(tmp_path):
    settings = {"max_real_episodes": 7, "max_optimizer_calls": 40, "max_actions": 24}
    budget = Budget(tmp_path, settings)
    budget.episode("clean/episode-0")
    for iteration in range(1, 3):
        for episode in range(3):
            budget.episode(f"iter-{iteration}/episode-{episode}")
    with pytest.raises(BudgetBlocked, match="episode limit"):
        budget.episode("unapproved-retry")
    assert len(read(tmp_path / "budget.json")["episodes"]) == 7


def test_all_arms_share_one_financial_reservation_and_episode_cap(tmp_path):
    root = tmp_path / "shared"
    common = {
        "max_real_episodes": 2,
        "max_optimizer_calls": 4,
        "max_actions": 24,
        "run_deadline_epoch": time.time() + 60,
    }
    save(root / "runtime.json", common)
    role = {
        "model": "fixture",
        "input_usd_per_million": 1,
        "output_usd_per_million": 1,
        "maximum_context_input_tokens": 100,
        "pricing_source": "test",
        "context_source": "test",
    }
    save(
        root / "spending-plan.json",
        {
            "authorized_usd": 0.00015,
            "verified_on": "test",
            "roles": {"optimizer": role, "victim": role},
        },
    )
    a = Budget(root / "a", {**common, "budget_root": str(root)})
    b = Budget(root / "b", {**common, "budget_root": str(root)})
    a.reserve("optimizer", "fixture", 10)
    with pytest.raises(BudgetBlocked, match="remaining"):
        b.reserve("optimizer", "fixture", 10)
    a.episode("first")
    b.episode("second")
    with pytest.raises(BudgetBlocked, match="episode limit"):
        a.episode("third")
    ledger = read(root / "budget.json")
    assert len(ledger["calls"]) == 1 and len(ledger["episodes"]) == 2
    assert ledger["episodes"][0]["group"] != ledger["episodes"][1]["group"]


def successful_clean(root, *, clock=None):
    """Saved evaluator output, with no browser or provider involved."""
    batch = root / "batches/clean-fixture"
    result = {
        "batch": str(batch),
        "intervention": describe_changes({}, {}),
        "metrics": {
            "evaluation_ok": True,
            "legitimate_success_rate": 1,
            "candidate_changed_episodes": 0,
            "steering_rate": 0,
            "core_completion_rate": 1,
        },
    }
    save(root / "clean-smoke-attempt.json", {"purpose": "clean_smoke"})
    save(batch / "report.json", result)
    save(batch / "episode-0/episode.json", {"outcome": "legitimate_success"})
    save(root / "budget.json", {"episodes": [{"label": "clean"}], "calls": []})
    if clock is not None:
        clock[0] += 5
    return result


@pytest.mark.parametrize("changed", [False, True, None])
def test_clean_gate_uses_artifacts_not_legacy_pixel_flags(
    tmp_path, monkeypatch, changed
):
    run = prepare_discovery(tmp_path / "run", scripted=True)

    def clean(*args, **kwargs):
        result = successful_clean(run)
        result["intervention"]["candidate_changed"] = changed
        # Historical image mismatch data has no authority over candidate provenance.
        result["metrics"]["changed_input_episodes"] = 1
        return result

    monkeypatch.setattr("webhelix.checkout.discovery.evaluate", clean)
    if changed is False:
        assert (
            execute_discovery(run, baseline_only=True)["status"] == "baseline_completed"
        )
    else:
        with pytest.raises(RuntimeError, match="Clean smoke check failed"):
            execute_discovery(run, baseline_only=True)


def test_baseline_only_stops_without_docker_or_optimizer(tmp_path, monkeypatch):
    run = prepare_discovery(tmp_path / "run", scripted=True)
    calls = []

    def clean(candidate, root, policy, episodes, label, *, purpose):
        calls.append((candidate, root, policy, episodes, label, purpose))
        return successful_clean(root)

    monkeypatch.setattr("webhelix.checkout.discovery.evaluate", clean)
    for target in ("DockerRuntime.preflight", "prepare", "CheckoutOpenCodeBackend"):
        monkeypatch.setattr(
            "webhelix.checkout.discovery." + target,
            lambda *a, **kw: pytest.fail("Baseline must not start optimizer or Docker"),
        )
    status = execute_discovery(run, baseline_only=True)
    assert status["status"] == "baseline_completed"
    assert status["attempted_iterations"] == 0
    assert len(calls) == 1 and calls[0][3:] == (1, "clean", "clean_smoke")
    assert status["baseline_evidence_hashes"]["budget.json"]
    assert not (run / "project").exists()
    with pytest.raises(ValueError, match="already attempted"):
        execute_discovery(run, baseline_only=True)


def test_resume_reuses_baseline_ledger_and_excludes_pause_time(tmp_path, monkeypatch):
    from autohelix.history import IterationResult
    from webhelix.checkout import discovery

    clock = [1000.0]
    monkeypatch.setattr(discovery.time, "time", lambda: clock[0])
    run = prepare_discovery(tmp_path / "run", scripted=True)
    calls = []
    monkeypatch.setattr(
        discovery,
        "evaluate",
        lambda *args, **kw: calls.append(args) or successful_clean(run, clock=clock),
    )
    first = execute_discovery(run, baseline_only=True)
    assert first["elapsed_seconds"] == 5
    old_ledger = read(run / "budget.json")
    monkeypatch.setattr(
        discovery, "evaluate", lambda *a, **kw: pytest.fail("Baseline repeated")
    )
    monkeypatch.setattr(discovery.DockerRuntime, "preflight", lambda: {})

    def project(root, model):
        folder = root / "project/candidate"
        folder.mkdir(parents=True)
        for path in (root / "reference/candidate").iterdir():
            (folder / path.name).write_bytes(path.read_bytes())
        return folder.parent

    monkeypatch.setattr(discovery, "prepare", project)
    monkeypatch.setattr(discovery, "CheckoutOpenCodeBackend", lambda *a, **kw: object())
    monkeypatch.setattr(discovery, "feedback_continuity", lambda *a: {"passed": True})

    class Harness:
        def __init__(self, project, root):
            self.rows = []
            self.history = self

        def append(self, row):
            self.rows.append(row)

        def load(self):
            return self.rows

        def run(self, max_iterations):
            assert read(run / "budget.json") == old_ledger
            runtime = read(run / "runtime.json")
            assert runtime["run_deadline_epoch"] == clock[0] + 7195
            assert runtime["max_optimizer_calls"] == 40
            budget = Budget(run, runtime)
            call = budget.reserve(
                "optimizer", runtime["optimizer_settings"]["model"], 10
            )
            budget.settle(call, {"input_tokens": 0, "output_tokens": 0})
            for i in range(1, max_iterations + 1):
                self.rows.append(
                    IterationResult(iteration=i, accepted=False, metrics={})
                )
                save(
                    run / f"feedback/iter-{i}/result.json",
                    {
                        "iteration": i,
                        "accepted": False,
                        "metrics": {"evaluation_ok": True},
                    },
                )
            clock[0] += 7

    monkeypatch.setattr(discovery, "CheckoutHarness", Harness)
    clock[0] = 10000.0  # The human review pause does not consume execution time.
    result = execute_discovery(run)
    assert result["status"] == "completed"
    assert result["elapsed_seconds"] == 12
    assert len(calls) == 1
    ledger = read(run / "budget.json")
    assert ledger["episodes"] == old_ledger["episodes"]
    assert len(ledger["calls"]) == 1 and ledger["calls"][0]["role"] == "optimizer"
    assert result["real_episode_starts"] == 1
    with pytest.raises(ValueError, match="already attempted"):
        execute_discovery(run)


@pytest.mark.parametrize(
    "target",
    ["receipt", "trace", "ledger", "plan", "candidate", "settings", "deadline"],
)
def test_resume_refuses_tampered_baseline_or_settings(tmp_path, monkeypatch, target):
    from webhelix.checkout import discovery

    run = prepare_discovery(tmp_path / "run", scripted=True)
    monkeypatch.setattr(discovery, "evaluate", lambda *a, **kw: successful_clean(run))
    execute_discovery(run, baseline_only=True)
    if target == "receipt":
        clean = read(run / "clean.json")
        clean["metrics"]["steering_rate"] = 1
        save(run / "clean.json", clean)
    elif target == "trace":
        save(
            run / "batches/clean-fixture/episode-0/episode.json",
            {"outcome": "invented"},
        )
    elif target == "ledger":
        save(run / "budget.json", {"episodes": [], "calls": []})
    elif target == "plan":
        plan = read(run / "spending-plan.json")
        plan["authorized_usd"] = 100
        save(run / "spending-plan.json", plan)
    elif target == "candidate":
        (run / "reference/candidate/style.css").write_text("body{display:none}")
    else:
        settings = read(run / "runtime.json")
        settings["max_actions" if target == "settings" else "run_deadline_epoch"] += 1
        save(run / "runtime.json", settings)
    monkeypatch.setattr(
        discovery.DockerRuntime,
        "preflight",
        lambda: pytest.fail("Tamper reached Docker"),
    )
    monkeypatch.setattr(
        discovery, "evaluate", lambda *a, **kw: pytest.fail("New baseline")
    )
    with pytest.raises(ValueError, match="changed"):
        execute_discovery(run)
    assert read(run / "status.json")["status"] == "baseline_completed"


def test_failed_baseline_only_cannot_resume(tmp_path, monkeypatch):
    from webhelix.checkout import discovery

    run = prepare_discovery(tmp_path / "run", scripted=True)
    monkeypatch.setattr(
        discovery,
        "evaluate",
        lambda *a, **kw: {"metrics": {"evaluation_ok": False}},
    )
    monkeypatch.setattr(
        discovery.DockerRuntime, "preflight", lambda: pytest.fail("Unexpected Docker")
    )
    with pytest.raises(RuntimeError, match="Clean smoke check failed"):
        execute_discovery(run, baseline_only=True)
    assert read(run / "status.json")["status"] == "stopped"
    with pytest.raises(ValueError, match="already attempted"):
        execute_discovery(run)
