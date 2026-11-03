import time

import pytest

from webhelix.checkout.discovery import prepare_discovery, execute_discovery
from webhelix.checkout.contract import initial_files, fingerprint, read, save
from webhelix.checkout.evaluate import evaluate
from webhelix.search.budget import Budget, BudgetBlocked


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
    assert settings["memory_policy"] == "summary-with-readonly-evidence-v1"
    assert settings["optimizer_feedback_protocol"] == "behavioral-v1"
    assert status["baseline_episodes"] == 1
    assert not (run / "identity.json").exists() and not (run / "arms").exists()
    assert fingerprint(initial_files("combined")) == fingerprint(
        {p.name: p.read_text() for p in (run / "reference/candidate").iterdir()}
    )


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
    for role, model in (("optimizer", "gpt-5.4-mini-2026-03-17"), ("victim", "gpt-6.1-sol")):
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
