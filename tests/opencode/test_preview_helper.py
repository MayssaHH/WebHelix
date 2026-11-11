"""Packaged helper transport must outlive the host's bounded preview work."""

import importlib.util
import json
from pathlib import Path

import pytest

from webhelix.opencode.runtime import ROOT
from webhelix.opencode.timing import (
    HELPER_TIMEOUT_SECONDS,
    PREVIEW_TIMEOUT_SECONDS,
    SHELL_TIMEOUT_SECONDS,
)


@pytest.fixture
def helper():
    path = ROOT / "experiments/checkout/opencode-runtime/wh-preview.py"
    spec = importlib.util.spec_from_file_location("webhelix_packaged_preview", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaged_transport_ceiling_matches_host_and_enclosing_shell(
    helper, monkeypatch
):
    monkeypatch.delenv("WEBHELIX_PREVIEW_HTTP_TIMEOUT_SECONDS", raising=False)
    assert (
        PREVIEW_TIMEOUT_SECONDS < helper.transport_timeout() == HELPER_TIMEOUT_SECONDS
    )
    assert HELPER_TIMEOUT_SECONDS < SHELL_TIMEOUT_SECONDS


@pytest.mark.parametrize("value", ["0", "-1", "281", "1.5", "wrong"])
def test_malformed_or_unbounded_transport_limit_fails_before_request(
    helper, monkeypatch, value
):
    monkeypatch.setenv("WEBHELIX_PREVIEW_HTTP_TIMEOUT_SECONDS", value)
    monkeypatch.setattr(
        helper.urllib.request,
        "urlopen",
        lambda *a, **kw: pytest.fail("HTTP must not run"),
    )
    with pytest.raises(ValueError, match="Preview HTTP timeout"):
        helper.main()


@pytest.mark.parametrize("configured", [None, "140"])
def test_helper_receives_and_saves_completed_or_deferred_preview_without_model_call(
    helper, monkeypatch, tmp_path, configured
):
    if configured is None:
        monkeypatch.delenv("WEBHELIX_PREVIEW_HTTP_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("WEBHELIX_PREVIEW_HTTP_TIMEOUT_SECONDS", configured)
    monkeypatch.setenv("WEBHELIX_GATEWAY_URL", "http://local.invalid")
    monkeypatch.setenv("WEBHELIX_GATEWAY_TOKEN", "scoped-test-token")
    monkeypatch.setattr(
        helper,
        "Path",
        lambda value: tmp_path if value == "/work/previews" else Path(value),
    )
    expected_timeout = int(configured or HELPER_TIMEOUT_SECONDS)
    result = {
        "status": "preview_not_started",
        "reason": "Insufficient optimizer session time",
        "candidate_hash": "candidate-123",
        "model_calls": 0,
        "validation": {"valid": None},
        "artifacts": [],
    }
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, size):
            assert size == 16_000_001
            return json.dumps(result).encode()

    def request(req, *, timeout):
        calls.append((req.full_url, timeout))
        return Response()

    monkeypatch.setattr(helper.urllib.request, "urlopen", request)
    helper.main()
    assert calls == [("http://local.invalid/preview", expected_timeout)]
    receipt = json.loads(next(tmp_path.glob("*/receipt.json")).read_text())
    assert receipt["status"] == "preview_not_started"
    assert receipt["validation"]["valid"] is None
    assert receipt["client_transport_timeout_seconds"] == expected_timeout
    assert receipt["model_calls"] == 0
