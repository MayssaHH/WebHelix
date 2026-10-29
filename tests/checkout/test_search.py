"""Optimizer contexts and durable memories without any provider requests."""

import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

import httpx
import pytest
from autohelix.harness import AutoHelixRunError, Harness

from webhelix.checkout.contract import FILES, FIXTURES, load, read, save, validate
from webhelix.checkout.experiment import CheckoutHarness, feedback_continuity, prepare
from webhelix.checkout.optimizer import (
    CheckoutBackend,
    FixtureClient,
    context_for,
    stage_images,
    VisualProposalClient,
)
from webhelix.optimizer.client import Settings


def test_root_url_frame_is_labelled_product_not_random_server_port(tmp_path):
    save(tmp_path / "observation-0.json", {"url": "http://127.0.0.1:54321/"})
    (tmp_path / "screenshot-0.png").write_bytes(b"placeholder; no image decoding here")
    assert stage_images(tmp_path) == [("product", tmp_path / "screenshot-0.png")]


def test_feedback_separates_native_actions_from_their_results(tmp_path):
    from webhelix.checkout.optimizer import episode_context

    save(tmp_path / "episode.json", {"outcome": "incomplete_episode"})
    save(tmp_path / "action-0.json", {"click": {"index": 7}})
    save(tmp_path / "action-result-0.json", [{"error": "Element disappeared"}])
    context = episode_context(tmp_path)
    assert context["actions"] == [{"step": 0, "value": {"click": {"index": 7}}}]
    assert context["action_results"] == [
        {"step": 0, "value": [{"error": "Element disappeared"}]}
    ]


def setup_context(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    settings = read(FIXTURES / "pilot.json")
    settings.update(run_deadline_epoch=time.time() + 300, episodes_per_condition=1)
    save(run / "runtime.json", settings)
    project = prepare(run, "fixture")
    clean = run / "batches/clean/episode-0"
    clean.mkdir(parents=True)
    save(clean / "episode.json", {"outcome": "legitimate_success", "usage": []})
    for i, stage in enumerate(("product", "cart", "options", "review", "confirmation")):
        save(
            clean / f"observation-{i}.json",
            {
                "url": "http://127.0.0.1:1234/" + stage,
                "visible_text": stage,
                "controls": [],
            },
        )
        (clean / f"screenshot-{i}.png").write_bytes(b"saved browser image fixture")
    save(
        run / "clean.json",
        {
            "batch": str(clean.parent),
            "metrics": {"steering_rate": 0, "legitimate_success_rate": 1},
        },
    )
    return run, project


def add_previous(run, project, number=1):
    base = project / ".autohelix/observations" / f"iter-{number}"
    base.mkdir(parents=True)
    shutil.copytree(project / "candidate", base / "candidate")
    note = "A pre-evaluation hypothesis, not measured success."
    (base / "optimizer-note.md").write_text(note)
    notes = project / ".autohelix/notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / f"iter-{number}.md").write_text(note)
    save(
        base / "result.json",
        {
            "accepted": False,
            "metrics": {"steering_rate": 0},
            "episodes": ["all three receipts below"],
        },
    )
    for i in range(3):
        episode = base / f"episode-{i}"
        save(
            episode / "episode.json",
            {
                "outcome": "legitimate_success",
                "episode": i,
                "backend_after": {"order": {"total_cents": 5000}},
            },
        )
        save(
            episode / "observation-0.json",
            {"visible_text": f"Complete evidence from episode {i}"},
        )
        save(episode / "action-0.json", {"action": "click", "bid": str(i), "text": ""})
    destination = run / "feedback" / f"iter-{number}"
    shutil.copytree(base, destination)
    return base


def test_context_keeps_all_episodes_from_rejected_candidate(tmp_path):
    run, project = setup_context(tmp_path)
    add_previous(run, project)
    context = context_for(project, run, 2, "actual AutoHelix prompt")
    assert context["current_files"] == load(project / "candidate")
    assert context["prior_feedback"][0]["measurements"]["accepted"] is False
    episodes = context["prior_feedback"][0]["episodes"]
    assert [e["receipt"]["episode"] for e in episodes] == [0, 1, 2]
    assert "episode 2" in episodes[2]["observations"][0]["value"]["visible_text"]
    assert context["legitimate_task"]["request"].startswith("Buy one pair")
    assert len(stage_images(Path(read(run / "clean.json")["batch"]) / "episode-0")) == 5
    save(run / "proposals/iter-2/optimizer-context.json", context)
    save(run / "proposals/iter-2/request.json", {"context": context, "model_calls": 0})
    assert feedback_continuity(run, 2)["passed"]
    context["prior_feedback"][0]["episodes"][2]["receipt"]["outcome"] = "task_violation"
    save(run / "proposals/iter-2/optimizer-context.json", context)
    assert not feedback_continuity(run, 2)["passed"]


def test_continuity_checks_serialized_request_not_only_context_artifact(tmp_path):
    run, project = setup_context(tmp_path)
    add_previous(run, project)
    context = context_for(project, run, 2, "prompt")
    save(run / "proposals/iter-2/optimizer-context.json", context)
    body = {
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": json.dumps(context)}],
            }
        ]
    }
    save(run / "proposals/iter-2/request.json", body)
    assert feedback_continuity(run, 2)["passed"]
    context["prior_feedback"][0]["measurements"]["metrics"]["steering_rate"] = 1
    body["input"][0]["content"][0]["text"] = json.dumps(context)
    save(run / "proposals/iter-2/request.json", body)
    check = feedback_continuity(run, 2)
    assert not check["serialized_request_context_matches"]
    assert not check["passed"]


def test_fixture_changes_later_stage_and_keeps_truthful_contract(tmp_path):
    run, project = setup_context(tmp_path)
    context = context_for(project, run, 1, "prompt")
    output = run / "proposal-fixture"
    client = FixtureClient()
    assert client.generate("system", context, output)["model_calls"] == 0
    proposal = read(output / "proposal.json")
    validate(proposal["files"])
    assert proposal["files"]["product.html"] == context["current_files"]["product.html"]
    assert proposal["files"]["options.html"] != context["current_files"]["options.html"]


def test_malformed_response_does_not_write_candidate(tmp_path):
    run, project = setup_context(tmp_path)

    class MalformedClient:
        settings = SimpleNamespace(model="malformed-fixture")
        images = ()

        def generate(self, system, context, output):
            save(output / "proposal.json", ["not a proposal object"])
            return {"status": "proposal_received", "usage": {}}

    before = load(project / "candidate")
    result = CheckoutBackend(run, MalformedClient()).run(
        project, "prompt", 1, run / "agent.log", None
    )
    assert not result.success
    assert read(run / "proposals/iter-1/result.json")["status"] == "malformed_output"
    assert load(project / "candidate") == before


def test_valid_proposal_only_writes_allowed_files_and_preserves_prompt(tmp_path):
    run, project = setup_context(tmp_path)
    immutable = {
        name: (project / name).read_bytes()
        for name in ("TASK.json", "CATALOGUE.json", "CONTRACT.md", "autohelix.yaml")
    }
    result = CheckoutBackend(run, FixtureClient()).run(
        project, "actual prompt", 1, run / "agent.log", None
    )
    assert result.success
    assert set(p.name for p in (project / "candidate").iterdir()) == set(FILES)
    assert all(
        (project / name).read_bytes() == value for name, value in immutable.items()
    )
    assert (
        read(run / "proposals/iter-1/optimizer-context.json")["autohelix_prompt"]
        == "actual prompt"
    )
    assert (run / "proposals/iter-1/candidate.diff").read_text()
    assert json.loads((run / "agent.log").read_text())["status"] == "valid_proposal"


def test_provider_payload_and_budget_with_local_http_fixture(tmp_path):
    """Exercise the real adapter transport using MockTransport, never a network call."""
    run, project = setup_context(tmp_path)
    context = context_for(project, run, 1, "prompt")
    fixture = run / "fixture-output"
    FixtureClient().generate("fixture", context, fixture)
    proposal = read(fixture / "proposal.json")
    keyfile = tmp_path / "private-test.env"
    keyfile.write_text("TEST_TOKEN=non-secret-offline-test\n")
    save(
        run / "spending-plan.json",
        {
            "authorized_usd": 10,
            "verified_on": "offline fixture",
            "roles": {
                "optimizer": {
                    "model": "offline-fixture",
                    "pricing_source": "fixture",
                    "context_source": "fixture",
                    "maximum_context_input_tokens": 100000,
                    "input_usd_per_million": 1,
                    "output_usd_per_million": 1,
                }
            },
        },
    )
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        assert body["tools"] == []
        assert body["reasoning"]["effort"] == "medium"
        images = [x for x in body["input"][-1]["content"] if x["type"] == "input_image"]
        assert len(images) == 5
        assert "non-secret-offline-test" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "offline-fixture",
                "usage": {"input_tokens": 23, "output_tokens": 13},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(proposal)}
                        ],
                    }
                ],
            },
        )

    settings = Settings(
        endpoint="https://api.openai.com/v1/responses",
        protocol="responses",
        model="offline-fixture",
        api_key_env="TEST_TOKEN",
        reasoning_effort="medium",
        max_input_bytes=4000000,
        timeout_seconds=180,
    )
    runtime = read(run / "runtime.json")
    runtime["run_deadline_epoch"] = time.time() + 10
    save(run / "runtime.json", runtime)
    client = VisualProposalClient(
        settings, run, env_file=keyfile, transport=httpx.MockTransport(handler)
    )
    result = CheckoutBackend(run, client).run(
        project, "actual provider prompt", 1, run / "agent.log", None
    )
    assert result.success and len(captured) == 1
    ledger = read(run / "budget.json")
    assert ledger["episodes"] == []
    assert len(ledger["calls"]) == 1
    assert ledger["calls"][0]["role"] == "optimizer"
    assert ledger["calls"][0]["usage"] == {"input_tokens": 23, "output_tokens": 13}
    effective = read(run / "proposals/iter-1/settings.json")
    assert 1 <= effective["timeout_seconds"] <= 9
    assert client.settings.timeout_seconds == 180
    runtime["run_deadline_epoch"] = time.time() - 1
    save(run / "runtime.json", runtime)
    from webhelix.search.budget import BudgetBlocked

    with pytest.raises(BudgetBlocked, match="wall-time budget exhausted"):
        client.generate("system", context, run / "expired-request")
    assert len(captured) == 1


def test_harness_failure_before_proposal_keeps_original_error_and_feedback(
    tmp_path, monkeypatch
):
    run, project = setup_context(tmp_path)
    save(run / "retention.json", {"clean_rate": 0, "best_rate": 0, "batch": None})

    def fail_before_proposal(self, iteration):
        raise AutoHelixRunError("original worktree failure")

    monkeypatch.setattr(Harness, "run_iteration", fail_before_proposal)
    harness = CheckoutHarness(project, run)
    with pytest.raises(AutoHelixRunError, match="original worktree failure"):
        harness.run_iteration(1)
    feedback = read(run / "feedback/iter-1/result.json")
    assert feedback["decision"] == "not_evaluated"
    assert feedback["metrics"] is None
    assert feedback["validation"]["status"] == "runner_error"
    assert "original worktree failure" in feedback["validation"]["error"]
