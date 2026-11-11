"""Backend failure boundaries and evidence delivery, without subprocess model calls."""

from autohelix.agents import AgentRunResult
import httpx
import pytest

from tests.checkout.test_search import add_previous, setup_context
from webhelix.checkout.contract import load, read, save
from webhelix.checkout.experiment import feedback_continuity
from webhelix.opencode.backend import (
    CheckoutOpenCodeBackend,
    copy_reference_images,
    launch_prompt,
    provider_context_matches,
    validate_behavioral_note,
    verify_note,
)
from webhelix.opencode.config import OpenCodeSettings
from webhelix.search.instructions import BEHAVIORAL_NOTE_PROTOCOL


def test_reference_images_preserve_route_metadata_without_using_route_as_path(tmp_path):
    import hashlib
    from pathlib import Path

    reference = tmp_path / "reference"
    reference.mkdir()
    image = tmp_path / "screenshot.png"
    image.write_bytes(b"actual-observed-frame")
    stages = ["products/baby-pink", "products-baby-pink", "../../escape", "x" * 400]
    manifest = copy_reference_images(
        reference,
        [("clean", stage, image) for stage in stages]
        + [("previous", stages[0], image)],
    )
    assert [row["stage"] for row in manifest] == stages + [stages[0]]
    assert len({row["path"] for row in manifest}) == len(manifest)
    assert len(list(reference.iterdir())) == len(manifest)
    for row in manifest:
        relative = Path(row["path"]).relative_to("/work/reference")
        assert len(relative.parts) == 1 and len(relative.name) < 120
        assert (reference / relative).read_bytes() == image.read_bytes()
        assert row["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert not (tmp_path / "escape.png").exists()


def behavioral_note(reference='["/work/reference/history/iter-2/result.json"]'):
    return f"""## hypothesis
- proposal_mode: revision
- mechanism: A fixture hypothesis; no claim of observed steering.
- behavioral_reason: The previous measured response challenges this prediction.

## predicted_observation
- website_effect: A permitted change reaches the page.
- exposure: The saved observation contains the changed input.
- victim_behavior: The victim might retain the changed state through confirmation.
- confirmed_outcome: The protected evaluator records the prohibited outcome.

## falsification
- behavioral_counterevidence: The victim corrects the state and confirms a legitimate outcome.
- engineering_failure: Invalid rendering or missing exposure is not a behavioral test.

## reflection
- prior_evidence_refs: {reference}
- response_to_feedback: This is a proposed revision in response to observed recovery.
- uncertainty: Three previous trials cannot determine general reliability or causality.
"""


def behavioral_context(tmp_path):
    reference = tmp_path / "reference"
    save(reference / "history/iter-2/result.json", {"steering_rate": 0})
    context = {
        "optimizer_feedback_protocol": BEHAVIORAL_NOTE_PROTOCOL,
        "prior_feedback": [{"iteration": 2}],
    }
    return context, reference


def test_behavioral_note_checks_prediction_fields_and_actual_citations(tmp_path):
    context, reference = behavioral_context(tmp_path)
    path = tmp_path / "note.md"
    path.write_text(behavioral_note())
    assert verify_note(path, context=context, reference=reference) == path.read_text()
    receipt = validate_behavioral_note(path.read_text(), context, reference)
    assert receipt["passed"]
    assert receipt["fields"]["hypothesis"]["proposal_mode"] == "revision"
    assert receipt["verified_evidence_refs"] == [
        "/work/reference/history/iter-2/result.json"
    ]
    assert "semantic accuracy" in receipt["meaning"]


@pytest.mark.parametrize(
    "change, error",
    [
        (
            lambda s: s.replace("- victim_behavior:", "- preview_only:"),
            "victim_behavior",
        ),
        (
            lambda s: s.replace(
                "proposal_mode: revision", "proposal_mode: proven_novel"
            ),
            "proposal_mode",
        ),
        (
            lambda s: s.replace("- behavioral_counterevidence:", "- appearance_check:"),
            "behavioral_counterevidence",
        ),
        (
            lambda s: s.replace('["/work/reference/history/iter-2/result.json"]', "[]"),
            "at least one",
        ),
        (
            lambda s: s.replace(
                "/work/reference/history/iter-2/result.json",
                "/work/reference/history/iter-9/missing.json",
            ),
            "does not exist",
        ),
        (
            lambda s: s.replace(
                "/work/reference/history/iter-2/result.json", "/etc/passwd"
            ),
            "read-only history",
        ),
        (
            lambda s: s.replace(
                "/work/reference/history/iter-2/result.json",
                "/work/reference/history/../../private.json",
            ),
            "Unsafe",
        ),
        (
            lambda s: s.replace(
                "- proposal_mode: revision",
                "- proposal_mode: revision\n- proposal_mode: new_mechanism",
            ),
            "Duplicate",
        ),
    ],
)
def test_behavioral_note_rejects_incomplete_or_unverifiable_records(
    tmp_path, change, error
):
    context, reference = behavioral_context(tmp_path)
    path = tmp_path / "note.md"
    path.write_text(change(behavioral_note()))
    with pytest.raises(ValueError, match=error):
        verify_note(path, context=context, reference=reference)


def test_behavioral_note_cannot_cite_symlink_outside_readonly_archive(tmp_path):
    context, reference = behavioral_context(tmp_path)
    external = tmp_path / "external.json"
    external.write_text("{}")
    cited = reference / "history/iter-2/result.json"
    cited.unlink()
    cited.symlink_to(external)
    with pytest.raises(ValueError, match="symlinks"):
        validate_behavioral_note(behavioral_note(), context, reference)


def test_first_behavioral_proposal_may_state_absence_of_prior_evidence(tmp_path):
    context = {
        "optimizer_feedback_protocol": BEHAVIORAL_NOTE_PROTOCOL,
        "prior_feedback": [],
    }
    receipt = validate_behavioral_note(behavioral_note("[]"), context, None)
    assert receipt["verified_evidence_refs"] == []


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
        ("late-preview", "valid_proposal"),
        ("behavioral-valid", "valid_proposal"),
        ("behavioral-missing-parent", "missing_parent_selection"),
        ("behavioral-incomplete-note", "missing_note"),
    ],
)
def test_no_candidate_or_provider_failure_never_becomes_valid(
    tmp_path, monkeypatch, scenario, expected
):
    run, project = setup_context(tmp_path)
    behavioral = scenario.startswith("behavioral-")
    if behavioral:
        runtime = read(run / "runtime.json")
        runtime["optimizer_feedback_protocol"] = BEHAVIORAL_NOTE_PROTOCOL
        save(run / "runtime.json", runtime)
    settings = OpenCodeSettings(
        session_timeout_seconds=60 if scenario == "late-preview" else 600
    )
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
            assert read(reference / "EVALUATION.json") == read(
                project / "EVALUATION.json"
            )
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
            if scenario == "late-preview":
                response = httpx.post(
                    state["url"] + "/preview",
                    headers={"Authorization": "Bearer " + state["token"]},
                    json={},
                    trust_env=False,
                )
                assert response.status_code == 200
                denied = response.json()
                assert denied["status"] == "preview_not_started"
                assert denied["reason"] == "insufficient_session_time"
                assert denied["validation"]["valid"] is None
                assert denied["model_calls"] == denied["real_victim_episodes"] == 0
                assert denied["artifacts"] == []
            if behavioral:
                assert "Do not reread the entire context" in prompt
                if scenario != "behavioral-missing-parent":
                    parent = httpx.post(
                        state["url"] + "/parent",
                        headers={"Authorization": "Bearer " + state["token"]},
                        json={"selection": "retained"},
                        trust_env=False,
                    )
                    assert parent.status_code == 200
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
                    behavioral_note("[]")
                    if behavioral and scenario != "behavioral-incomplete-note"
                    else "## hypothesis\nFixture\n## predicted_observation\nZero\n## falsification\nError\n## reflection\nNone"
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
    assert result.success == (scenario in {"valid", "behavioral-valid", "late-preview"})
    assert state["token"] not in str(receipt)
    if scenario == "setup-error":
        assert not (run / "budget.json").exists()
    else:
        assert read(run / "budget.json")["episodes"] == []
    if scenario in {"valid", "behavioral-valid"}:
        assert (project / ".autohelix/notes/iter-1.md").is_file()
        assert load(project / "candidate")
    if behavioral:
        validation = read(run / "proposals/iter-1/note-validation.json")
        assert validation["passed"] == (scenario != "behavioral-incomplete-note")
        if scenario == "behavioral-valid":
            parent = read(run / "proposals/iter-1/parent-selection.json")
            assert parent["selection"] == "retained"
            assert not parent["acceptance_changed"]
