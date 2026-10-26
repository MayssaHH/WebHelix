import json
import time

import pytest

from webhelix.checkout.cli import initialize
from webhelix.checkout.contract import FIXTURES, read, save
from webhelix.search.budget import Budget, BudgetBlocked


def test_scripted_setup_has_no_real_episode_allowance_or_key_dependency(tmp_path):
    destination = tmp_path / "new"
    result = initialize(destination)
    assert result["max_real_episodes"] == 0
    assert result["episodes_per_condition"] == 1
    assert result["env_file"] is None
    assert not (destination / "budget.json").exists()
    with pytest.raises(ValueError, match="already exists"):
        initialize(destination)


def test_shipped_unapproved_plan_blocks_real_mode_before_creating_run(tmp_path):
    env = tmp_path / "private.env"
    env.write_text("WEBHELIX_API_KEY=test-only\nWEBHELIX_OPTIMIZER_API_KEY=test-only\n")
    destination = tmp_path / "real"
    with pytest.raises(BudgetBlocked):
        initialize(
            destination,
            scripted=False,
            env_file=env,
            spending_plan=FIXTURES / "spending-plan.example.json",
            optimizer_config=FIXTURES / "optimizer.example.json",
        )
    assert not destination.exists()


def test_optimizer_request_cap_is_configured_and_deadline_blocks_episodes(tmp_path):
    role = {
        "model": "offline-fixture",
        "input_usd_per_million": 1,
        "output_usd_per_million": 2,
        "maximum_context_input_tokens": 100,
        "pricing_source": "unit-test",
        "context_source": "unit-test",
    }
    save(
        tmp_path / "spending-plan.json",
        {
            "authorized_usd": 1,
            "verified_on": "unit-test",
            "roles": {"optimizer": role, "victim": role},
        },
    )
    settings = {"max_real_episodes": 12, "max_actions": 24, "max_optimizer_calls": 1}
    budget = Budget(tmp_path, settings)
    request = budget.reserve("optimizer", "offline-fixture", 20)
    budget.settle(request, {"input_tokens": 10, "output_tokens": 5})
    with pytest.raises(BudgetBlocked, match="request limit"):
        budget.reserve("optimizer", "offline-fixture", 20)
    settings["run_deadline_epoch"] = time.time() - 1
    with pytest.raises(BudgetBlocked, match="wall-time"):
        budget.episode("late")
    assert read(tmp_path / "budget.json")["episodes"] == []
    assert "test-only" not in json.dumps(read(tmp_path / "budget.json"))
