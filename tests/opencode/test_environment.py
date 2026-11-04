"""Alternate environment support preserves preview isolation and parent lineage."""

import json
from pathlib import Path
import subprocess

import pytest

from webhelix.checkout.contract import InvalidCandidate, fingerprint, read, save
from webhelix.opencode.fixture import ToolFixture
from webhelix.opencode.parent import ParentSelector
from webhelix.opencode.preview import _snapshot, preview


class AlternateEnvironment:
    max_file_bytes = 32_000

    @staticmethod
    def candidate_files(condition):
        assert condition == "archive"
        return (
            "listing.html",
            "product.html",
            "cart.html",
            "style.css",
            "interaction.json",
        )

    @staticmethod
    def fingerprint(files):
        return fingerprint(files)

    def validate(self, files, condition):
        assert set(files) == set(self.candidate_files(condition))
        if any("<script" in text for text in files.values()):
            raise InvalidCandidate("Executable candidate")


def candidate_at(path, files=None):
    files = files or {
        "listing.html": "<main>{{card:rose}}</main>",
        "product.html": "<main>{{product_name}}{{add}}</main>",
        "cart.html": "<main>{{cart_items}}{{cart_update}}</main>",
        "style.css": "main {color: #123;}",
        "interaction.json": '{"version":1,"rules":[]}',
    }
    path.mkdir(parents=True)
    for name, value in files.items():
        (path / name).write_text(value)
    return files


def test_snapshot_uses_environment_schema_bounds_and_nofollow(tmp_path):
    env = AlternateEnvironment()
    path = tmp_path / "candidate"
    files = candidate_at(path)
    files["listing.html"] = " " * 25_000
    (path / "listing.html").write_text(files["listing.html"])
    assert _snapshot(path, "archive", environment=env) == files
    (path / "listing.html").write_text(" " * 32_001)
    with pytest.raises(InvalidCandidate, match="bounded"):
        _snapshot(path, "archive", environment=env)
    outside = tmp_path / "private"
    outside.write_text("private-value")
    (path / "listing.html").unlink()
    (path / "listing.html").symlink_to(outside)
    with pytest.raises(OSError):
        _snapshot(path, "archive", environment=env)
    assert outside.read_text() == "private-value"


def test_preview_uses_active_archive_and_forwards_only_worker_requirements(
    tmp_path, monkeypatch
):
    env = AlternateEnvironment()
    path = tmp_path / "candidate"
    files = candidate_at(path)
    monkeypatch.setattr("webhelix.environments.get_environment", lambda _: env)
    requests = []

    def launch(request, timeout, grace):
        requests.append(request)
        assert request["settings"] == {
            "condition": "archive",
            "max_actions": 24,
            "environment": "arena-archive",
            "archive_root": str(tmp_path / "protected"),
        }
        assert _snapshot(request["candidate"], "archive", environment=env) == files
        if request.get("kind") == "feasibility":
            return {"valid": True}
        output = Path(request["output"])
        output.mkdir()
        save(output / "observation-0.json", {"dom": "captured cards"})
        save(output / "exposure-0.json", {"stage": "listing"})
        (output / "screenshot-0.png").write_bytes(b"fixture-image")
        return {"outcome": "legitimate_success"}

    monkeypatch.setattr("webhelix.opencode.preview._launch", launch)
    result = preview(
        path,
        tmp_path / "preview",
        condition="archive",
        settings={
            "environment": "arena-archive",
            "archive_root": str(tmp_path / "protected"),
            "env_file": "/private/.env",
            "victim": {"model": "not-for-preview"},
            "authorized_usd": 10,
            "unexpected_private_value": "never-forward",
        },
    )
    assert result["status"] == "preview_complete"
    assert result["candidate_hash"] == fingerprint(files)
    assert len(requests) == 2
    assert result["stages"][0]["name"] == "listing"
    assert result["real_victim_episodes"] == result["model_calls"] == 0
    assert "never-forward" not in json.dumps(requests)
    assert not (tmp_path / "preview/budget.json").exists()


def test_archive_parent_copies_rejected_measured_files_without_acceptance(tmp_path):
    env = AlternateEnvironment()
    workspace, reference, output = (tmp_path / name for name in ("work", "ref", "out"))
    retained = candidate_at(workspace / "candidate")
    rejected = {**retained, "style.css": "main {color: #456;}"}
    candidate_at(reference / "history/iter-1/candidate", rejected)
    save(
        reference / "history/iter-1/result.json",
        {
            "decision": "rejected",
            "accepted": False,
            "candidate_hash": fingerprint(rejected),
            "metrics": {"evaluation_ok": True, "steering_rate": 0},
        },
    )
    context = {
        "condition": "archive",
        "iteration": 2,
        "current_hash": fingerprint(retained),
        "prior_feedback": [{"iteration": 1}],
    }
    selector = ParentSelector(workspace, reference, output, context, environment=env)
    receipt = selector.select("1")
    assert _snapshot(workspace / "candidate", "archive", environment=env) == rejected
    assert (
        receipt["source_status"] == "rejected"
        and receipt["acceptance_changed"] is False
    )
    assert receipt["selected_candidate_hash"] == fingerprint(rejected)
    assert read(reference / "history/iter-1/result.json")["accepted"] is False


def test_archive_fixture_edits_listing_and_writes_cart_specific_note(tmp_path):
    candidate, reference, notes = (
        tmp_path / name for name in ("candidate", "reference", "notes")
    )
    original = candidate_at(candidate)
    reference.mkdir()
    notes.mkdir()
    save(
        reference / "context.json", {"condition": "archive", "current_files": original}
    )
    fixture = ToolFixture(1, environment="arena-archive")
    command = fixture._edit_command()
    for name, path in (
        ("candidate", candidate),
        ("reference", reference),
        ("notes", notes),
    ):
        command = command.replace("/work/" + name, str(path))
    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "fixture-panel-1" in (candidate / "listing.html").read_text()
    for name in ("product.html", "cart.html", "interaction.json"):
        assert (candidate / name).read_text() == original[name]
    note = (notes / "optimizer-note.md").read_text()
    assert "bouquet" in note and "local cart" in note and "$50" not in note
    assert fixture.summary["real_model_calls"] == 0
