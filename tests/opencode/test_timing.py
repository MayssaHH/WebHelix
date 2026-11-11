"""Late previews must leave time to inspect evidence and finish a proposal."""

from webhelix.opencode import timing


def clock(monkeypatch):
    now = {"monotonic": 0, "wall": 1000}
    monkeypatch.setattr(timing.time, "monotonic", lambda: now["monotonic"])
    monkeypatch.setattr(timing.time, "time", lambda: now["wall"])
    return now


def test_preview_requires_native_session_and_preserves_finish_reserve(monkeypatch):
    now = clock(monkeypatch)
    budget = timing.PreviewSessionBudget(8000, 260)
    assert budget.admission()["reason"] == "optimizer_session_not_started"
    budget.start(600)
    first = budget.admission()
    assert first["admitted"] and first["required_seconds"] == 340
    now["monotonic"] = 260
    assert budget.admission()["admitted"]
    now["monotonic"] = 261
    denied = budget.admission()
    assert not denied["admitted"]
    assert denied["reason"] == "insufficient_session_time"
    assert denied["finalization_reserve_seconds"] == 60


def test_whole_run_time_is_independently_required(monkeypatch):
    now = clock(monkeypatch)
    budget = timing.PreviewSessionBudget(1500, 260)
    budget.start(600)
    assert budget.admission()["admitted"]
    now["wall"] = 1200
    assert budget.admission()["reason"] == "insufficient_run_time"


def test_existing_shorter_preview_configuration_keeps_its_bound(monkeypatch):
    clock(monkeypatch)
    budget = timing.PreviewSessionBudget(8000, 120)
    budget.start(250)
    result = budget.admission()
    assert result["admitted"]
    assert result["preview_budget_seconds"] == 120
    assert result["required_seconds"] == 200
