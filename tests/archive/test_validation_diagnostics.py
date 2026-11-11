"""Preserve the failed comparison even when later browser capture is unavailable."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from webhelix.archive.episode import faithful_facts
from webhelix.archive.validation import ValidationDiagnostics
from webhelix.checkout.contract import InvalidCandidate


def read(path):
    return json.loads(path.read_text())


class FakePage:
    """No browser/model calls: state can differ from the failed fact snapshot."""

    def __init__(self, name, output, *, facts=(), fail=()):
        self.name = name
        self.url = f"http://local/{name}"
        self.output = output
        self.facts = list(facts)
        self.fail = set(fail)
        self.calls = []

    def locator(self, selector):
        assert selector == "[data-wh-fact]"
        return self

    async def evaluate_all(self, expression):
        self.calls.append("facts")
        return self.facts

    async def evaluate(self, expression):
        self.calls.append("state")
        # The initial error must already be durable before a browser operation
        # that can itself fail or be interrupted by the parent worker deadline.
        assert read(self.output / "failure.json")["error"]
        if "state" in self.fail:
            raise RuntimeError("Target page has been closed")
        return {
            "replay": {"stage": "cart", "rendered_cart_items": []},
            "cart_size": 0 if self.name == "candidate" else "0",
            "cart_size_type": "number" if self.name == "candidate" else "string",
            "pending_requests": 0,
        }

    async def content(self):
        self.calls.append("html")
        if "html" in self.fail:
            raise RuntimeError("Target page has been closed")
        return f"<main>{self.name} state after assertion</main>"

    async def screenshot(self, *, path):
        self.calls.append("screenshot")
        if "screenshot" in self.fail:
            raise RuntimeError("Screenshot unavailable")
        Path(path).write_bytes(b"synthetic screenshot fixture")


def site():
    return SimpleNamespace(snapshot=lambda: {"cart": [], "version": 2})


def test_phase_is_durable_while_running_and_after_completion(tmp_path):
    diagnostics = ValidationDiagnostics(tmp_path, "candidate-hash")
    with diagnostics.phase("reference_restore", step=7):
        running = read(tmp_path / "validation-timing.json")
        assert running["candidate_hash"] == "candidate-hash"
        assert running["phases"][0]["status"] == "running"
        assert running["phases"][0]["step"] == 7
        assert "elapsed_seconds" not in running["phases"][0]
    finished = read(tmp_path / "validation-timing.json")
    assert finished["phases"][0]["status"] == "completed"
    assert finished["phases"][0]["elapsed_seconds"] >= 0
    assert finished["phases"][0]["offset_seconds"] >= 0
    assert finished["phases"][0]["started_at"].endswith("+00:00")


@pytest.mark.parametrize(
    "error", [InvalidCandidate("Changed price"), asyncio.CancelledError()]
)
def test_failed_phase_preserves_original_exception_and_records_failure(tmp_path, error):
    diagnostics = ValidationDiagnostics(tmp_path, "candidate-hash")
    with pytest.raises(type(error)) as raised:
        with diagnostics.phase("fact_comparison", step=7):
            raise error
    assert raised.value is error
    failure = read(tmp_path / "validation-timing.json")["phases"][0]
    assert failure["status"] == "failed"
    assert failure["error_type"] == type(error).__name__
    assert failure["error"] == str(error)
    assert failure["elapsed_seconds"] >= 0


@pytest.mark.parametrize(
    "error", [InvalidCandidate("Protected price changed"), asyncio.CancelledError()]
)
def test_timing_write_failure_during_unwind_cannot_replace_original_error(
    tmp_path, monkeypatch, error
):
    diagnostics = ValidationDiagnostics(tmp_path, "candidate-hash")
    persist = diagnostics._write_timing

    def disk_fails_after_assertion():
        if diagnostics.phases[-1]["status"] == "failed":
            raise OSError("Diagnostic volume is full")
        persist()

    monkeypatch.setattr(diagnostics, "_write_timing", disk_fails_after_assertion)
    with pytest.raises(type(error)) as raised:
        with diagnostics.phase("fact_comparison", step=7):
            raise error
    assert raised.value is error
    assert diagnostics.phases[-1]["status"] == "failed"
    # A failed evidence write leaves an incomplete checkpoint, never an invented
    # completed validation or a replacement diagnosis of the candidate failure.
    saved = read(tmp_path / "validation-timing.json")
    assert saved["phases"][-1]["status"] == "running"


@pytest.mark.parametrize("failed_status", ["running", "completed"])
def test_cleanup_timing_failure_still_runs_cleanup_and_preserves_primary_error(
    tmp_path, monkeypatch, failed_status
):
    diagnostics = ValidationDiagnostics(tmp_path, "candidate-hash")
    persist = diagnostics._write_timing
    original = InvalidCandidate("Original protected fact failure")
    cleanup = []

    def fail_cleanup_write():
        phase = diagnostics.phases[-1]
        if phase["phase"] == "browser_cleanup" and phase["status"] == failed_status:
            raise OSError("Diagnostic volume is full during cleanup")
        persist()

    monkeypatch.setattr(diagnostics, "_write_timing", fail_cleanup_write)
    with pytest.raises(InvalidCandidate) as raised:
        try:
            raise original
        finally:
            with diagnostics.phase("browser_cleanup"):
                cleanup.append("browser closed")

    assert cleanup == ["browser closed"]
    assert raised.value is original


@pytest.mark.parametrize("failed_status", ["running", "completed"])
def test_timing_storage_failure_without_primary_error_remains_infrastructure_error(
    tmp_path, monkeypatch, failed_status
):
    diagnostics = ValidationDiagnostics(tmp_path, "candidate-hash")
    persist = diagnostics._write_timing
    original = OSError("Diagnostic volume is full")
    cleanup = []

    def fail_write():
        if diagnostics.phases[-1]["status"] == failed_status:
            raise original
        persist()

    monkeypatch.setattr(diagnostics, "_write_timing", fail_write)
    with pytest.raises(OSError) as raised:
        with diagnostics.phase("browser_cleanup"):
            cleanup.append("browser closed")

    assert raised.value is original
    assert cleanup == ([] if failed_status == "running" else ["browser closed"])


def test_fact_failure_keeps_asserted_arrays_and_both_later_browser_states(tmp_path):
    async def check():
        expected = [
            {
                "key": "source-fact-0",
                "text": "£0.00",
                "readable": True,
                "unclipped": True,
            }
        ]
        actual = [
            {
                "key": "listing:a:0:0",
                "text": "Bouquet",
                "readable": True,
                "unclipped": True,
            }
        ]
        page = FakePage("candidate", tmp_path, facts=actual)
        reference = FakePage("reference", tmp_path, facts=expected)
        diagnostics = ValidationDiagnostics(tmp_path, "candidate-hash")
        target = {"stage": "cart", "drawer_open": True}
        diagnostics.reference_target = target
        with pytest.raises(InvalidCandidate) as raised:
            with diagnostics.phase("fact_comparison", step=7):
                await faithful_facts(page, reference, diagnostics=diagnostics)
        await diagnostics.failure(
            raised.value, site(), page, reference, actions=["remove:bouquet"]
        )
        report = read(tmp_path / "failure.json")
        assert report["error"] == "Source fact hidden or changed: source-fact-0"
        assert report["error_type"] == "InvalidCandidate"
        assert report["candidate_hash"] == "candidate-hash"
        assert report["phase"]["step"] == 7
        assert report["reference_target"] == target
        assert report["comparison"]["reference_facts"] == expected
        assert report["comparison"]["candidate_facts"] == actual
        assert report["backend"] == {"cart": [], "version": 2}
        assert report["actions"] == ["remove:bouquet"]
        assert report["pages"]["candidate"]["state"]["cart_size"] == 0
        assert report["pages"]["reference"]["state"]["cart_size"] == "0"
        assert report["capture_errors"] == {}
        assert (
            tmp_path / "failure.html"
        ).read_text() == "<main>candidate state after assertion</main>"
        assert (
            tmp_path / "failure-reference.html"
        ).read_text() == "<main>reference state after assertion</main>"
        assert page.calls == ["facts", "state", "html", "screenshot"]
        assert reference.calls == ["facts", "state", "html", "screenshot"]
        assert "valid" not in report and "outcome" not in report

    asyncio.run(check())


@pytest.mark.parametrize("failed", [{"screenshot"}, {"state", "html", "screenshot"}])
def test_capture_errors_do_not_erase_assertion_or_skip_the_other_page(tmp_path, failed):
    async def check():
        diagnostics = ValidationDiagnostics(tmp_path, "candidate-hash")
        diagnostics.compared([{"key": "price", "text": "£35"}], [])
        original = InvalidCandidate("Source fact hidden or changed: price")
        page = FakePage("candidate", tmp_path, fail=failed)
        reference = FakePage("reference", tmp_path)
        await diagnostics.failure(original, site(), page, reference)
        report = read(tmp_path / "failure.json")
        assert report["error"] == str(original)
        assert report["error_type"] == "InvalidCandidate"
        assert report["comparison"]["candidate_facts"] == []
        assert set(report["capture_errors"]) == {f"candidate.{kind}" for kind in failed}
        assert report["pages"]["reference"]["html"] == "failure-reference.html"
        assert report["pages"]["reference"]["screenshot"] == "failure-reference.png"
        assert not (tmp_path / "failure.png").exists()

    asyncio.run(check())


def test_uncreated_reference_is_recorded_after_candidate_capture(tmp_path):
    diagnostics = ValidationDiagnostics(tmp_path, "candidate-hash")
    asyncio.run(
        diagnostics.failure(
            RuntimeError("Reference setup failed"),
            site(),
            FakePage("candidate", tmp_path),
            None,
        )
    )
    report = read(tmp_path / "failure.json")
    assert report["error"] == "Reference setup failed"
    assert report["capture_errors"]["reference"] == "Page was not created"


def test_disabled_capture_does_not_access_browser_or_create_artifacts():
    diagnostics = ValidationDiagnostics(None, "candidate-hash")
    # No output directory means this must return without consulting the objects.
    asyncio.run(
        diagnostics.failure(RuntimeError("failed"), object(), object(), object())
    )
