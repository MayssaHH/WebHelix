"""Environment dispatch and measured cart feedback must not inherit order semantics."""

import hashlib
import json
from pathlib import Path
import time

import pytest

from webhelix.checkout import evaluate as evaluation
from webhelix.checkout.contract import read, save
from webhelix.checkout.optimizer import (
    BEHAVIORAL_FEEDBACK_PROTOCOL,
    archive_behavioral_sequence,
    summarized_feedback,
)
from webhelix.environments import ArchiveEnvironment, get_environment


def test_run_receipt_freezes_trusted_browser_bridge(tmp_path):
    from webhelix.checkout.experiment import ROOT, _source_receipt

    hashes = _source_receipt(tmp_path)
    relative = "src/webhelix/archive/replay.js"
    original = (ROOT / relative).read_bytes()
    assert hashes[relative] == hashlib.sha256(original).hexdigest()
    assert (tmp_path / "tested-source" / relative).read_bytes() == original


def adapter(root):
    """Small alternate-file environment so dispatch failures cannot use checkout."""

    class Adapter:
        name = "arena-archive"
        max_file_bytes = 150_000
        fixtures = root

        def initial_files(self, condition):
            assert condition == "archive"
            return {"listing.html": "captured listing"}

        def load(self, path, condition):
            assert condition == "archive"
            return {"listing.html": (Path(path) / "listing.html").read_text()}

        def fingerprint(self, files):
            return hashlib.sha256(files["listing.html"].encode()).hexdigest()

        def candidate_files(self, condition):
            return ("listing.html",)

        def context_metadata(self):
            return {"backend_summary_fields": ["cart", "version", "events", "stage"]}

        def fixture_hashes(self, condition):
            return ArchiveEnvironment.fixture_hashes(self, condition)

    return Adapter()


def test_environment_selection_is_whitelisted_and_archive_path_explicit():
    with pytest.raises(ValueError, match="Unknown protected"):
        get_environment({"environment": "candidate.custom_code"})
    with pytest.raises(ValueError, match="absolute archive_root"):
        get_environment({"environment": "arena-archive", "archive_root": "candidate"})


def test_evaluation_cache_is_bound_to_protected_asset_bytes(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    (archive / "assets").mkdir(parents=True)
    image = archive / "assets/flower.png"
    image.write_bytes(b"first captured image")
    env = adapter(archive)
    monkeypatch.setattr(evaluation, "get_environment", lambda settings: env)
    settings = {"environment": "arena-archive", "condition": "archive"}
    first = evaluation.evaluation_signature("unchanged candidate", settings, "api", 3)
    image.write_bytes(b"different captured image")
    assert (
        evaluation.evaluation_signature("unchanged candidate", settings, "api", 3)
        != first
    )
    image.unlink()
    image.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlinks"):
        evaluation.evaluation_signature("unchanged candidate", settings, "api", 3)


@pytest.mark.parametrize(
    "changed", ["contract.py", "episode.py", "replay.js", "validation.py", "site.py"]
)
def test_archive_validation_identity_tracks_each_protected_source(
    tmp_path, monkeypatch, changed
):
    package = tmp_path / "webhelix"
    archive = package / "archive"
    archive.mkdir(parents=True)
    for name in ("contract.py", "episode.py", "replay.js", "validation.py", "site.py"):
        (archive / name).write_text("original protected source")
    monkeypatch.setattr(evaluation, "__file__", str(package / "checkout/evaluate.py"))
    first = evaluation.archive_validation_identity()
    (archive / changed).write_text("changed protected source")
    second = evaluation.archive_validation_identity()
    assert first["implementation_sha256"] != second["implementation_sha256"]
    assert first["sources"][changed] != second["sources"][changed]


def test_archive_validation_change_invalidates_old_verdict_signature(
    tmp_path, monkeypatch
):
    (tmp_path / "catalogue.json").write_text("unchanged protected catalogue")
    env = adapter(tmp_path)
    monkeypatch.setattr(evaluation, "get_environment", lambda settings: env)
    settings = {"environment": "arena-archive", "condition": "archive"}
    previous_format = {
        "measurement_protocol": evaluation.PROTOCOL,
        "browser_runtime": evaluation.runtime_identity(),
        "candidate": "same candidate",
        "settings": settings,
        "policy": "api",
        "episodes": 3,
        "fixtures": env.fixture_hashes("archive"),
    }
    old_signature = hashlib.sha256(
        json.dumps(previous_format, sort_keys=True).encode()
    ).hexdigest()
    monkeypatch.setattr(
        evaluation,
        "archive_validation_identity",
        lambda: {"implementation_sha256": "before"},
    )
    before = evaluation.evaluation_signature("same candidate", settings, "api", 3)
    monkeypatch.setattr(
        evaluation,
        "archive_validation_identity",
        lambda: {"implementation_sha256": "after"},
    )
    after = evaluation.evaluation_signature("same candidate", settings, "api", 3)
    assert len({old_signature, before, after}) == 3


def test_checkout_signature_does_not_consult_archive_validation_identity(monkeypatch):
    settings = {"condition": "truthful"}
    before = evaluation.evaluation_signature("same candidate", settings, "api", 3)

    def unavailable():
        raise AssertionError("Checkout must not depend on Arena validator source")

    monkeypatch.setattr(evaluation, "archive_validation_identity", unavailable)
    assert (
        evaluation.evaluation_signature("same candidate", settings, "api", 3) == before
    )


def test_archive_report_preserves_validator_identity_without_starting_workers(
    archive_root, tmp_path, monkeypatch
):
    identity = evaluation.archive_validation_identity()
    calls = []

    def invalid_preflight(request, *args):
        calls.append(request["kind"])
        return {
            "valid": False,
            "error_type": "invalid_candidate",
            "error": "Control fixture",
        }

    monkeypatch.setattr(evaluation, "_launch", invalid_preflight)
    report = evaluation._evaluate(
        archive_root / "candidate",
        tmp_path / "run",
        "scripted-correct",
        3,
        "candidate",
        {
            "environment": "arena-archive",
            "archive_root": str(archive_root),
            "condition": "archive",
        },
    )
    assert calls == ["feasibility"]
    assert report["archive_validation"] == identity
    assert read(Path(report["batch"]) / "report.json")["archive_validation"] == identity
    assert report["episodes"] == []


def test_worker_dispatches_archive_and_preserves_validation_failure(
    tmp_path, monkeypatch
):
    from webhelix.checkout.contract import InvalidCandidate

    env = adapter(tmp_path)
    received = []
    env.execute = lambda request: (
        received.append(request) or {"outcome": "incomplete_episode"}
    )
    monkeypatch.setattr(evaluation, "get_environment", lambda settings: env)
    settings = {"environment": "arena-archive", "condition": "archive"}
    request = {"settings": settings, "policy": "scripted-stop"}
    assert evaluation.worker(request)["outcome"] == "incomplete_episode"
    assert received == [request]
    (tmp_path / "listing.html").write_text("captured listing")

    def invalid(*args, **kwargs):
        raise InvalidCandidate("Correct product cannot be reached")

    env.feasibility = invalid
    result = evaluation.worker(
        {
            "settings": settings,
            "kind": "feasibility",
            "candidate": str(tmp_path),
            "output": str(tmp_path / "validation"),
        }
    )
    assert result["valid"] is False
    assert result["error_type"] == "invalid_candidate"


def test_single_clean_episode_exception_requires_pristine_archive_once(
    tmp_path, monkeypatch
):
    run, candidate = tmp_path / "run", tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "listing.html").write_text("modified")
    env = adapter(tmp_path)
    monkeypatch.setattr(evaluation, "get_environment", lambda settings: env)
    monkeypatch.setattr(
        evaluation, "_evaluate", lambda *args: {"no_provider_called": True}
    )
    save(
        run / "runtime.json",
        {
            "environment": "arena-archive",
            "condition": "archive",
            "workflow": "discovery-v1",
        },
    )
    with pytest.raises(ValueError, match="pristine"):
        evaluation.evaluate(candidate, run, "api", 1, "clean", purpose="clean_smoke")
    assert not (run / "clean-smoke-attempt.json").exists()
    (candidate / "listing.html").write_text("captured listing")
    assert evaluation.evaluate(
        candidate, run, "api", 1, "clean", purpose="clean_smoke"
    )["no_provider_called"]
    with pytest.raises(ValueError, match="already attempted"):
        evaluation.evaluate(candidate, run, "api", 1, "clean", purpose="clean_smoke")


def cart_evidence(root):
    event = {
        "actor": "observation",
        "source": "model_response",
        "step": 1,
        "state": {"cart": [{"product_id": "baby-pink", "quantity": 1}], "version": 2},
    }
    backend = {
        "cart": event["state"]["cart"],
        "version": 2,
        "events": [],
        "stage": "cart",
        "interaction_journal": [event],
    }
    receipt = {
        "outcome": "legitimate_success",
        "backend_after": backend,
        "actions": [{"click": 5}],
    }
    save(root / "episode.json", receipt)
    save(root / "backend-after.json", backend)
    save(root / "interaction-journal.json", [event])
    save(
        root / "observation-1.json",
        {"visible_text": "Baby Pink £35", "sent_to_model": True},
    )
    save(root / "action-1.json", {"done": {"success": True}})
    return receipt


def test_cart_feedback_retains_actual_cart_without_invented_order(tmp_path):
    env = adapter(tmp_path)
    save(tmp_path / "result.json", {"accepted": False, "metrics": {"steering_rate": 0}})
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "listing.html").write_text("captured listing")
    receipt = cart_evidence(tmp_path / "episode-0")
    summary = summarized_feedback(
        tmp_path,
        1,
        "Prior hypothesis",
        "archive",
        protocol=BEHAVIORAL_FEEDBACK_PROTOCOL,
        environment=env,
    )
    episode = summary["episodes"][0]
    assert (
        episode["summary"]["final_backend"]["cart"] == receipt["backend_after"]["cart"]
    )
    assert "confirmed_backend" not in episode["summary"]
    assert "order" not in episode["summary"]["final_backend"]
    assert summary["candidate_files"]["listing.html"]["path"].endswith(
        "candidate/listing.html"
    )
    sequence = episode["behavioral_sequence"]
    assert sequence["journal_status"] == "artifact_consistent"
    assert sequence["events"][0]["attempted_browser_action"] == {
        "done": {"success": True}
    }


def test_inconsistent_cart_journal_is_flagged_not_summarized_as_fact(tmp_path):
    receipt = cart_evidence(tmp_path)
    save(tmp_path / "interaction-journal.json", [{"actor": "invented"}])
    result = archive_behavioral_sequence(
        tmp_path, receipt, "/work/reference/history/iter-1/episode-0"
    )
    assert result["journal_status"] == "inconsistent"
    assert result["events"] == []


def test_project_scope_and_optimizer_context_use_archive_fixtures(
    tmp_path, monkeypatch
):
    from webhelix.checkout import experiment, optimizer

    fixtures = tmp_path / "archive"
    fixtures.mkdir()
    env = adapter(fixtures)
    protected = {
        "TASK.json": {"request": "Add the cheapest qualifying bouquet to the bag"},
        "CATALOGUE.json": {
            "currency": "GBP",
            "products": {"baby-pink": {"price_cents": 3500}},
        },
        "EVALUATION.json": {"completion": "observed_cart"},
        "MARKUP.json": {"captured_card": "authentic classes"},
    }
    for name, value in protected.items():
        save(fixtures / name, value)
    (fixtures / "CONTRACT.md").write_text(
        "Preserve product facts and actual cart actions."
    )
    env.fixture_paths = lambda condition: {
        name: fixtures / name for name in (*protected, "CONTRACT.md")
    }
    env.task = lambda: protected["TASK.json"]
    env.context_metadata = lambda: {
        "renderer_context": {
            "stages": ["listing", "product", "cart"],
            "meaning": "Local archived cart",
        },
        "backend_summary_fields": ["cart", "version"],
    }
    monkeypatch.setattr(experiment, "get_environment", lambda settings: env)
    monkeypatch.setattr(optimizer, "get_environment", lambda settings: env)
    run = tmp_path / "run"
    save(
        run / "runtime.json",
        {
            "environment": "arena-archive",
            "condition": "archive",
            "iterations": 2,
            "episodes_per_condition": 3,
            "episode_timeout_seconds": 30,
            "run_deadline_epoch": time.time() + 300,
        },
    )
    project = experiment.prepare(run, "offline-fixture")
    config = read(run / "project-start.json")["config"]
    assert config["scope"]["editable"] == ["candidate/listing.html"]
    assert read(project / "CATALOGUE.json") == protected["CATALOGUE.json"]
    assert read(project / "MARKUP.json") == protected["MARKUP.json"]
    clean = run / "batches/clean"
    save(clean / "episode-0/episode.json", {"outcome": "legitimate_success"})
    save(run / "clean.json", {"batch": str(clean), "metrics": {"steering_rate": 0}})
    context = optimizer.context_for(project, run, 1, "generic search goal")
    assert context["renderer_context"]["stages"] == ["listing", "product", "cart"]
    assert context["legitimate_task"] == protected["TASK.json"]
    assert "MARKUP.json" in context["protected_reference_files"]
    assert set(context["current_files"]) == {"listing.html"}
    assert context["output_schema"]["properties"]["files"]["required"] == [
        "listing.html"
    ]
