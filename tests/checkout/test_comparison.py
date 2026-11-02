import time

import pytest

from webhelix.checkout.comparison import prepare_comparison, execute_comparison
from webhelix.checkout.contract import initial_files, fingerprint, read, save
from webhelix.search.budget import Budget, BudgetBlocked


def test_identity_is_same_across_conditions():
    assert (
        len(
            {
                fingerprint(initial_files(c))
                for c in ("presentation", "interaction", "combined")
            }
        )
        == 1
    )


def test_live_prepare_is_offline_and_null_budget_blocks_before_browser(
    tmp_path, monkeypatch
):
    secret = tmp_path / "private.env"
    secret.write_text("WEBHELIX_API_KEY=not-a-real-key\n")
    run = prepare_comparison(tmp_path / "run", scripted=False, env_file=secret)
    assert read(run / "status.json")["status"] == "prepared"
    assert not (run / "budget.json").exists()

    def forbidden():
        pytest.fail("Docker/model work reached before spending-plan validation")

    monkeypatch.setattr(
        "webhelix.checkout.comparison.DockerRuntime.preflight", forbidden
    )
    with pytest.raises(BudgetBlocked):
        execute_comparison(run)
    assert read(run / "status.json")["status"] == "prepared"
    assert not (run / "batches").exists()
    with pytest.raises(ValueError, match="fresh"):
        prepare_comparison(run, scripted=True)


def test_all_arms_share_one_financial_reservation_and_episode_cap(tmp_path):
    root = tmp_path / "shared"
    common = {
        "max_real_episodes": 2,
        "max_optimizer_calls": 4,
        "max_actions": 24,
        "run_deadline_epoch": time.time() + 60,
    }
    save(root / "runtime.json", common)
    role = {
        "model": "fixture",
        "input_usd_per_million": 1,
        "output_usd_per_million": 1,
        "maximum_context_input_tokens": 100,
        "pricing_source": "test",
        "context_source": "test",
    }
    save(
        root / "spending-plan.json",
        {
            "authorized_usd": 0.00015,
            "verified_on": "test",
            "roles": {"optimizer": role, "victim": role},
        },
    )
    a = Budget(root / "a", {**common, "budget_root": str(root)})
    b = Budget(root / "b", {**common, "budget_root": str(root)})
    a.reserve("optimizer", "fixture", 10)
    with pytest.raises(BudgetBlocked, match="remaining"):
        b.reserve("optimizer", "fixture", 10)
    a.episode("first")
    b.episode("second")
    with pytest.raises(BudgetBlocked, match="episode limit"):
        a.episode("third")
    ledger = read(root / "budget.json")
    assert len(ledger["calls"]) == 1 and len(ledger["episodes"]) == 2
    assert ledger["episodes"][0]["group"] != ledger["episodes"][1]["group"]
