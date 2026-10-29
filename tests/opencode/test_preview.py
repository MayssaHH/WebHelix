"""Trusted preview checks: fresh browser state, exact inputs and zero paid calls."""

import base64
import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest

from webhelix.checkout.contract import FIXTURES, fingerprint, load
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
