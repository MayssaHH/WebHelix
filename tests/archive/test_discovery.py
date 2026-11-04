import json

import pytest

from webhelix.archive.cli import main
from webhelix.checkout.discovery import prepare_discovery, execute_discovery
from webhelix.checkout.contract import read


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
    assert settings["max_real_episodes"] == 0
    assert status["maximum_scheduled_episodes"] == 7
    assert status["maximum_optimizer_sessions"] == 2
    assert status["protected_environment_hashes"]["assets/core.css"]
    assert not (run / "budget.json").exists()
    initial = (run / "archive/assets/core.css").read_text()
    (archive_root / "assets/core.css").write_text("changed original outside frozen run")
    assert (run / "archive/assets/core.css").read_text() == initial


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
