import json
import shutil

import httpx
import pytest

from webhelix.optimizer.backend import ProposalBackend, proposal_check
from webhelix.optimizer.budget import import_budget
from webhelix.optimizer.client import ProposalClient, Settings
from webhelix.v2.contract import FIXTURES, load_candidate, read


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "workspace"
    shutil.copytree(FIXTURES / "candidate", path / "candidate")
    shutil.copy2(FIXTURES / "task.json", path / "TASK.json")
    shutil.copy2(FIXTURES / "CONTRACT.md", path / "CONTRACT.md")
    (path / ".autohelix/notes").mkdir(parents=True)
    return path


def proposal(files):
    return {
        "files": {**files, "rooms.html": "<h2>Availability</h2>" + files["rooms.html"]},
        "hypothesis": "A visible heading control.",
        "predicted_observation": "An added heading.",
        "falsification": "Heading is absent.",
        "reflection": "No previous trials.",
    }


def response(value):
    return {
        "status": "completed",
        "model": "test-model",
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            }
        ],
    }


def settings(**kwargs):
    return Settings(
        **{
            "endpoint": "https://example.invalid/v1/responses",
            "protocol": "responses",
            "model": "test-model",
            **kwargs,
        }
    )


@pytest.mark.parametrize(
    "kind", ["path", "extra", "missing", "script", "oversize", "blank", "type"]
)
def test_invalid_proposals_do_not_write(workspace, tmp_path, monkeypatch, kind):
    monkeypatch.setenv("WEBHELIX_OPTIMIZER_API_KEY", "synthetic-key")
    original = load_candidate(workspace / "candidate")
    p = proposal(original)
    if kind == "path":
        p["files"]["../../evaluator.py"] = "arbitrary code"
    if kind == "extra":
        p["command"] = "anything"
    if kind == "missing":
        del p["files"]["style.css"]
    if kind == "script":
        p["files"]["rooms.html"] += "<script>run()</script>"
    if kind == "oversize":
        p["files"]["style.css"] = "é" * 13000
    if kind == "blank":
        p["hypothesis"] = " "
    if kind == "type":
        p["files"]["rooms.html"] = 123
    run = tmp_path / "run"
    backend = ProposalBackend(
        ProposalClient(
            settings(),
            run,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=response(p))
            ),
        ),
        run,
    )
    result = backend.run(
        workspace, "truthful test", 1, run / "agent.log", lambda e: None
    )
    assert not result.success
    assert (
        read(run / "proposals/iter-1/adapter/result.json")["status"]
        == "invalid_proposal"
    )
    assert load_candidate(workspace / "candidate") == original
    assert not list((workspace / ".autohelix/notes").iterdir())


def test_noop_is_not_a_candidate(workspace):
    files = load_candidate(workspace / "candidate")
    p = proposal(files)
    p["files"] = files
    assert proposal_check(p, files)["status"] == "no_change"


def test_symlink_is_rejected_before_call(workspace, tmp_path):
    original = workspace / "candidate/style.css"
    protected = tmp_path / "protected.css"
    original.rename(protected)
    original.symlink_to(protected)
    original_bytes = protected.read_bytes()
    backend = ProposalBackend(
        ProposalClient(
            settings(),
            tmp_path / "run",
            transport=httpx.MockTransport(
                lambda req: pytest.fail("Unsafe input reached model")
            ),
        ),
        tmp_path / "run",
    )
    result = backend.run(workspace, "test", 1, tmp_path / "agent.log", lambda e: None)
    assert not result.success
    assert protected.read_bytes() == original_bytes


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_protocol_exact_requests_usage_and_no_credentials(
    tmp_path, monkeypatch, protocol
):
    monkeypatch.setenv("WEBHELIX_OPTIMIZER_API_KEY", "synthetic-secret")
    bodies = []

    def handle(req):
        bodies.append(json.loads(req.content))
        if protocol == "responses":
            body = response({"example": True})
        else:
            body = {
                "model": "returned-alias",
                "usage": {"prompt_tokens": 10},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"example":true}'},
                    }
                ],
            }
        return httpx.Response(200, json=body)

    client = ProposalClient(
        settings(protocol=protocol, reasoning_effort="medium"),
        tmp_path,
        transport=httpx.MockTransport(handle),
    )
    result = client.generate("system", {"test": True}, tmp_path / "call")
    body = read(tmp_path / "call/request.json")
    assert body == bodies[0]
    assert body["model"] == "test-model" and body["tools"] == []
    assert (body.get("reasoning") or body.get("reasoning_effort")) in (
        {"effort": "medium"},
        "medium",
    )
    assert result["status"] == "proposal_received" and result["usage"]
    assert "synthetic-secret" not in "".join(
        p.read_text() for p in tmp_path.rglob("*") if p.is_file()
    )


@pytest.mark.parametrize(
    "case,expected",
    [
        ("service", "service_error"),
        ("refusal", "model_refusal"),
        ("truncated", "incomplete_response"),
        ("malformed", "malformed_output"),
        ("timeout", "timeout"),
        ("network", "transport_error"),
        ("tools", "malformed_output"),
        ("big", "local_error"),
        ("echo", "service_error"),
    ],
)
def test_failures_preserved_without_retry(tmp_path, monkeypatch, case, expected):
    monkeypatch.setenv("WEBHELIX_OPTIMIZER_API_KEY", "synthetic-secret")
    calls = []

    def handle(req):
        calls.append(req)
        if case == "timeout":
            raise httpx.ReadTimeout("wait")
        if case == "network":
            raise httpx.ConnectError("disconnected")
        if case == "service":
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": "cyber_policy_violation",
                        "message": "Service block",
                    }
                },
            )
        if case == "echo":
            return httpx.Response(401, json={"error": {"message": "synthetic-secret"}})
        if case == "big":
            return httpx.Response(200, content=b"a" * 160001)
        if case == "malformed":
            return httpx.Response(200, text="I cannot do that.")
        if case == "refusal":
            body = {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "Declined"}],
                    }
                ],
            }
        elif case == "truncated":
            body = {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            }
        else:
            body = {
                "status": "completed",
                "output": [{"type": "function_call", "name": "shell"}],
            }
        return httpx.Response(200, json=body)

    client = ProposalClient(settings(), tmp_path, transport=httpx.MockTransport(handle))
    result = client.generate("test", {}, tmp_path / "call")
    assert result["status"] == expected
    assert len(calls) == 1
    assert read(tmp_path / "call/transport-result.json") == result
    assert not (tmp_path / "call/proposal.json").exists()
    if case == "service":
        assert result["provider_error"]["code"] == "cyber_policy_violation"
    if case == "echo":
        assert "synthetic-secret" not in (tmp_path / "call/response.txt").read_text()


def test_call_budget_and_context_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBHELIX_OPTIMIZER_API_KEY", "synthetic-key")
    calls = []
    client = ProposalClient(
        settings(),
        tmp_path,
        transport=httpx.MockTransport(
            lambda req: (
                calls.append(req)
                or httpx.Response(429, json={"error": {"code": "quota"}})
            )
        ),
    )
    for number in range(3):
        result = client.generate("test", {}, tmp_path / str(number))
    assert result["status"] == "budget_exhausted" and len(calls) == 2
    client = ProposalClient(
        settings(max_input_bytes=10),
        tmp_path / "short",
        transport=httpx.MockTransport(
            lambda req: pytest.fail("Oversized context was sent")
        ),
    )
    assert client.generate("too long", {}, tmp_path / "long")["status"] == "local_error"


def test_shared_victim_budget_never_restarts(tmp_path, monkeypatch):
    from webhelix.v2.evaluate import reserve

    shared = tmp_path / "shared"
    source = FIXTURES.parents[1] / "examples/reservation/real/budget.json"
    import_budget(source, shared)
    assert len(read(shared / "budget.json")["reservations"]) == 6
    with pytest.raises(ValueError, match="already exists"):
        import_budget(source, shared)
    monkeypatch.setenv("WEBHELIX_V2_BUDGET_DIR", str(shared))
    assert reserve(tmp_path / "new-run", "continuation") == 7
    assert not (tmp_path / "new-run/budget.json").exists()
    monkeypatch.setenv("WEBHELIX_V2_BUDGET_DIR", str(tmp_path / "absent"))
    with pytest.raises(FileNotFoundError):
        reserve(tmp_path / "new-run", "cannot reset")


def test_unknown_endpoint_options_and_model_fail_fast():
    for change in (
        {"endpoint": "https://key:secret@host/v1"},
        {"endpoint": "http://remote/v1"},
        {"max_calls": 3},
        {"model": "REPLACE_ME"},
        {"protocol": "auto"},
    ):
        with pytest.raises(ValueError):
            settings(**change)


def test_real_harness_adapter_feedback_and_rejection(tmp_path, monkeypatch):
    from webhelix.optimizer.cli import main

    monkeypatch.delenv("WEBHELIX_V2_BUDGET_DIR", raising=False)
    run = tmp_path / "adapter-demo"
    main(["demo", "--run-dir", str(run)])
    status = read(run / "status.json")
    assert status["optimizer_backend"] == "bounded_proposal_adapter"
    assert status["protected_files_unchanged"]
    assert status["command"][:3] == ["webhelix", "optimizer", "demo"]
    assert status["completed_iterations"] == 2 and status["reserved_episodes"] == 0
    history = [
        json.loads(line)
        for line in (run / "autohelix-history.jsonl").read_text().splitlines()
    ]
    assert [r["accepted"] for r in history] == [True, False, False]
    assert load_candidate(run / "project/candidate") == load_candidate(
        FIXTURES / "candidate"
    )
    second = read(run / "proposals/iter-2/adapter/request.json")
    context = json.loads(second["input"][1]["content"])
    prior = context["prior_feedback"][0]
    assert prior["evaluator_feedback"]["metrics"]["violation_rate"] == 0
    assert "Scripted hypothesis 1" in prior["optimizer_note"]
    assert "Availability overview 1" in prior["candidate_files"]["rooms.html"]
    assert "Availability overview 1" not in context["current_files"]["rooms.html"]
    assert prior["observations"] and prior["actions"]
    assert len(read(run / "optimizer-budget.json")["calls"]) == 2
    assert not list((run / "project/.autohelix/worktrees").glob("iter-*"))


def test_harmless_smoke_excludes_research_objective(tmp_path, monkeypatch):
    from webhelix.optimizer.cli import smoke

    monkeypatch.setenv("WEBHELIX_OPTIMIZER_API_KEY", "synthetic-key")

    def handle(req):
        context = json.loads(json.loads(req.content)["input"][1]["content"])
        assert "contract" not in context and "autohelix_prompt" not in context
        return httpx.Response(200, json=response(proposal(context["current_files"])))

    result = smoke(
        settings(), tmp_path / "smoke", transport=httpx.MockTransport(handle)
    )
    assert result["passed"] and result["victim_episodes"] == 0


@pytest.mark.parametrize("failure", ["invalid_proposal", "service_error"])
def test_real_harness_stops_before_evaluating_failed_proposal(
    tmp_path, monkeypatch, failure
):
    import hashlib
    from autohelix.harness import AutoHelixRunError
    from webhelix.v2.contract import fingerprint, save
    from webhelix.v2.experiment import ResearchHarness, prepare

    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setenv("WEBHELIX_OPTIMIZER_API_KEY", "synthetic-key")
    monkeypatch.setenv("WEBHELIX_V2_RUN", str(run))
    monkeypatch.setenv("WEBHELIX_V2_POLICY", "scripted")
    original = load_candidate(FIXTURES / "candidate")
    invalid = proposal(original)
    invalid["files"]["rooms.html"] += "<script>invalid()</script>"
    contexts = []

    def handle(req):
        context = json.loads(json.loads(req.content)["input"][1]["content"])
        contexts.append(context)
        if len(contexts) == 2:
            prior = context["prior_feedback"][0]
            assert prior["evaluator_feedback"]["metrics"] is None
            assert prior["evaluator_feedback"]["victim_episodes"] == 0
            assert prior["raw_proposal"] == invalid
            assert invalid["hypothesis"] in prior["optimizer_note"]
        return (
            httpx.Response(403, json={"error": {"code": "synthetic-block"}})
            if failure == "service_error"
            else httpx.Response(200, json=response(invalid))
        )

    client = ProposalClient(
        settings(),
        run,
        transport=httpx.MockTransport(handle),
    )
    backend = ProposalBackend(client, run)
    project = prepare(run, scripted=True, optimizer=backend)
    # Synthetic baseline cache solely for this stop-path test; no observed episode is claimed.
    save(
        run / "cache" / f"{hashlib.sha256(str(project).encode()).hexdigest()}.json",
        {
            "candidate_hash": fingerprint(original),
            "batch": "synthetic-test-baseline",
            "metrics": {
                "evaluation_ok": True,
                "violation_rate": 0,
                "legitimate_success_rate": 1,
            },
        },
    )
    harness = ResearchHarness(project, run)
    harness.agent = backend
    if failure == "service_error":
        with pytest.raises(AutoHelixRunError, match=failure):
            harness.run(max_iterations=2)
        assert len(contexts) == 1
        assert [r.iteration for r in harness.history.load()] == [0]
    else:
        harness.run(max_iterations=2)
        assert len(contexts) == 2
        assert [r.iteration for r in harness.history.load()] == [0, 1, 2]
        assert [r.metrics for r in harness.history.load()][1:] == [{}, {}]
    assert not (run / "batches").exists()
    assert not (run / "budget.json").exists()
    assert load_candidate(project / "candidate") == original
    result = read(run / "proposals/iter-1/adapter/result.json")
    assert result["status"] == failure
    assert not read(run / "proposals/iter-1/agent-result.json")["success"]


def test_replay_does_not_call_provider_and_preserves_exact_proposal(
    workspace, tmp_path
):
    from webhelix.v2.contract import ROOT

    source = (
        ROOT
        / "examples/optimizer-adapter/real-research-test-20261019/optimizer-proposal.json"
    )
    run = tmp_path / "replay"
    backend = ProposalBackend(
        ProposalClient(
            settings(),
            run,
            transport=httpx.MockTransport(
                lambda request: pytest.fail("Replay invoked a model")
            ),
            call_limit=1,
        ),
        run,
        replay=source,
    )
    result = backend.run(workspace, "replay test", 1, run / "agent.log", lambda e: None)
    assert result.success and not result.usage
    assert load_candidate(workspace / "candidate") == read(source)["files"]
    assert (
        run / "proposals/iter-1/adapter/proposal.json"
    ).read_bytes() == source.read_bytes()
    assert not (run / "optimizer-budget.json").exists()
    assert (
        read(source)["hypothesis"]
        in (workspace / ".autohelix/notes/iter-1.md").read_text()
    )


def test_continuation_call_limit_is_one(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBHELIX_OPTIMIZER_API_KEY", "synthetic-key")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=response({"fixture": True}))

    client = ProposalClient(
        settings(), tmp_path, transport=httpx.MockTransport(handle), call_limit=1
    )
    assert client.generate("test", {}, tmp_path / "one")["request_started"]
    assert client.generate("test", {}, tmp_path / "two")["status"] == "budget_exhausted"
    assert len(calls) == 1
