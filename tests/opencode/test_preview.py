"""Trusted preview checks: fresh browser state, exact inputs and zero paid calls."""

import base64
import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest

from webhelix.checkout.contract import FIXTURES, fingerprint, load, save
from webhelix.opencode.preview import preview


def candidate_at(tmp_path):
    candidate = tmp_path / "candidate"
    shutil.copytree(FIXTURES / "candidate", candidate)
    return candidate


def test_invalid_and_symlink_candidates_are_rejected_before_browser(
    tmp_path, monkeypatch
):
    candidate = candidate_at(tmp_path)
    monkeypatch.setattr(
        "webhelix.opencode.preview._launch",
        lambda *args, **kwargs: pytest.fail("Invalid candidate launched a browser"),
    )
    (candidate / "product.html").write_text("<script>unsafe()</script>")
    result = preview(candidate, tmp_path / "invalid")
    assert result["status"] == "invalid_candidate"
    assert result["validation"]["valid"] is False
    assert result["artifacts"] == []
    private = tmp_path / "credential.txt"
    private.write_text("test-private-value-never-export")
    (candidate / "product.html").unlink()
    (candidate / "product.html").symlink_to(private)
    result = preview(candidate, tmp_path / "symlink")
    assert result["status"] == "invalid_candidate"
    assert "test-private-value" not in json.dumps(result)
    assert not (tmp_path / "symlink/candidate/product.html").exists()


def test_output_never_overwrites_candidate_or_previous_preview(tmp_path):
    candidate = candidate_at(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        preview(candidate, candidate / "preview")
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        preview(candidate, destination)
    with pytest.raises(ValueError, match="timeout"):
        preview(candidate, tmp_path / "bad-timeout", timeout_seconds=121)


def test_worker_failure_preserves_an_infrastructure_result(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    calls = []

    def fail(request, timeout, grace):
        calls.append(request)
        assert 0 < timeout <= 1
        return {
            "valid": None,
            "error_type": "infrastructure_error",
            "error": "worker timed out",
        }

    monkeypatch.setattr("webhelix.opencode.preview._launch", fail)
    result = preview(candidate, tmp_path / "failed", timeout_seconds=1)
    assert result["status"] == "infrastructure_error"
    assert len(calls) == 1
    assert result["artifacts"] == []
    assert result["model_calls"] == result["real_victim_episodes"] == 0
    assert (tmp_path / "failed/preview.json").exists()
    assert result["failure_phase"] == "rendered_validation"
    assert result["timing"]["phases"][0]["worker_outcome"] == "infrastructure_error"


def test_phase_receipts_explain_shared_deadline_without_retry(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    output = tmp_path / "timed-preview"
    clock = [100.0]
    allowances = []
    monkeypatch.setattr("webhelix.opencode.preview.time.monotonic", lambda: clock[0])

    def launch(request, timeout, grace):
        allowances.append(timeout)
        assert grace == 2
        saved = json.loads((output / "preview.json").read_text())
        assert saved["timing"]["phases"][-1]["status"] == "running"
        assert saved["timing"]["phases"][-1]["worker_timeout_seconds"] == timeout
        if request.get("kind") == "feasibility":
            clock[0] += 53
            return {"valid": True, "latency_seconds": 51}
        clock[0] += 68  # Includes bounded worker termination after its allowance.
        return {
            "outcome": "infrastructure_error",
            "error": f"Worker exceeded {timeout} seconds; no retry",
        }

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(candidate, output)
    assert allowances == [120, 67]
    assert result["status"] == "infrastructure_error"
    assert result["validation"]["valid"] is True
    assert result["failure_phase"] == "scripted_journey"
    phases = result["timing"]["phases"]
    assert [p["name"] for p in phases] == ["rendered_validation", "scripted_journey"]
    assert [p["latency_seconds"] for p in phases] == [53, 68]
    assert phases[0]["status"] == "completed"
    assert phases[1]["status"] == "timed_out"
    assert phases[1]["remaining_after_seconds"] == 0
    assert result["model_calls"] == result["real_victim_episodes"] == 0
    assert (
        json.loads((output / "preview.json").read_text())["timing"] == result["timing"]
    )


def test_exhausted_deadline_records_unstarted_journey(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    clock = [100.0]
    calls = []
    monkeypatch.setattr("webhelix.opencode.preview.time.monotonic", lambda: clock[0])

    def launch(request, timeout, grace):
        calls.append(request)
        clock[0] += timeout
        return {"valid": True}

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(candidate, tmp_path / "exhausted")
    assert len(calls) == 1
    assert result["status"] == "infrastructure_error"
    assert result["failure_phase"] == "scripted_journey"
    assert "deadline exhausted" in result["error"]
    phase = result["timing"]["phases"][-1]
    assert phase["worker_timeout_seconds"] == 0
    assert phase["status"] == "timed_out"
    assert "worker_outcome" not in phase


def test_launch_exception_preserves_phase_and_zero_paid_calls(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)

    def launch(*args, **kwargs):
        raise OSError("offline worker launch failed")

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(candidate, tmp_path / "launch-error")
    assert result["status"] == "infrastructure_error"
    assert result["failure_phase"] == "rendered_validation"
    phase = result["timing"]["phases"][0]
    assert phase["status"] == "failed" and phase["error_type"] == "OSError"
    assert phase["latency_seconds"] >= 0
    assert result["model_calls"] == result["real_victim_episodes"] == 0


@pytest.mark.parametrize("worker_raises", [True, False])
def test_diagnostic_write_failure_preserves_original_failure(
    tmp_path, monkeypatch, worker_raises
):
    candidate = candidate_at(tmp_path)

    def launch(*args, **kwargs):
        if worker_raises:
            raise TimeoutError("original worker timeout")
        return {"valid": False, "error": "original protected-fact failure"}

    def fail_after_launch(path, value):
        phases = value.get("timing", {}).get("phases", [])
        if path.name == "preview.json" and any(
            p["status"] != "running" for p in phases
        ):
            raise OSError("diagnostic disk full")
        save(path, value)

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    monkeypatch.setattr("webhelix.opencode.preview.save", fail_after_launch)
    result = preview(candidate, tmp_path / "write-failure")
    if worker_raises:
        assert result["status"] == "infrastructure_error"
        assert result["error"] == "TimeoutError: original worker timeout"
    else:
        assert result["status"] == "invalid_candidate"
        assert result["validation"]["error"] == "original protected-fact failure"
    assert result["failure_phase"] == "rendered_validation"
    assert result["diagnostic_errors"]
    assert all("disk full" in error["error"] for error in result["diagnostic_errors"])


def test_diagnostic_write_failure_does_not_replace_cancellation(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    original = KeyboardInterrupt("stop preview")

    def launch(*args, **kwargs):
        raise original

    def fail_after_launch(path, value):
        if any(
            p.get("error_type") == "KeyboardInterrupt"
            for p in value["timing"]["phases"]
        ):
            raise OSError("diagnostic disk full")
        save(path, value)

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    monkeypatch.setattr("webhelix.opencode.preview.save", fail_after_launch)
    with pytest.raises(KeyboardInterrupt) as caught:
        preview(candidate, tmp_path / "cancelled")
    assert caught.value is original


def test_receipt_write_failure_cannot_report_success(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)

    def launch(request, **kwargs):
        return (
            {"valid": True}
            if request.get("kind") == "feasibility"
            else {"outcome": "legitimate_success"}
        )

    def fail_final_receipt(path, value):
        if path.name == "preview.json" and value["status"] == "preview_complete":
            raise OSError("final receipt disk full")
        save(path, value)

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    monkeypatch.setattr("webhelix.opencode.preview.save", fail_final_receipt)
    with pytest.raises(OSError, match="final receipt disk full"):
        preview(candidate, tmp_path / "missing-final-receipt")


def test_successful_phase_write_failure_stops_before_next_worker(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    calls = []

    def launch(request, **kwargs):
        calls.append(request)
        return {"valid": True}

    def fail_completed_phase(path, value):
        if any(
            p["status"] == "completed"
            for p in value.get("timing", {}).get("phases", [])
        ):
            raise OSError("phase receipt disk full")
        save(path, value)

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    monkeypatch.setattr("webhelix.opencode.preview.save", fail_completed_phase)
    result = preview(candidate, tmp_path / "phase-write-failure")
    assert len(calls) == 1
    assert result["status"] == "infrastructure_error"
    assert result["error"] == "OSError: phase receipt disk full"
    assert result["diagnostic_errors"]


def test_actual_preview_tracks_active_edits_and_uses_fresh_scripted_state(tmp_path):
    candidate = candidate_at(tmp_path)
    first = preview(candidate, tmp_path / "first")
    assert first["status"] == "preview_complete", first
    assert first["candidate_hash"] == fingerprint(load(candidate))
    assert [stage["name"] for stage in first["stages"]] == [
        "product",
        "cart",
        "options",
        "review",
        "confirmation",
    ]
    (candidate / "style.css").write_text(
        (candidate / "style.css").read_text() + "\nbody { background: #e5eef8; }\n"
    )
    second = preview(candidate, tmp_path / "second")
    assert second["status"] == "preview_complete", second
    assert second["candidate_hash"] == fingerprint(load(candidate))
    assert second["candidate_hash"] != first["candidate_hash"]
    assert (tmp_path / "first/candidate/style.css").read_text() != (
        candidate / "style.css"
    ).read_text()
    first_artifacts = {item["path"]: item for item in first["artifacts"]}
    second_artifacts = {item["path"]: item for item in second["artifacts"]}
    assert (
        first_artifacts["screenshot-0.png"]["sha256"]
        != second_artifacts["screenshot-0.png"]["sha256"]
    )
    for name, result in (("first", first), ("second", second)):
        assert result["model_calls"] == result["real_victim_episodes"] == 0
        assert not (tmp_path / name / "budget.json").exists()
        receipt = json.loads(
            (tmp_path / name / "scripted-journey/episode.json").read_text()
        )
        assert receipt["policy"] == "scripted-correct" and receipt["usage"] == []
        assert receipt["initial_storage"] == {"cookies": [], "local": 0, "session": 0}
        assert receipt["backend_before"]["order"] is None
        for artifact in result["artifacts"]:
            assert Path(artifact["path"]).name == artifact["path"]
            data = (
                base64.b64decode(artifact["content"])
                if artifact["encoding"] == "base64"
                else artifact["content"].encode()
            )
            assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
        observation = json.loads(
            next(a for a in result["artifacts"] if a["path"] == "observation-0.json")[
                "content"
            ]
        )
        assert observation["agent_harness"] == "browser-use"
        assert re.search(r"\[\d+\]<", observation["dom"])
        assert "Atlas" in observation["dom"]
        assert "metrics" not in result and "steering_rate" not in json.dumps(result)
