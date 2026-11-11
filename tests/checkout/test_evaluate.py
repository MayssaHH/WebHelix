import shutil
import signal
import subprocess
import threading
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


@pytest.mark.parametrize("kind", ["feasibility", "episode"])
def test_cooperative_cancellation_stops_process_group_and_preserves_partial_result(
    tmp_path, monkeypatch, kind
):
    from webhelix.checkout.evaluate import _launch

    cancelled = threading.Event()
    signals, waits = [], []
    partial = (
        {"valid": True}
        if kind == "feasibility"
        else {"outcome": "legitimate_success"}
    )
    filename = "feasibility.json" if kind == "feasibility" else "episode.json"
    save(tmp_path / filename, partial)

    class Worker:
        pid = 12345
        args = ["offline-worker"]
        returncode = None

        def wait(self, timeout):
            waits.append(timeout)
            if self.returncode is not None:
                return self.returncode
            cancelled.set()
            raise subprocess.TimeoutExpired(self.args, timeout)

        def poll(self):
            return self.returncode

    process = Worker()

    def start(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        return process

    def kill_group(pid, signum):
        signals.append((pid, signum))
        process.returncode = -signum

    monkeypatch.setattr("webhelix.checkout.evaluate.subprocess.Popen", start)
    monkeypatch.setattr("webhelix.checkout.evaluate.os.killpg", kill_group)
    result = _launch(
        {"output": str(tmp_path), "kind": kind},
        timeout=120,
        grace=2,
        cancel_event=cancelled,
    )
    assert signals == [(process.pid, signal.SIGTERM)]
    assert waits == [0.1, 2]
    assert result["cancelled"] is True
    assert "cancelled" in result["error"]
    if kind == "feasibility":
        assert result["valid"] is None
        assert result["error_type"] == "infrastructure_error"
    else:
        assert result["outcome"] == "infrastructure_error"
        assert result["legitimate_success"] is False
    assert read(tmp_path / filename) == result
    assert read(tmp_path / "worker-partial-receipt.json") == partial


def test_pre_cancelled_worker_does_not_launch(tmp_path, monkeypatch):
    from webhelix.checkout.evaluate import _launch

    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(
        "webhelix.checkout.evaluate.subprocess.Popen",
        lambda *a, **kw: pytest.fail("Cancelled request started a worker"),
    )
    result = _launch(
        {"output": str(tmp_path), "kind": "feasibility"},
        timeout=120,
        cancel_event=cancelled,
    )
    assert result["cancelled"] and result["valid"] is None
    assert "before launch" in result["error"]
    assert read(tmp_path / "feasibility.json") == result


def test_event_enabled_worker_keeps_timeout_and_process_cleanup(tmp_path, monkeypatch):
    from webhelix.checkout.evaluate import _launch

    now = [100.0]
    signals = []

    class Worker:
        pid = 12345
        args = ["offline-worker"]
        returncode = None

        def wait(self, timeout):
            if self.returncode is not None:
                return self.returncode
            now[0] += timeout
            raise subprocess.TimeoutExpired(self.args, timeout)

        def poll(self):
            return self.returncode

    process = Worker()

    def kill_group(pid, signum):
        signals.append((pid, signum))
        process.returncode = -signum

    monkeypatch.setattr("webhelix.checkout.evaluate.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "webhelix.checkout.evaluate.subprocess.Popen", lambda *a, **kw: process
    )
    monkeypatch.setattr("webhelix.checkout.evaluate.os.killpg", kill_group)
    result = _launch(
        {"output": str(tmp_path), "kind": "feasibility"},
        timeout=0.25,
        cancel_event=threading.Event(),
    )
    assert result["valid"] is None and "exceeded 0.25 seconds" in result["error"]
    assert "cancelled" not in result
    assert result["latency_seconds"] == 0.25
    assert signals == [(process.pid, signal.SIGTERM)]
