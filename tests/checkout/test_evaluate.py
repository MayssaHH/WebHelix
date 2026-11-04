import shutil
import time

import pytest

from webhelix.checkout.contract import FIXTURES, fingerprint, load, read, save
from webhelix.checkout.evaluate import (
    bridge,
    cache_path,
    evaluate,
    evaluation_signature,
)


def runtime(run):
    settings = {
        "condition": "truthful",
        "policy": "scripted-correct",
        "episodes_per_condition": 3,
        "episode_timeout_seconds": 30,
    }
    save(run / "runtime.json", settings)
    return settings


def test_invalid_candidate_is_archived_without_starting_browser_or_model(
    tmp_path, monkeypatch
):
    run, candidate = tmp_path / "run", tmp_path / "candidate"
    runtime(run)
    shutil.copytree(FIXTURES / "candidate", candidate)
    (candidate / "product.html").write_text("<script>invalid()</script>")
    monkeypatch.setattr(
        "webhelix.checkout.evaluate._launch",
        lambda *args: pytest.fail("Invalid candidate must never start a worker"),
    )
    result = evaluate(candidate, run, episodes=3)
    assert result["episodes"] == [] and result["metrics"]["steering_rate"] is None
    assert result["metrics"]["invalid_candidates"] == 1
    assert (
        type(candidate)(result["batch"]) / "candidate/product.html"
    ).read_text() == "<script>invalid()</script>"


def test_metrics_never_evaluate_on_missing_cache(tmp_path, monkeypatch):
    run, workspace = tmp_path / "run", tmp_path / "project"
    runtime(run)
    workspace.mkdir()
    monkeypatch.setenv("WEBHELIX_CHECKOUT_RUN", str(run))
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "webhelix.checkout.evaluate.evaluate",
        lambda *args: pytest.fail("Metrics must never evaluate"),
    )
    with pytest.raises(RuntimeError, match="cannot launch"):
        bridge("metrics")


def test_cached_results_reject_changed_candidate_or_configuration(
    tmp_path, monkeypatch
):
    run, workspace = tmp_path / "run", tmp_path / "project"
    settings = runtime(run)
    shutil.copytree(FIXTURES / "candidate", workspace / "candidate")
    sha = fingerprint(load(workspace / "candidate"))
    save(
        cache_path(run, workspace),
        {
            "candidate_hash": sha,
            "evaluation_signature": evaluation_signature(
                sha, settings, settings["policy"], 3
            ),
        },
    )
    monkeypatch.setenv("WEBHELIX_CHECKOUT_RUN", str(run))
    monkeypatch.chdir(workspace)
    settings["victim"] = {"model": "a-different-model"}
    save(run / "runtime.json", settings)
    with pytest.raises(RuntimeError, match="changed after"):
        bridge("metrics")


def test_cache_signature_includes_browser_agent_version(monkeypatch):
    monkeypatch.setattr(
        "webhelix.checkout.evaluate.runtime_identity",
        lambda: {"browser_use_version": "0.13.14"},
    )
    first = evaluation_signature("candidate-hash", {}, "api", 3)
    monkeypatch.setattr(
        "webhelix.checkout.evaluate.runtime_identity",
        lambda: {"browser_use_version": "0.13.15"},
    )
    assert evaluation_signature("candidate-hash", {}, "api", 3) != first


def test_cache_signature_includes_measurement_protocol(monkeypatch):
    first = evaluation_signature("candidate-hash", {}, "api", 3)
    monkeypatch.setattr("webhelix.checkout.evaluate.PROTOCOL", "future-protocol")
    assert evaluation_signature("candidate-hash", {}, "api", 3) != first


def test_factual_diff_is_saved_before_failed_evaluation(tmp_path, monkeypatch):
    runtime(tmp_path)
    candidate = tmp_path / "candidate"
    shutil.copytree(FIXTURES / "candidate", candidate)
    css = candidate / "style.css"
    css.write_text(css.read_text() + "\n.unused { color: red; }\n")
    monkeypatch.setattr(
        "webhelix.checkout.evaluate._launch",
        lambda *args: {"valid": None, "error_type": "infrastructure_error"},
    )
    result = evaluate(candidate, tmp_path, episodes=3)
    facts = result["intervention"]
    batch = type(tmp_path)(result["batch"])
    assert facts["candidate_changed"] is True
    assert facts["changed_files"] == ["style.css"]
    assert facts["clean_hash"] != facts["candidate_hash"]
    assert read(batch / "intervention.json") == facts
    assert ".unused" in (batch / "candidate.diff").read_text()
    assert (batch / "intervention.md").is_file()
    assert result["metrics"]["steering_rate"] is None
    assert result["metrics"]["infrastructure_errors"] == 1


def test_preflight_infrastructure_failure_is_not_invalid_candidate(
    tmp_path, monkeypatch
):
    runtime(tmp_path)
    monkeypatch.setattr(
        "webhelix.checkout.evaluate._launch",
        lambda *args: {
            "valid": None,
            "error_type": "infrastructure_error",
            "error": "Browser launch failed",
        },
    )
    result = evaluate(FIXTURES / "candidate", tmp_path, episodes=3)
    assert result["episodes"] == []
    assert result["metrics"]["infrastructure_errors"] == 1
    assert result["metrics"]["invalid_candidates"] == 0


def test_expired_wall_budget_does_not_start_worker_or_invent_episodes(
    tmp_path, monkeypatch
):
    settings = runtime(tmp_path)
    settings["run_deadline_epoch"] = time.time() - 10
    save(tmp_path / "runtime.json", settings)
    monkeypatch.setattr(
        "webhelix.checkout.evaluate._launch",
        lambda *args: pytest.fail("Expired run must not launch"),
    )
    result = evaluate(FIXTURES / "candidate", tmp_path, episodes=3)
    assert result["metrics"]["budget_blocked"] and result["episodes"] == []
    assert result["metrics"]["steering_rate"] is None
    assert read(type(tmp_path)(result["batch"]) / "report.json")["budget_error"]


def test_worker_budget_stop_preserves_attempt_and_prevents_followup_episodes(
    tmp_path, monkeypatch
):
    runtime(tmp_path)
    requests = []

    def launched(request, *args):
        requests.append(request)
        if request.get("kind") == "feasibility":
            return {"valid": True}
        return {
            "outcome": "budget_blocked",
            "error": "Request reservation exceeded authorized amount",
        }

    monkeypatch.setattr("webhelix.checkout.evaluate._launch", launched)
    result = evaluate(FIXTURES / "candidate", tmp_path, episodes=3)
    assert len(requests) == 2  # One feasibility check and one attempted episode.
    assert result["budget_error"] == "Request reservation exceeded authorized amount"
    assert result["metrics"]["budget_blocked"]
    assert result["metrics"]["episodes"] == 1 and result["unstarted_episodes"] == 2
    assert result["metrics"]["infrastructure_errors"] == 0
    assert not result["metrics"]["evaluation_ok"]


def test_parent_cancellation_stops_worker_and_preserves_receipt(tmp_path, monkeypatch):
    from webhelix.checkout.evaluate import _launch

    class InterruptedWorker:
        pid = 12345
        returncode = None

        def wait(self, timeout):
            raise KeyboardInterrupt

        def poll(self):
            return None

    process = InterruptedWorker()
    terminated = []
    monkeypatch.setattr(
        "webhelix.checkout.evaluate.subprocess.Popen", lambda *a, **k: process
    )
    monkeypatch.setattr(
        "webhelix.checkout.evaluate._terminate", lambda p, grace: terminated.append(p)
    )
    with pytest.raises(KeyboardInterrupt):
        _launch({"output": str(tmp_path)}, 10)
    assert terminated == [process]
    assert read(tmp_path / "cancellation.json")["outcome"] == "cancelled"
