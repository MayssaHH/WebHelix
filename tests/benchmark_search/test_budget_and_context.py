import json

import httpx
import pytest

from webhelix.benchmark.budget import Budget, BudgetBlocked, validate_plan
from webhelix.benchmark.contract import FIXTURES, ROOT, load, save
from webhelix.benchmark.optimizer import context_for, VisualProposalClient
from webhelix.benchmark.policy import VictimPolicy
from webhelix.optimizer.client import Settings


def plan(root, amount):
    spec = {
        "model": "unit-fixture",
        "input_usd_per_million": 1,
        "output_usd_per_million": 2,
        "maximum_context_input_tokens": 1000,
        "pricing_source": "unit fixture",
        "context_source": "unit fixture",
    }
    save(
        root / "spending-plan.json",
        {
            "authorized_usd": amount,
            "verified_on": "unit-test",
            "roles": {"optimizer": spec, "victim": spec},
        },
    )


def test_missing_budget_never_authorizes_calls(tmp_path):
    plan(tmp_path, None)
    budget = Budget(tmp_path, {"max_real_episodes": 12, "max_actions": 12})
    with pytest.raises(BudgetBlocked):
        budget.reserve("victim", "unit-fixture", 100)
    assert not (tmp_path / "budget.json").exists()


@pytest.mark.parametrize("amount", [None, 0, -1, "NaN", "Infinity", "not-a-number"])
def test_invalid_dollar_amounts_fail_before_execution(tmp_path, amount):
    plan(tmp_path, amount)
    with pytest.raises(BudgetBlocked):
        validate_plan(
            json.loads((tmp_path / "spending-plan.json").read_text()),
            {"victim": "unit-fixture"},
        )


def test_both_roles_need_verified_matching_model_settings(tmp_path):
    plan(tmp_path, 1)
    with pytest.raises(BudgetBlocked):
        validate_plan(
            json.loads((tmp_path / "spending-plan.json").read_text()),
            {
                "victim": "unit-fixture",
                "optimizer": "another-model",
            },
        )


def test_reservations_include_failed_calls_and_enforce_remaining_cap(tmp_path):
    plan(tmp_path, 0.002)
    budget = Budget(tmp_path, {"max_real_episodes": 12, "max_actions": 12})
    first = budget.reserve("victim", "unit-fixture", 100)
    budget.settle(first, {})  # A timeout does not get a free retry.
    with pytest.raises(BudgetBlocked, match="remaining"):
        budget.reserve("victim", "unit-fixture", 100)
    assert (
        json.loads((tmp_path / "budget.json").read_text())["calls"][0]["accounted_usd"]
        == "0.0012"
    )


def test_zero_usage_release_and_episode_cap(tmp_path):
    plan(tmp_path, 1)
    budget = Budget(tmp_path, {"max_real_episodes": 1, "max_actions": 12})
    number = budget.reserve("optimizer", "unit-fixture", 100)
    budget.settle(number, {"input_tokens": 0, "output_tokens": 0})
    budget.episode("failure-still-counts")
    with pytest.raises(BudgetBlocked, match="episode limit"):
        budget.episode("retry")


def test_next_optimizer_receives_actual_zero_measurement_and_rejected_candidate(
    tmp_path,
):
    workspace, run = tmp_path / "project", tmp_path / "run"
    save(run / "runtime.json", {"condition": "truthful"})
    clean = run / "clean-batch"
    save(
        clean / "episode-0/observation-0.json",
        {"visible_text": "Price", "controls": []},
    )
    save(run / "clean.json", {"batch": str(clean)})
    save(workspace / "TASK.json", {"intent": "Reduce the price by $5"})
    (workspace / "CONTRACT.md").write_text("test contract")
    for name, text in load(FIXTURES / "candidate", "truthful").items():
        p = workspace / "candidate" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    base = workspace / ".autohelix/observations/iter-1"
    save(base / "result.json", {"accepted": False, "metrics": {"steering_rate": 0.0}})
    (base / "candidate").mkdir()
    (base / "candidate/style.css").write_text(".panel {color: navy}")
    notes = workspace / ".autohelix/notes"
    notes.mkdir()
    (notes / "iter-1.md").write_text("Prior predicted effect, not a measurement")
    context = context_for(workspace, run, 2, "goal")
    feedback = context["prior_feedback"][0]
    assert feedback["measurements"]["metrics"]["steering_rate"] == 0
    assert not feedback["measurements"]["accepted"]
    assert (
        feedback["candidate_files"]["style.css"]
        != context["current_files"]["style.css"]
    )
    assert feedback["optimizer_note"] == "Prior predicted effect, not a measurement"


def test_victim_requests_have_visual_input_and_no_optimizer_context(tmp_path):
    sent = []

    def handle(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(
            200,
            json={
                "model": "unit-fixture",
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"action":"click","bid":"5","text":""}',
                            },
                        ],
                    }
                ],
            },
        )

    class NoPaidBudget:
        def reserve(self, *args):
            return 0

        def settle(self, *args):
            pass

    env = tmp_path / "private.env"
    env.write_text("WEBHELIX_API_KEY=unit-fixture-credential\n")
    settings = json.loads((FIXTURES / "pilot.json").read_text())["victim"]
    settings["model"] = "unit-fixture"
    victim = VictimPolicy(
        "Reduce the price by $5",
        tmp_path,
        settings,
        env,
        NoPaidBudget(),
        httpx.MockTransport(handle),
    )
    observation = {"visible_text": "Price", "controls": [{"bid": "5", "name": "Save"}]}
    victim.act(observation, ROOT / "examples/benchmark-admin/clean-product.png", 0)
    victim.close()
    body = sent[0]
    assert body["input"][0]["content"] == "Reduce the price by $5"
    assert body["input"][-1]["content"][1]["type"] == "input_image"
    serialized = json.dumps(body)
    assert "unit-fixture-credential" not in serialized
    assert "prior_feedback" not in serialized and "steering_rate" not in serialized
    assert body["max_output_tokens"] == 1024 and body["reasoning"]["effort"] == "medium"
    assert body["service_tier"] == "default"


def test_optimizer_image_input_uses_explicit_protocol(tmp_path):
    for protocol in ("responses", "chat_completions"):
        client = VisualProposalClient(
            Settings(
                endpoint="https://example.invalid/fixture",
                protocol=protocol,
                model="unit-fixture",
            ),
            tmp_path,
        )
        client.images = [
            ("Clean reference", ROOT / "examples/benchmark-admin/clean-product.png")
        ]
        body = client.make_payload("system", {"goal": "fixture"})
        messages = body["input" if protocol == "responses" else "messages"]
        assert messages[-1]["content"][-1]["type"] == (
            "input_image" if protocol == "responses" else "image_url"
        )
        if protocol == "responses":
            assert body["service_tier"] == "default"


def test_malformed_proposal_is_rejected_before_writing_candidate(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import webhelix.benchmark.optimizer as optimizer

    workspace, run = tmp_path / "workspace", tmp_path / "run"
    workspace.mkdir()
    original = load(FIXTURES / "candidate", "truthful")
    (workspace / "candidate").mkdir()
    for name, text in original.items():
        (workspace / "candidate" / name).write_text(text)
    save(run / "clean.json", {"batch": str(run / "clean")})
    monkeypatch.setattr(
        optimizer, "context_for", lambda *_: {"current_files": original}
    )

    class MalformedClient:
        settings = SimpleNamespace(model="unit-fixture")

        def generate(self, system, context, output):
            save(output / "proposal.json", ["valid JSON, but not a proposal"])
            return {"status": "proposal_received"}

    result = optimizer.BenchmarkBackend(run, MalformedClient()).run(
        workspace,
        "test goal",
        1,
        tmp_path / "agent.log",
        None,
    )
    assert not result.success and result.error == "invalid_proposal"
    assert load(workspace / "candidate", "truthful") == original
    assert not (run / "proposals/iter-1/optimizer-note.md").exists()


def test_continuity_checks_actual_note_not_just_file_existence(tmp_path):
    from webhelix.benchmark.experiment import feedback_continuity

    files = load(FIXTURES / "candidate", "truthful")
    previous = tmp_path / "feedback/iter-1"
    save(previous / "result.json", {"metrics": {"steering_rate": 0.0}})
    (previous / "optimizer-note.md").write_text("Actual hypothesis")
    (previous / "candidate").mkdir()
    for name, value in files.items():
        (previous / "candidate" / name).write_text(value)
    context = {
        "prior_feedback": [
            {
                "measurements": {"metrics": {"steering_rate": 0.0}},
                "optimizer_note": "Different hypothesis",
                "candidate_files": files,
            }
        ]
    }
    path = tmp_path / "proposals/iter-2/optimizer-context.json"
    save(path, context)
    assert not feedback_continuity(tmp_path, "truthful")["optimizer_note_matches"]
    context["prior_feedback"][0]["optimizer_note"] = (
        "Actual hypothesis\n\nPre-evaluation optimizer note.\n"
    )
    save(path, context)
    assert feedback_continuity(tmp_path, "truthful")["optimizer_note_matches"]
