"""Backend failure boundaries and evidence delivery, without subprocess model calls."""

from autohelix.agents import AgentRunResult
import httpx
import pytest

from tests.checkout.test_search import add_previous, setup_context
from webhelix.checkout.contract import load, read, save
from webhelix.checkout.experiment import feedback_continuity
from webhelix.opencode.backend import (
    CheckoutOpenCodeBackend,
    launch_prompt,
    provider_context_matches,
    verify_note,
)
from webhelix.opencode.config import OpenCodeSettings


def test_context_delivery_is_verified_against_actual_provider_request(tmp_path):
    run, project = setup_context(tmp_path)
    add_previous(run, project)
    from webhelix.checkout.optimizer import context_for

    context = context_for(project, run, 2, "prompt")
    output = run / "proposals/iter-2"
    save(output / "optimizer-context.json", context)
    assert not provider_context_matches(output, context)
    save(
        output / "gateway/calls/request-1/request.json",
        {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": launch_prompt(context)}],
                }
            ]
        },
    )
    assert provider_context_matches(output, context)
    runtime = read(run / "runtime.json")
    runtime["optimizer_backend"] = "opencode"
    save(run / "runtime.json", runtime)
    assert feedback_continuity(run, 2)["passed"]
    context["prior_feedback"][0]["measurements"]["metrics"]["steering_rate"] = 1
    save(output / "optimizer-context.json", context)
    assert not feedback_continuity(run, 2)["passed"]


def test_note_must_exist_and_contain_actual_sections(tmp_path):
    note = tmp_path / "optimizer-note.md"
    with pytest.raises(ValueError):
        verify_note(note)
    note.write_text("I finished the experiment.")
    with pytest.raises(ValueError):
        verify_note(note)
    note.write_text(
        "## hypothesis\nIdea\n## predicted_observation\nPrediction\n## falsification\nFailure\n## reflection\nPrior result"
    )
    assert "Prediction" in verify_note(note)


@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("valid", "valid_proposal"),
        ("no-change", "no_change"),
        ("missing-note", "missing_note"),
        ("invalid", "invalid_proposal"),
        ("refusal", "refusal"),
        ("service-error", "service_error"),
        ("setup-error", "local_error"),
    ],
)
def test_no_candidate_or_provider_failure_never_becomes_valid(
    tmp_path, monkeypatch, scenario, expected
):
    run, project = setup_context(tmp_path)
    settings = OpenCodeSettings()
    save(
        run / "spending-plan.json",
        {
            "offline_fixture": True,
            "authorized_usd": 10,
            "verified_on": "fixture",
            "roles": {
                "optimizer": {
                    "model": settings.model,
                    "input_usd_per_million": 1,
                    "output_usd_per_million": 1,
                    "maximum_context_input_tokens": 10000,
                    "pricing_source": "fixture",
                    "context_source": "fixture",
                }
            },
        },
    )
    state = {}

    class Sandbox:
        def __init__(self, workspace, reference, output, **kwargs):
            self.gateway_host = "127.0.0.1"
            self.command_path = "/bin/false"
            self.note_path = output / "note.md"
            self.note_path.parent.mkdir(parents=True)
            state["sandbox"] = self

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def start(self, gateway_url, token, agent_prompt=None):
            state.update(url=gateway_url, token=token, agent_prompt=agent_prompt)
            if scenario == "setup-error":
                raise RuntimeError(
                    "Offline launch failure mentioning scoped token " + token
                )

        def stop_container(self):
            pass

    class Fixture:
        def __init__(self, iteration):
            pass

        def __call__(self, request):
            if scenario == "service-error":
                return httpx.Response(503, json={"error": "offline service error"})
            response = {
                "model": settings.model,
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "output": [],
            }
            if scenario == "refusal":
                response["output"] = [
                    {
                        "type": "message",
                        "content": [
                            {"type": "refusal", "refusal": "Offline refusal fixture"}
                        ],
                    }
                ]
            return httpx.Response(200, json=response)

    class Native:
        def __init__(self, config):
            assert config.type == "opencode"
            assert config.reasoning_effort == "medium"

        def run(self, workspace, prompt, iteration, log_path, callback, **kwargs):
            response = httpx.post(
                state["url"] + "/v1/responses",
                headers={"Authorization": "Bearer " + state["token"]},
                json={
                    "model": settings.model,
                    "instructions": state["agent_prompt"],
                    "input": prompt,
                },
                trust_env=False,
            )
            assert response.status_code in (200, 503)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("offline native-agent fixture\n")
            if scenario != "no-change":
                css = workspace / "candidate/style.css"
                css.write_text(
                    css.read_text() + "\nmain { border: 2px solid #315a82; }\n"
                )
            if scenario == "invalid":
                (workspace / "candidate/options.html").write_text(
                    "<script>invalid()</script>"
                )
            if scenario != "missing-note":
                state["sandbox"].note_path.write_text(
                    "## hypothesis\nFixture\n## predicted_observation\nZero\n## falsification\nError\n## reflection\nNone"
                )
            return AgentRunResult(success=True, exit_code=0)

    monkeypatch.setattr("webhelix.opencode.runtime.DockerRuntime", Sandbox)
    monkeypatch.setattr("webhelix.opencode.fixture.ToolFixture", Fixture)
    monkeypatch.setattr("webhelix.opencode.backend.OpenCodeAgent", Native)
    result = CheckoutOpenCodeBackend(run, settings, scripted=True).run(
        project, "prompt", 1, run / "agent.log", None
    )
    receipt = read(run / "proposals/iter-1/result.json")
    assert receipt["status"] == expected
    assert result.success == (scenario == "valid")
    assert state["token"] not in str(receipt)
    if scenario == "setup-error":
        assert not (run / "budget.json").exists()
    else:
        assert read(run / "budget.json")["episodes"] == []
    if scenario == "valid":
        assert (project / ".autohelix/notes/iter-1.md").is_file()
        assert load(project / "candidate")
