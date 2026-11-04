"""Explicit uncapped money authorization preserves request and episode bounds."""

from decimal import Decimal
import time

import pytest

from webhelix.search.budget import Budget, BudgetBlocked, validate_plan
from webhelix.v2.contract import read, save


def plan(**changes):
    role = {
        "model": "fixture",
        "input_usd_per_million": 1,
        "output_usd_per_million": 2,
        "maximum_context_input_tokens": 10_000_000,
        "pricing_source": "unit-test",
        "context_source": "unit-test",
    }
    return {
        "authorized_usd": None,
        "verified_on": "unit-test",
        "roles": {"optimizer": dict(role), "victim": dict(role)},
        **changes,
    }


def uncapped(**changes):
    return plan(
        **{
            "usd_ceiling_disabled": True,
            "authorization": "User explicitly removed the experiment dollar ceiling.",
            **changes,
        }
    )


def budget(tmp_path, spending, **settings):
    save(tmp_path / "spending-plan.json", spending)
    return Budget(
        tmp_path,
        {
            "max_real_episodes": 1,
            "max_actions": 2,
            "max_optimizer_calls": 1,
            "run_deadline_epoch": time.time() + 60,
            **settings,
        },
    )


@pytest.mark.parametrize("settings", [{}, {"usd_ceiling_disabled": False}])
def test_default_null_amount_remains_unauthorized(settings):
    with pytest.raises(BudgetBlocked):
        validate_plan(plan(**settings), {"victim": "fixture"})


@pytest.mark.parametrize(
    "changes",
    [
        {"usd_ceiling_disabled": None},
        {"usd_ceiling_disabled": "true"},
        {"usd_ceiling_disabled": "false"},
        {"usd_ceiling_disabled": 1},
        {"usd_ceiling_disabled": 0},
        {"authorized_usd": 10},
        {"authorized_usd": "unlimited"},
        {"authorized_usd": "Infinity"},
        {"authorization": ""},
        {"authorization": "   "},
        {"authorization": None},
        {"authorization": True},
    ],
)
def test_uncapped_mode_rejects_ambiguous_authorization(changes):
    with pytest.raises(BudgetBlocked):
        validate_plan(uncapped(**changes), {"victim": "fixture"})


def test_uncapped_mode_requires_explicit_null_amount():
    spending = uncapped()
    del spending["authorized_usd"]
    with pytest.raises(BudgetBlocked):
        validate_plan(spending, {"victim": "fixture"})


def test_capped_behavior_still_blocks_reservation_exceeding_ceiling(tmp_path):
    ledger = budget(tmp_path, plan(authorized_usd=10, usd_ceiling_disabled=False))
    with pytest.raises(BudgetBlocked, match="remaining budget"):
        ledger.reserve("victim", "fixture", 20)
    assert not (tmp_path / "budget.json").exists()


def test_explicit_uncapped_mode_keeps_full_reservations_and_usage(tmp_path):
    ledger = budget(tmp_path, uncapped())
    # Existing calls remain charged; uncapped mode does not erase history.
    original = {
        "id": 0,
        "role": "optimizer",
        "model": "fixture",
        "accounted_usd": "100",
        "status": "usage_reported",
    }
    save(
        tmp_path / "budget.json",
        {"episodes": [], "calls": [original], "max_real_episodes": 1},
    )
    number = ledger.reserve("victim", "fixture", 20)
    reserved = read(tmp_path / "budget.json")
    assert number == 1 and reserved["calls"][0] == original
    assert Decimal(reserved["calls"][1]["reserved_usd"]) == Decimal("10.00004")
    assert reserved["calls"][1]["status"] == "reserved"
    ledger.settle(number, {"input_tokens": 100, "output_tokens": 10})
    settled = read(tmp_path / "budget.json")
    assert settled["calls"][0] == original
    assert settled["calls"][1]["status"] == "usage_reported"
    assert Decimal(settled["calls"][1]["accounted_usd"]) == Decimal("0.00012")


def test_uncapped_mode_preserves_request_episode_and_deadline_limits(tmp_path):
    ledger = budget(tmp_path, uncapped())
    request = ledger.reserve("optimizer", "fixture", 20)
    ledger.settle(request, {"input_tokens": 100, "output_tokens": 10})
    with pytest.raises(BudgetBlocked, match="optimizer request limit"):
        ledger.reserve("optimizer", "fixture", 20)
    ledger.episode("allowed")
    with pytest.raises(BudgetBlocked, match="episode limit"):
        ledger.episode("excess")
    ledger.settings["run_deadline_epoch"] = time.time() - 1
    with pytest.raises(BudgetBlocked, match="wall-time"):
        ledger.reserve("victim", "fixture", 20)
    with pytest.raises(BudgetBlocked, match="wall-time"):
        ledger.episode("late")
    saved = read(tmp_path / "budget.json")
    assert len(saved["calls"]) == 1 and len(saved["episodes"]) == 1


def test_uncapped_mode_preserves_victim_request_limit(tmp_path):
    ledger = budget(tmp_path, uncapped(), max_actions=1)
    ledger.reserve("victim", "fixture", 20)
    with pytest.raises(BudgetBlocked, match="victim request limit"):
        ledger.reserve("victim", "fixture", 20)


@pytest.mark.parametrize("change", ["model", "rates", "context", "verified_on"])
def test_uncapped_mode_still_requires_verified_model_and_pricing(change):
    spending = uncapped()
    if change == "model":
        spending["roles"]["victim"]["model"] = "different"
    elif change == "rates":
        spending["roles"]["victim"]["input_usd_per_million"] = 0
    elif change == "context":
        spending["roles"]["victim"]["maximum_context_input_tokens"] = 0
    else:
        spending["verified_on"] = ""
    with pytest.raises(BudgetBlocked):
        validate_plan(spending, {"victim": "fixture"})


def test_uncapped_mode_preserves_verified_usage_bound_checks(tmp_path):
    ledger = budget(tmp_path, uncapped())
    request = ledger.reserve("victim", "fixture", 20)
    with pytest.raises(BudgetBlocked, match="verified bounds"):
        ledger.settle(request, {"input_tokens": 100, "output_tokens": 21})
    assert (
        read(tmp_path / "budget.json")["calls"][0]["status"]
        == "verified_bound_violated"
    )
