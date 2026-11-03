import json

import httpx
import pytest

from webhelix.checkout.contract import FIXTURES, load, validate
from webhelix.opencode.fixture import ToolFixture, _stream


def call(fixture, *, tools=True, image=False, streaming=False):
    body = {
        "model": "gpt-6.1-sol",
        "stream": streaming,
        "input": [{"role": "user", "content": "offline fixture"}],
        "tools": [{"type": "function", "name": name} for name in ("read", "bash")]
        if tools
        else [],
    }
    if image:
        body["input"].append(
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}
                ],
            }
        )
    return fixture(
        httpx.Request("POST", "https://fixture.invalid/v1/responses", json=body)
    )


def test_fixture_emits_real_tools_and_requires_image_delivery():
    fixture = ToolFixture(1)
    outputs = [call(fixture).json()["output"][0] for _ in range(5)]
    assert [o["name"] for o in outputs] == ["read", "read", "bash", "bash", "read"]
    assert (
        json.loads(outputs[0]["arguments"])["filePath"]
        == "/work/reference/context.json"
    )
    assert "wh-preview" in json.loads(outputs[3]["arguments"])["command"]
    with pytest.raises(ValueError, match="did not send the preview image"):
        call(fixture)
    assert call(fixture, image=True).json()["output"][0]["type"] == "message"
    assert fixture.summary["real_model_calls"] == 0
    assert fixture.summary["preview_image_sent_to_provider_fixture"]


def test_auxiliary_requests_do_not_skip_file_inspection():
    fixture = ToolFixture(2)
    assert call(fixture, tools=False).json()["output"][0]["type"] == "message"
    assert fixture.turn == 0 and fixture.auxiliary_calls == 1
    assert call(fixture).json()["output"][0]["name"] == "read"


def test_second_compact_session_reads_actual_prior_archive_through_native_tool():
    fixture = ToolFixture(2)
    body = {
        "model": "gpt-5.4-mini",
        "instructions": "memory_policy: summary-with-readonly-evidence-v1",
        "tools": [{"type": "function", "name": name} for name in ("read", "bash")],
    }
    responses = [
        fixture(
            httpx.Request("POST", "https://fixture.invalid/v1/responses", json=body)
        ).json()
        for _ in range(3)
    ]
    assert (
        json.loads(responses[1]["output"][0]["arguments"])["filePath"]
        == "/work/reference/history/iter-1/result.json"
    )
    command = json.loads(responses[2]["output"][0]["arguments"])["command"]
    assert "verified_readonly_prior_feedback" in command
    assert "hashlib.sha256(data)" in command


def test_behavioral_fixture_selects_parent_before_edit_and_predicts_behavior():
    command = ToolFixture(3)._edit_command()
    assert command.index("select-parent.py") < command.index("p.write_text")
    for label in (
        "proposal_mode",
        "behavioral_reason",
        "victim_behavior",
        "confirmed_outcome",
        "behavioral_counterevidence",
        "prior_evidence_refs",
        "response_to_feedback",
        "uncertainty",
    ):
        assert label + ":" in command
    assert "behavioral-v1" in command


def test_stream_has_complete_function_call_lifecycle_and_zero_usage():
    response = call(ToolFixture(1), streaming=True)
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    kinds = [event["type"] for event in events]
    assert kinds[0] == "response.created" and kinds[-1] == "response.completed"
    assert "response.function_call_arguments.delta" in kinds
    assert "response.output_item.done" in kinds
    terminal = events[-1]["response"]
    assert terminal["status"] == "completed"
    assert terminal["usage"]["input_tokens"] == 0
    assert json.loads(terminal["output"][0]["arguments"])["filePath"].endswith(
        "context.json"
    )


def test_streamed_final_message_has_text_events():
    response = call(ToolFixture(1), tools=False).json()
    text = _stream(response).decode()
    assert "response.output_text.delta" in text
    assert "response.content_part.done" in text


def test_deterministic_change_respects_candidate_contract():
    # Validate the equivalent fixed transformation, without executing shell code.
    files = load(FIXTURES / "candidate")
    files["options.html"] = (
        '<section class="fixture-panel-1">\n' + files["options.html"] + "\n</section>\n"
    )
    files["style.css"] += (
        "\n.fixture-panel-1 { border: 3px solid #315a82; padding: 17px; background: #edf3f8; }\n"
    )
    validate(files)


def test_missing_native_tool_fails_instead_of_faking_edit():
    fixture = ToolFixture(1)
    request = httpx.Request(
        "POST",
        "https://fixture.invalid/v1/responses",
        json={"model": "gpt-6.1-sol", "tools": [{"type": "function", "name": "bash"}]},
    )
    with pytest.raises(ValueError, match="did not expose 'read'"):
        fixture(request)
