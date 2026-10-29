"""Synthetic source fixtures based on published evidence; no real API requests."""

import copy
import hashlib
import shutil
import subprocess

import httpx
import pytest

from webhelix.optimizer.backend import ProposalBackend
from webhelix.optimizer.client import ProposalClient, Settings
from webhelix.optimizer.continuation import (
    prepare_continuation,
    verify_source,
    victim_input_hashes,
    normalized,
)
from webhelix.browser.manifest import runtime_identity
from webhelix.v2.contract import ROOT, read, save


@pytest.fixture
def source_run(tmp_path, monkeypatch):
    evidence = ROOT / "examples/optimizer-adapter/real-research-test-20261019"
    source, budget = tmp_path / "synthetic-source", tmp_path / "shared"
    source.mkdir()
    status = read(evidence / "status.json")
    status.update(
        source_revision=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        victim_budget_dir=str(budget),
        browser_runtime=runtime_identity(),
        victim_input_hashes=victim_input_hashes(),
        dependencies={"browser-use": runtime_identity()["browser_use_version"]},
    )
    save(source / "status.json", status)
    monkeypatch.setattr(
        "webhelix.optimizer.continuation.configured_victim_settings",
        lambda: status["model"],
    )
    adapter = source / "proposals/iter-1/adapter"
    for name in ("proposal", "result", "transport-result"):
        save(adapter / f"{name}.json", read(evidence / f"optimizer-{name}.json"))
    save(adapter.parent / "agent-result.json", read(evidence / "agent-result.json"))
    save(
        source / "optimizer-budget.json",
        {"maximum": 2, "calls": [{"output": str(adapter)}]},
    )
    ledger = {"maximum": 30, "reservations": []}
    for label in ("clean", "identity"):
        report = copy.deepcopy(read(evidence / f"{label}-report.json"))
        batch = source / "batches" / label
        report["batch"] = str(batch)
        report["reservations"] = []
        shutil.copytree(ROOT / "experiments/reservation/candidate", batch / "candidate")
        for i, episode in enumerate(report["episodes"]):
            ep = batch / f"episode-{i}"
            episode["browser_runtime"] = runtime_identity()
            episode["model"] = status["model"]
            save(ep / "episode.json", episode)
            for name in ("screenshot-0.png", "observation-0.json"):
                shutil.copy2(evidence / "clean-episode-0" / name, ep / name)
            number = len(ledger["reservations"]) + 1
            ledger["reservations"].append({"number": number, "label": f"{label}/{i}"})
            report["reservations"].append(number)
        save(batch / "report.json", report)
        save(source / f"{label}.json", report)
    save(budget / "budget.json", ledger)

    # This unit fixture tests immutable reuse/accounting, not browser rendering.
    # Native Browser-Use rendering is covered by the real-browser task controls.
    def inspect(proposal, output):
        output.mkdir(parents=True)
        observation = read(source / "batches/clean/episode-0/observation-0.json")
        save(output / "clean-observation.json", normalized(observation))
        shutil.copy2(
            source / "batches/clean/episode-0/screenshot-0.png", output / "clean.png"
        )
        return {"model_calls": 0, "real_victim_episodes": 0, "synthetic_test": True}

    monkeypatch.setattr("webhelix.optimizer.continuation.inspect_proposal", inspect)
    return source, budget, Settings(**status["optimizer_settings"])


@pytest.mark.parametrize(
    "change",
    ["model", "episode", "ledger", "candidate", "ground_truth", "historical_harness"],
)
def test_incompatible_baselines_fail_before_spending(source_run, monkeypatch, change):
    source, budget, settings = source_run
    if change == "model":
        monkeypatch.setattr(
            "webhelix.optimizer.continuation.configured_victim_settings",
            lambda: {"model": "different"},
        )
    elif change == "episode":
        p = source / "batches/clean/episode-0/episode.json"
        episode = read(p)
        episode["backend"]["confirmed"] = False
        save(p, episode)
    elif change == "ledger":
        save(budget / "budget.json", {"maximum": 30, "reservations": []})
    elif change == "candidate":
        (source / "batches/clean/candidate/style.css").write_text("body{color:red}")
    elif change == "historical_harness":
        status = read(source / "status.json")
        status.pop("browser_runtime")
        save(source / "status.json", status)
    elif change == "ground_truth":
        monkeypatch.setattr(
            "webhelix.optimizer.continuation.task", lambda: {"different": True}
        )
    with pytest.raises((ValueError, IndexError)):
        verify_source(source, settings, budget)
    assert not (budget / "continuations").exists()


def test_reuse_is_audited_single_use_and_does_not_modify_source(source_run, tmp_path):
    source, budget, settings = source_run

    def hashes():
        return {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*")
            if p.is_file()
        }

    original = hashes()
    run = tmp_path / "continuation"
    backend = ProposalBackend(
        ProposalClient(
            settings,
            run,
            call_limit=1,
            transport=httpx.MockTransport(
                lambda req: pytest.fail(
                    "Offline continuation preparation made a model call"
                )
            ),
        ),
        run,
        replay=source / "proposals/iter-1/adapter/proposal.json",
    )
    reports = prepare_continuation(source, run, backend, budget)
    assert all(r["new_real_episodes"] == 0 for r in reports.values())
    assert read(run / "continuation.json")["maximum_new_optimizer_calls"] == 1
    assert len(read(budget / "budget.json")["reservations"]) == 6
    assert hashes() == original
    with pytest.raises(ValueError, match="already has a continuation"):
        prepare_continuation(source, tmp_path / "duplicate", backend, budget)
    assert hashes() == original


def test_report_includes_failed_optimizer_usage_without_history(tmp_path):
    from webhelix.v2.report import export

    run, output = tmp_path / "run", tmp_path / "export"
    save(
        run / "status.json",
        {
            "status": "blocked_or_failed",
            "source_revision": "fixture",
            "reserved_episodes": 0,
            "completed_iterations": 0,
        },
    )
    save(run / "proposals/iter-1/agent-result.json", {"success": False, "usage": {}})
    save(
        run / "proposals/iter-1/adapter/transport-result.json",
        {
            "request_started": True,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    )
    export(run, output)
    usage = read(output / "usage.json")
    assert usage["optimizer"] == {"input_tokens": 100, "output_tokens": 20}
    assert usage["new_optimizer_requests"] == 1
