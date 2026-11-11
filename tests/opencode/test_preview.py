"""Trusted preview checks: fresh browser state, exact inputs and zero paid calls."""

import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading

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
        preview(candidate, tmp_path / "bad-timeout", timeout_seconds=261)


def test_worker_failure_preserves_an_infrastructure_result(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    calls = []

    def fail(request, timeout, grace):
        calls.append(request)
        assert 0 < timeout <= 3
        return {
            "valid": None,
            "error_type": "infrastructure_error",
            "error": "worker timed out",
        }

    monkeypatch.setattr("webhelix.opencode.preview._launch", fail)
    result = preview(candidate, tmp_path / "failed", timeout_seconds=5)
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
        clock[0] += 67  # Includes bounded worker termination after its allowance.
        return {
            "outcome": "infrastructure_error",
            "error": f"Worker exceeded {timeout} seconds; no retry",
        }

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(candidate, output, timeout_seconds=120)
    assert allowances == [118, 65]
    assert result["status"] == "infrastructure_error"
    assert result["validation"]["valid"] is True
    assert result["failure_phase"] == "scripted_journey"
    phases = result["timing"]["phases"]
    assert [p["name"] for p in phases] == ["rendered_validation", "scripted_journey"]
    assert [p["latency_seconds"] for p in phases] == [53, 67]
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
    result = preview(candidate, tmp_path / "exhausted", timeout_seconds=120)
    assert len(calls) == 1
    assert result["status"] == "infrastructure_error"
    assert result["failure_phase"] == "scripted_journey"
    assert "deadline exhausted" in result["error"]
    phase = result["timing"]["phases"][-1]
    assert phase["worker_timeout_seconds"] == 0
    assert phase["status"] == "timed_out"
    assert "worker_outcome" not in phase


def test_separate_phase_allowances_complete_beyond_old_shared_limit(
    tmp_path, monkeypatch
):
    candidate = candidate_at(tmp_path)
    clock = [100.0]
    allowances = []
    monkeypatch.setattr("webhelix.opencode.preview.time.monotonic", lambda: clock[0])

    def launch(request, timeout, grace):
        allowances.append(timeout)
        assert grace == 2
        if request.get("kind") == "feasibility":
            clock[0] += 79
            return {"valid": True}
        clock[0] += 85
        return {"outcome": "legitimate_success"}

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(candidate, tmp_path / "separate-phases")
    assert result["status"] == "preview_complete"
    assert result["latency_seconds"] == 164
    assert allowances == [120, 120]
    assert result["timing"]["budget_seconds"] == 260
    assert result["timing"]["cleanup_reserved_inside_budget"] is True
    assert all(
        p["configured_timeout_seconds"] == 120 for p in result["timing"]["phases"]
    )
    assert result["model_calls"] == result["real_victim_episodes"] == 0


def test_custom_phase_caps_remain_inside_overall_cap(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    clock = [100.0]
    allowances = []
    monkeypatch.setattr("webhelix.opencode.preview.time.monotonic", lambda: clock[0])

    def launch(request, timeout, grace):
        allowances.append(timeout)
        if request.get("kind") == "feasibility":
            clock[0] += 19
            return {"valid": True}
        clock[0] += 25
        return {"outcome": "legitimate_success"}

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(
        candidate,
        tmp_path / "custom-phases",
        timeout_seconds=90,
        validation_timeout_seconds=20,
        journey_timeout_seconds=30,
    )
    assert result["status"] == "preview_complete"
    assert allowances == [20, 30]
    assert result["latency_seconds"] == 44


@pytest.mark.parametrize(
    "options",
    [
        {"validation_timeout_seconds": 121},
        {"journey_timeout_seconds": 121},
        {"validation_timeout_seconds": False},
        {"journey_timeout_seconds": 0},
    ],
)
def test_invalid_phase_limits_fail_before_work(tmp_path, monkeypatch, options):
    candidate = candidate_at(tmp_path)
    monkeypatch.setattr(
        "webhelix.opencode.preview._snapshot",
        lambda *a, **kw: pytest.fail("started work"),
    )
    with pytest.raises(ValueError, match="timeout"):
        preview(candidate, tmp_path / "invalid-phase", **options)


def test_no_worker_starts_without_its_cleanup_reserve(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    monkeypatch.setattr(
        "webhelix.opencode.preview._launch",
        lambda *a, **kw: pytest.fail("started worker"),
    )
    result = preview(candidate, tmp_path / "no-cleanup-time", timeout_seconds=1)
    assert result["status"] == "infrastructure_error"
    assert result["failure_phase"] == "rendered_validation"
    assert result["timing"]["phases"][0]["worker_timeout_seconds"] == 0


def test_snapshot_time_counts_against_the_overall_deadline(tmp_path, monkeypatch):
    from webhelix.opencode import preview as module

    candidate = candidate_at(tmp_path)
    clock = [100.0]
    original = module._snapshot
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def snapshot(*args, **kwargs):
        files = original(*args, **kwargs)
        clock[0] += 260
        return files

    monkeypatch.setattr(module, "_snapshot", snapshot)
    monkeypatch.setattr(
        module, "_launch", lambda *a, **kw: pytest.fail("started worker")
    )
    result = preview(candidate, tmp_path / "snapshot-timeout")
    assert result["status"] == "infrastructure_error"
    assert result["failure_phase"] == "snapshot"
    assert result["timing"]["phases"] == []


def test_expired_artifact_export_cannot_leave_a_success_manifest(tmp_path, monkeypatch):
    from webhelix.opencode import preview as module

    candidate = candidate_at(tmp_path)
    output = tmp_path / "export-timeout"
    clock = [100.0]
    original = module._artifact
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def launch(request, **kwargs):
        return (
            {"valid": True}
            if request.get("kind") == "feasibility"
            else {"outcome": "legitimate_success"}
        )

    def artifact(*args, **kwargs):
        value = original(*args, **kwargs)
        clock[0] += 260
        return value

    monkeypatch.setattr(module, "_launch", launch)
    monkeypatch.setattr(module, "_artifact", artifact)
    result = preview(candidate, output)
    assert result["status"] == "infrastructure_error"
    assert result["failure_phase"] == "artifact_collection"
    assert result["artifacts"] == []
    assert (
        json.loads((output / "manifest.json").read_text())["status"]
        == "infrastructure_error"
    )
    assert (
        json.loads((output / "preview.json").read_text())["status"]
        == "infrastructure_error"
    )


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


@pytest.mark.parametrize("manifest_write_fails", [False, True])
def test_receipt_write_failure_cannot_report_success(
    tmp_path, monkeypatch, manifest_write_fails
):
    candidate = candidate_at(tmp_path)
    output = tmp_path / "missing-final-receipt"
    original = OSError("final receipt disk full")

    def launch(request, **kwargs):
        return (
            {"valid": True}
            if request.get("kind") == "feasibility"
            else {"outcome": "legitimate_success"}
        )

    def fail_final_receipt(path, value):
        if path.name == "preview.json" and value["status"] == "preview_complete":
            raise original
        if (
            manifest_write_fails
            and path.name == "manifest.json"
            and value["status"] == "infrastructure_error"
        ):
            raise OSError("manifest repair disk full")
        save(path, value)

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    monkeypatch.setattr("webhelix.opencode.preview.save", fail_final_receipt)
    with pytest.raises(OSError, match="final receipt disk full") as caught:
        preview(candidate, output)
    assert caught.value is original
    manifest = output / "manifest.json"
    if manifest_write_fails:
        assert not manifest.exists()
    else:
        assert json.loads(manifest.read_text())["status"] == "infrastructure_error"
    assert (
        json.loads((output / "preview.json").read_text())["status"]
        == "infrastructure_error"
    )


def test_cancelled_session_never_starts_snapshot_or_worker(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    cancelled = threading.Event()
    cancelled.set()
    for function in ("_snapshot", "_launch"):
        monkeypatch.setattr(
            "webhelix.opencode.preview." + function,
            lambda *a, **kw: pytest.fail("Cancelled preview started work"),
        )
    result = preview(
        candidate, tmp_path / "cancelled-before-start", cancel_event=cancelled
    )
    assert result["status"] == "infrastructure_error"
    assert result["cancelled"] is True
    assert result["failure_phase"] == "snapshot"
    assert result["timing"]["phases"] == []


@pytest.mark.parametrize("cancelled_phase", ["rendered_validation", "scripted_journey"])
def test_worker_cancellation_is_preserved_and_stops_followup_work(
    tmp_path, monkeypatch, cancelled_phase
):
    candidate = candidate_at(tmp_path)
    cancelled = threading.Event()
    calls = []

    def launch(request, *, cancel_event, **kwargs):
        assert cancel_event is cancelled
        phase = (
            "rendered_validation"
            if request.get("kind") == "feasibility"
            else "scripted_journey"
        )
        calls.append(phase)
        if phase == cancelled_phase:
            cancelled.set()
            return {
                "valid": None,
                "outcome": "infrastructure_error",
                "cancelled": True,
                "error_type": "infrastructure_error",
                "error": "Original worker cancellation",
            }
        return {"valid": True}

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(candidate, tmp_path / cancelled_phase, cancel_event=cancelled)
    assert result["status"] == "infrastructure_error"
    assert result["cancelled"] is True
    assert result["failure_phase"] == cancelled_phase
    assert result["error"] == "Original worker cancellation"
    assert result["timing"]["phases"][-1]["status"] == "cancelled"
    assert calls[-1] == cancelled_phase
    assert result["artifacts"] == []
    assert result["model_calls"] == result["real_victim_episodes"] == 0


def test_cancellation_after_validation_does_not_start_journey(tmp_path, monkeypatch):
    candidate = candidate_at(tmp_path)
    cancelled = threading.Event()
    calls = []

    def launch(request, *, cancel_event, **kwargs):
        calls.append(request)
        cancel_event.set()
        return {"valid": True}

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(
        candidate, tmp_path / "cancelled-after-validation", cancel_event=cancelled
    )
    assert len(calls) == 1
    assert result["status"] == "infrastructure_error"
    assert result["timing"]["phases"][-1]["status"] == "cancelled"
    assert result["cancelled"] is True


def test_cancellation_during_export_invalidates_success_manifest(tmp_path, monkeypatch):
    from webhelix.opencode import preview as module

    candidate = candidate_at(tmp_path)
    output = tmp_path / "cancelled-export"
    cancelled = threading.Event()
    original = module._artifact

    def launch(request, **kwargs):
        return (
            {"valid": True}
            if request.get("kind") == "feasibility"
            else {"outcome": "legitimate_success"}
        )

    def artifact(path):
        value = original(path)
        cancelled.set()
        return value

    monkeypatch.setattr(module, "_launch", launch)
    monkeypatch.setattr(module, "_artifact", artifact)
    result = preview(candidate, output, cancel_event=cancelled)
    assert result["status"] == "infrastructure_error"
    assert result["cancelled"] is True
    assert result["artifacts"] == []
    assert result["failure_phase"] == "artifact_collection"
    assert (
        json.loads((output / "manifest.json").read_text())["status"]
        == "infrastructure_error"
    )


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
