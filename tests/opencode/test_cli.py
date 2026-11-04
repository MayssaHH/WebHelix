"""Backend selection and run bounds are verified before any paid execution."""

import pytest

from webhelix.checkout.cli import initialize, main
from webhelix.checkout.contract import FIXTURES, read, save
from webhelix.search.budget import BudgetBlocked


def test_opencode_demo_selects_real_cli_with_explicit_offline_transport_plan(tmp_path):
    run = tmp_path / "offline"
    settings = initialize(run, optimizer_backend="opencode")
    assert settings["optimizer_backend"] == "opencode"
    assert settings["iterations"] == 5
    assert settings["max_optimizer_calls"] == 50
    assert settings["optimizer_settings"]["max_requests_per_session"] == 10
    assert settings["policy"] == "scripted-correct"
    assert settings["max_real_episodes"] == 0
    assert settings["env_file"] is None
    plan = read(run / "spending-plan.json")
    assert plan["offline_fixture"] is True
    assert read(run / "optimizer-settings.json")["model"] == "gpt-6.1-sol"
    assert not (run / "budget.json").exists()


def test_opencode_real_requires_new_authorized_plan_before_creating_run(tmp_path):
    env = tmp_path / "unit-test.env"
    env.write_text("WEBHELIX_API_KEY=offline-test-only\n")
    run = tmp_path / "not-created"
    with pytest.raises(BudgetBlocked):
        initialize(
            run,
            optimizer_backend="opencode",
            scripted=False,
            optimizer_config=FIXTURES / "opencode.example.json",
            spending_plan=FIXTURES / "spending-plan.example.json",
            env_file=env,
        )
    assert not run.exists()


def test_longer_pilot_allows_ten_requests_for_check_and_each_research_session(tmp_path):
    original = read(FIXTURES / "opencode-pilot.json")
    longer = read(FIXTURES / "opencode-longer-pilot.json")
    assert original["max_optimizer_calls"] == 50
    assert longer == {**original, "max_optimizer_calls": 60}
    settings = initialize(
        tmp_path / "longer-offline",
        optimizer_backend="opencode",
        pilot_config=FIXTURES / "opencode-longer-pilot.json",
    )
    assert settings["max_optimizer_calls"] == (
        settings["iterations"] + 1
    ) * settings["optimizer_settings"]["max_requests_per_session"]


def test_proposed_dollar_ceiling_does_not_authorize_longer_real_run(tmp_path):
    plan = read(FIXTURES / "spending-plan.example.json")
    plan["proposed_authorized_usd"] = 30
    source = tmp_path / "proposed-plan.json"
    save(source, plan)
    env = tmp_path / "unit-test.env"
    env.write_text("WEBHELIX_API_KEY=offline-test-only\n")
    run = tmp_path / "not-created"
    with pytest.raises(BudgetBlocked, match="positive authorized amount"):
        initialize(
            run,
            scripted=False,
            optimizer_backend="opencode",
            pilot_config=FIXTURES / "opencode-longer-pilot.json",
            optimizer_config=FIXTURES / "opencode.example.json",
            spending_plan=source,
            env_file=env,
        )
    assert not run.exists()


def test_offline_plan_cannot_be_reused_to_authorize_real_requests(tmp_path):
    demo = tmp_path / "demo"
    initialize(demo, optimizer_backend="opencode")
    env = tmp_path / "unit-test.env"
    env.write_text("WEBHELIX_API_KEY=offline-test-only\n")
    run = tmp_path / "real"
    with pytest.raises(ValueError, match="never authorizes"):
        initialize(
            run,
            optimizer_backend="opencode",
            scripted=False,
            optimizer_config=FIXTURES / "opencode.example.json",
            spending_plan=demo / "spending-plan.json",
            env_file=env,
        )
    assert not run.exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("iterations", 0, "iterations"),
        ("iterations", 11, "iterations"),
        ("iterations", True, "iterations"),
        ("condition", "misleading", "truthful"),
        ("max_real_episodes", 20, "clean, identity"),
        ("max_actions", 41, "max_actions"),
        ("episode_timeout_seconds", 481, "episode_timeout_seconds"),
        ("max_optimizer_calls", 61, "session request cap"),
        ("viewport", {"width": 640, "height": 480}, "viewport"),
    ],
)
def test_changed_pilot_must_preserve_condition_and_bound_resources(
    tmp_path, field, value, message
):
    pilot = read(FIXTURES / "opencode-pilot.json")
    pilot[field] = value
    source = tmp_path / "pilot.json"
    save(source, pilot)
    run = tmp_path / "run"
    with pytest.raises(ValueError, match=message):
        initialize(run, optimizer_backend="opencode", pilot_config=source)
    assert not run.exists()


def test_pilot_cannot_inject_a_different_scoring_function(tmp_path):
    pilot = read(FIXTURES / "opencode-pilot.json")
    pilot["scoring"] = "always_accept"
    source = tmp_path / "pilot.json"
    save(source, pilot)
    with pytest.raises(ValueError, match="documented run settings"):
        initialize(tmp_path / "run", optimizer_backend="opencode", pilot_config=source)


def test_real_mode_requires_three_episodes_even_with_custom_pilot(tmp_path):
    pilot = read(FIXTURES / "opencode-pilot.json")
    pilot["episodes_per_condition"] = 1
    source = tmp_path / "pilot.json"
    save(source, pilot)
    with pytest.raises(ValueError, match="three fresh episodes"):
        initialize(
            tmp_path / "run",
            scripted=False,
            optimizer_backend="opencode",
            pilot_config=source,
        )


def test_demo_cli_routes_explicit_backend_without_any_model_execution(
    tmp_path, monkeypatch
):
    import webhelix.checkout.experiment
    import webhelix.checkout.report

    captured = []
    monkeypatch.setattr(
        webhelix.checkout.experiment,
        "experiment",
        lambda run, **kwargs: captured.append((read(run / "runtime.json"), kwargs)),
    )
    monkeypatch.setattr(
        webhelix.checkout.report, "build_report", lambda run: run / "index.html"
    )
    main(
        [
            "demo",
            "--optimizer-backend",
            "opencode",
            "--run-dir",
            str(tmp_path / "demo"),
        ]
    )
    assert len(captured) == 1
    assert captured[0][0]["optimizer_backend"] == "opencode"
    assert captured[0][1]["scripted"] is True


def test_default_backend_remains_existing_two_iteration_api_adapter(tmp_path):
    settings = initialize(tmp_path / "old-demo")
    assert settings["optimizer_backend"] == "proposal-api"
    assert settings["iterations"] == 2
    assert settings["max_optimizer_calls"] == 2
    assert "optimizer_settings" not in settings
