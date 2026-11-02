"""Explicit model metadata survives settings loading and rejects invalid limits."""

import json

import pytest

from webhelix.opencode.config import OpenCodeSettings


def test_context_default_preserves_legacy_sol_settings():
    assert OpenCodeSettings().context_window_tokens == 1050000


def test_mini_context_is_loaded_and_serialized(tmp_path):
    path = tmp_path / "optimizer.json"
    path.write_text(
        json.dumps({"model": "gpt-5.4-mini", "context_window_tokens": 400000})
    )
    settings = OpenCodeSettings.load(path)
    assert settings.model == "gpt-5.4-mini"
    assert settings.as_dict()["context_window_tokens"] == 400000
    assert settings.reasoning_effort == "medium"


@pytest.mark.parametrize("value", [True, 0, -1, 1050001, 400000.0, "400000"])
def test_unverified_context_bounds_are_rejected(value):
    with pytest.raises(ValueError, match="context_window_tokens"):
        OpenCodeSettings(context_window_tokens=value)


def test_output_reservation_must_fit_context():
    with pytest.raises(ValueError, match="smaller than context_window_tokens"):
        OpenCodeSettings(context_window_tokens=8192, max_output_tokens=8192)
