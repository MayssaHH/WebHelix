"""Explicit search parents preserve lineage without accepting rejected files."""

import json
import subprocess
import sys

import httpx
import pytest

from tests.opencode.test_gateway import setup
from webhelix.checkout.contract import fingerprint, initial_files, load, read, save
from webhelix.opencode.gateway import Gateway
from webhelix.opencode.parent import ParentSelector


def prepared(tmp_path, measured=True):
    workspace = tmp_path / "workspace"
    reference = tmp_path / "reference"
    output = tmp_path / "output"
    retained = initial_files("combined")
    archived = {
        **retained,
        "style.css": retained["style.css"] + "\nmain {border:2px solid blue;}\n",
    }
    for directory, files in (
        (workspace / "candidate", retained),
        (reference / "history/iter-2/candidate", archived),
    ):
        directory.mkdir(parents=True)
        for name, content in files.items():
            (directory / name).write_text(content)
    save(
        reference / "history/iter-2/result.json",
        {
            "decision": "rejected",
            "accepted": False,
            "metrics": {"evaluation_ok": measured, "steering_rate": 0},
            "candidate_hash": fingerprint(archived),
        },
    )
    context = {
        "condition": "combined",
        "iteration": 3,
        "current_hash": fingerprint(retained),
        "prior_feedback": [{"iteration": 2}],
    }
    selector = ParentSelector(workspace, reference, output, context)
    return selector, workspace, reference, output, retained, archived


def test_selecting_rejected_parent_is_exact_and_does_not_accept_it(tmp_path):
    selector, workspace, reference, output, retained, archived = prepared(tmp_path)
    assert [c["selection"] for c in selector.context["choices"]] == ["retained", "2"]
    result = selector.select("2")
    assert load(workspace / "candidate", "combined") == archived
    assert result == read(output / "parent-selection.json") == selector.selected
    assert result["selected_candidate_hash"] == fingerprint(archived)
    assert result["retained_hash_before"] == fingerprint(retained)
    assert (
        result["source_status"] == "rejected" and result["acceptance_changed"] is False
    )
    assert read(reference / "history/iter-2/result.json")["accepted"] is False
    assert load(reference / "history/iter-2/candidate", "combined") == archived
    with pytest.raises(ValueError, match="only once"):
        selector.select("retained")


def test_retained_selection_records_a_fresh_start_and_keeps_files(tmp_path):
    selector, workspace, _, output, retained, _ = prepared(tmp_path)
    result = selector.select("retained")
    assert result["source_iteration"] is None
    assert result["retained_hash_before"] == result["selected_candidate_hash"]
    assert load(workspace / "candidate", "combined") == retained
    assert (output / "parent-selection.json").is_file()


@pytest.mark.parametrize("measured", [False, None, "true", 1])
def test_unevaluated_or_invalid_attempt_is_not_an_available_parent(tmp_path, measured):
    selector, *_ = prepared(tmp_path, measured=measured)
    assert [c["selection"] for c in selector.context["choices"]] == ["retained"]
    with pytest.raises(ValueError, match="allowlisted"):
        selector.select("2")


@pytest.mark.parametrize("selection", ["../../private", "3", "iter-2", 2, None])
def test_only_exact_allowlisted_identifiers_are_accepted(tmp_path, selection):
    selector, workspace, _, output, retained, _ = prepared(tmp_path)
    with pytest.raises(ValueError, match="allowlisted"):
        selector.select(selection)
    assert load(workspace / "candidate", "combined") == retained
    assert not (output / "parent-selection.json").exists()


def test_selection_cannot_erase_edits_already_made(tmp_path):
    selector, workspace, _, _, _, _ = prepared(tmp_path)
    path = workspace / "candidate/style.css"
    changed = path.read_text() + "\nmain {border:3px solid green;}\n"
    path.write_text(changed)
    with pytest.raises(ValueError, match="before making"):
        selector.select("2")
    assert path.read_text() == changed


def test_archived_parent_tampering_is_detected_before_copy(tmp_path):
    selector, workspace, reference, _, retained, _ = prepared(tmp_path)
    path = reference / "history/iter-2/candidate/style.css"
    path.write_text(path.read_text() + "\nmain {border:3px solid green;}\n")
    with pytest.raises(ValueError, match="changed after"):
        selector.select("2")
    assert load(workspace / "candidate", "combined") == retained


def test_candidate_symlink_is_not_followed_or_overwritten(tmp_path):
    selector, workspace, _, _, _, _ = prepared(tmp_path)
    outside = tmp_path / "outside.css"
    outside.write_text("private outside content")
    path = workspace / "candidate/style.css"
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(OSError):
        selector.select("2")
    assert outside.read_text() == "private outside content"


def test_real_gateway_parent_route_has_no_model_or_ledger_call(tmp_path):
    selector, workspace, _, _, _, archived = prepared(tmp_path)
    settings = setup(tmp_path)
    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "private-offline-provider-credential",
        transport=httpx.MockTransport(
            lambda _: pytest.fail("No provider request for parent selection")
        ),
        parent_callback=selector.select,
    ) as gateway:
        response = httpx.post(
            gateway.url + "/parent", json={"selection": "2"}, trust_env=False
        )
        assert response.status_code == 401
        headers = {"Authorization": "Bearer " + gateway.token}
        response = httpx.post(
            gateway.url + "/parent",
            headers=headers,
            json={"selection": "2", "path": "/tmp"},
            trust_env=False,
        )
        assert response.status_code == 400 and selector.selected is None
        response = httpx.post(
            gateway.url + "/parent",
            headers=headers,
            json={"selection": "2"},
            trust_env=False,
        )
        assert response.status_code == 200 and response.json()["selection"] == "2"
        assert load(workspace / "candidate", "combined") == archived
        response = httpx.post(
            gateway.url + "/parent",
            headers=headers,
            json={"selection": "retained"},
            trust_env=False,
        )
        assert response.status_code == 400
    assert gateway.summary["requests_received"] == 0
    assert gateway.summary["requests_forwarded"] == 0
    assert gateway.summary["parent_selection_requests"] == 2
    assert not (tmp_path / "budget.json").exists()


def test_parent_helper_uses_existing_local_gateway_capability(tmp_path):
    selector, workspace, reference, _, _, archived = prepared(tmp_path)
    settings = setup(tmp_path)
    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-provider",
        parent_callback=selector.select,
    ) as gateway:
        import os

        environment = {
            **os.environ,
            "WEBHELIX_GATEWAY_URL": gateway.url,
            "WEBHELIX_GATEWAY_TOKEN": gateway.token,
        }
        result = subprocess.run(
            [sys.executable, str(reference / "select-parent.py"), "2"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["selected_candidate_hash"] == fingerprint(
            archived
        )
        assert gateway.token not in result.stdout and gateway.token not in result.stderr
        assert load(workspace / "candidate", "combined") == archived
    assert not (tmp_path / "budget.json").exists()


def test_parent_selection_obeys_deadline_without_copying(tmp_path):
    import time

    selector, workspace, _, output, retained, _ = prepared(tmp_path)
    settings = setup(tmp_path)
    runtime = read(tmp_path / "runtime.json")
    runtime["run_deadline_epoch"] = time.time() - 1
    save(tmp_path / "runtime.json", runtime)
    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-provider",
        parent_callback=selector.select,
    ) as gateway:
        response = httpx.post(
            gateway.url + "/parent",
            headers={"Authorization": "Bearer " + gateway.token},
            json={"selection": "2"},
            trust_env=False,
        )
        assert response.status_code == 429
    assert gateway.summary["halted"] == "budget_blocked"
    assert load(workspace / "candidate", "combined") == retained
    assert not (output / "parent-selection.json").exists()
    assert not (tmp_path / "budget.json").exists()
