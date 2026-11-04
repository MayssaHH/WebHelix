"""Protocol and accounting tests make no network/model calls."""

import asyncio
import json

from browser_use.llm.messages import (
    AssistantMessage,
    ContentPartImageParam,
    ContentPartTextParam,
    ImageURL,
    SystemMessage,
    UserMessage,
)
import httpx
from pydantic import BaseModel
import pytest

from webhelix.browser.model import ResponsesModel, ScriptedModel, model_from_environment
from webhelix.provider import InvalidAction, ProviderUnavailable


class Output(BaseModel):
    answer: str


class Budget:
    def __init__(self, blocked=False):
        self.events = []
        self.blocked = blocked

    def reserve(self, role, model, max_output):
        self.events.append(("reserve", role, model, max_output))
        if self.blocked:
            raise RuntimeError("Budget exhausted")
        return 17

    def settle(self, number, usage, started=True):
        self.events.append(("settle", number, usage, started))


SETTINGS = {
    "endpoint": "https://api.openai.com/v1/responses",
    "protocol": "responses",
    "model": "gpt-6.1-sol",
    "reasoning_effort": "medium",
    "api_key_env": "WEBHELIX_API_KEY",
    "max_output_tokens": 2048,
    "max_input_bytes": 500000,
    "timeout_seconds": 60,
}


def model(tmp_path, handler, budget=None, settings=None):
    credential = tmp_path / "private.env"
    credential.write_text("WEBHELIX_API_KEY=unit-test-placeholder\n")
    return ResponsesModel(
        settings or SETTINGS,
        tmp_path / "evidence",
        env_file=credential,
        budget=budget or Budget(),
        transport=httpx.MockTransport(handler),
    )


def response(answer='{"answer":"ok"}', **overrides):
    return {
        "model": "gpt-6.1-sol",
        "status": "completed",
        "usage": {
            "input_tokens": 101,
            "output_tokens": 17,
            "input_tokens_details": {"cached_tokens": 11},
        },
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": answer}]}
        ],
        **overrides,
    }


def invoke(llm, messages=None, output_format=Output):
    async def run():
        try:
            return await llm.ainvoke(
                messages or [UserMessage(content="Do the legitimate task")],
                output_format,
            )
        finally:
            await llm.aclose()

    return asyncio.run(run())


def test_native_messages_images_schema_and_usage_are_preserved(tmp_path):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        assert request.url == SETTINGS["endpoint"]
        return httpx.Response(200, json=response())

    messages = [
        SystemMessage(content="Browser-Use native system prompt"),
        UserMessage(content="Legitimate task only"),
        AssistantMessage(content='{"action":[{"click":{"index":7}}]}'),
        UserMessage(
            content=[
                ContentPartTextParam(text="Native DOM observation"),
                ContentPartImageParam(
                    image_url=ImageURL(url="data:image/png;base64,YWJj", detail="high")
                ),
            ]
        ),
    ]
    ledger = Budget()
    llm = model(tmp_path, handler, ledger)
    result = invoke(llm, messages)
    assert result.completion.answer == "ok"
    assert result.usage.prompt_tokens == 101
    assert result.usage.prompt_cached_tokens == 11
    assert result.usage.completion_tokens == 17
    body = captured[0]
    assert body["model"] == "gpt-6.1-sol"
    assert body["reasoning"] == {"effort": "medium"}
    assert body["input"][0] == {"role": "system", "content": messages[0].content}
    assert body["input"][2]["content"] == messages[2].content
    assert body["input"][3]["content"] == [
        {"type": "input_text", "text": "Native DOM observation"},
        {
            "type": "input_image",
            "image_url": "data:image/png;base64,YWJj",
            "detail": "high",
        },
    ]
    assert body["text"]["format"]["schema"]["required"] == ["answer"]
    assert body["text"]["format"]["schema"]["additionalProperties"] is False
    assert body["max_output_tokens"] == 2048
    assert body["tools"] == []
    assert body["store"] is False
    assert ledger.events[0] == ("reserve", "victim", "gpt-6.1-sol", 2048)
    assert ledger.events[1] == ("settle", 17, response()["usage"], True)
    saved = json.loads((tmp_path / "evidence/request-0.json").read_text())
    assert saved == body
    assert "unit-test-placeholder" not in "".join(
        path.read_text() for path in (tmp_path / "evidence").glob("*.json")
    )


@pytest.mark.parametrize("status", [401, 429, 500])
def test_service_failure_is_recorded_accounted_and_never_retried(tmp_path, status):
    calls = []
    ledger = Budget()

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={
                "error": {
                    "code": "insufficient_quota",
                    "message": "unit-test-placeholder",
                }
            },
        )

    llm = model(tmp_path, handler, ledger)

    async def run():
        try:
            for _ in range(2):
                with pytest.raises(ProviderUnavailable, match=f"HTTP {status}"):
                    await llm.ainvoke([UserMessage(content="Task")], Output)
        finally:
            await llm.aclose()

    asyncio.run(run())
    assert len(calls) == 1
    assert ledger.events[-1] == ("settle", 17, {}, True)
    assert (
        json.loads((tmp_path / "evidence/call-0.json").read_text())["status"]
        == "failed"
    )
    assert (
        "unit-test-placeholder"
        not in (tmp_path / "evidence/response-0.json").read_text()
    )


def test_budget_denial_prevents_http_request(tmp_path):
    def handler(request):
        pytest.fail("A request escaped the spending ledger")

    llm = model(tmp_path, handler, Budget(blocked=True))
    with pytest.raises(RuntimeError, match="Budget exhausted"):
        invoke(llm)
    assert json.loads((tmp_path / "evidence/call-0.json").read_text())["sent"] is False


def test_quota_diagnostics_reach_episode_error_without_exposing_credentials(tmp_path):
    ledger = Budget()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429,
            headers={"x-request-id": "req_quota-test", "retry-after": "60"},
            json={
                "error": {
                    "type": "insufficient_quota",
                    "code": "credit_balance_exhausted",
                    "message": "No credits remain for unit-test-placeholder. Add credits.",
                }
            },
        )

    llm = model(tmp_path, handler, ledger)

    async def run():
        try:
            for _ in range(2):
                with pytest.raises(
                    ProviderUnavailable, match=r"HTTP 429 \(credit_balance_exhausted\)"
                ) as caught:
                    await llm.ainvoke([UserMessage(content="Task")], Output)
                assert "unit-test-placeholder" not in str(caught.value)
        finally:
            await llm.aclose()

    asyncio.run(run())
    assert len(calls) == 1
    assert ledger.events[-1] == ("settle", 17, {}, True)
    record = json.loads((tmp_path / "evidence/response-0.json").read_text())
    assert (
        record["error"]["message"] == "No credits remain for [REDACTED]. Add credits."
    )
    assert record["headers"] == {"retry-after": "60", "x-request-id": "req_quota-test"}
    assert (
        "unit-test-placeholder"
        not in (tmp_path / "evidence/response-0.json").read_text()
    )


@pytest.mark.parametrize(
    "payload,message",
    [
        (response("invalid JSON"), "malformed Browser-Use"),
        (response(status="incomplete"), "incomplete"),
        (
            response(
                output=[
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "No"}],
                    }
                ]
            ),
            "declined",
        ),
    ],
)
def test_invalid_model_output_is_not_a_completed_action(tmp_path, payload, message):
    ledger = Budget()
    llm = model(tmp_path, lambda request: httpx.Response(200, json=payload), ledger)
    with pytest.raises(InvalidAction, match=message):
        invoke(llm)
    assert ledger.events[-1][2] == payload["usage"]
    assert llm.failure is not None


def test_missing_usage_retains_unknown_usage_for_conservative_accounting(tmp_path):
    ledger = Budget()
    llm = model(
        tmp_path, lambda request: httpx.Response(200, json=response(usage=None)), ledger
    )
    result = invoke(llm)
    assert result.usage is None
    assert ledger.events[-1] == ("settle", 17, {}, True)


def test_input_bound_stops_before_reserving_or_sending(tmp_path):
    ledger = Budget()
    llm = model(
        tmp_path,
        lambda request: pytest.fail("Unexpected HTTP"),
        ledger,
        {**SETTINGS, "max_input_bytes": 1},
    )
    with pytest.raises(ProviderUnavailable, match="payload bytes > 1 configured bytes"):
        invoke(
            llm, [UserMessage(content="private-page-text-must-not-be-in-diagnostic")]
        )
    assert ledger.events == []
    evidence = tmp_path / "evidence"
    diagnostic = json.loads((evidence / "input-bound-0.json").read_text())
    receipt = json.loads((evidence / "call-0.json").read_text())
    assert diagnostic["payload_bytes"] == receipt["payload_bytes"] > 1
    assert diagnostic["max_input_bytes"] == receipt["max_input_bytes"] == 1
    assert diagnostic["sent"] is False and diagnostic["truncated"] is False
    assert receipt["sent"] is False
    assert not (evidence / "request-0.json").exists()
    assert not (evidence / "agent-messages-0.json").exists()
    assert "private-page-text" not in (evidence / "input-bound-0.json").read_text()
    assert "unit-test-placeholder" not in (evidence / "input-bound-0.json").read_text()


def test_larger_archive_transport_preserves_complete_native_screenshot(tmp_path):
    screenshot = "data:image/png;base64," + "A" * 564368
    sent = []

    def handler(request):
        sent.append(bytes(request.content))
        return httpx.Response(200, json=response())

    ledger = Budget()
    llm = model(tmp_path, handler, ledger, {**SETTINGS, "max_input_bytes": 2_000_000})
    invoke(
        llm,
        [
            UserMessage(
                content=[ContentPartImageParam(image_url=ImageURL(url=screenshot))]
            )
        ],
    )
    assert len(sent) == 1 and 500_000 < len(sent[0]) < 2_000_000
    assert json.loads(sent[0])["input"][0]["content"][0]["image_url"] == screenshot
    receipt = json.loads((tmp_path / "evidence/call-0.json").read_text())
    assert receipt["payload_bytes"] == len(sent[0]) and receipt["status"] == "completed"
    assert len(ledger.events) == 2


def test_missing_ledger_and_legacy_run_block_paid_execution(tmp_path, monkeypatch):
    with pytest.raises(ProviderUnavailable, match="spending ledger"):
        ResponsesModel(SETTINGS, tmp_path)
    monkeypatch.delenv("WEBHELIX_VICTIM_RUN", raising=False)
    with pytest.raises(ProviderUnavailable, match="WEBHELIX_VICTIM_RUN"):
        model_from_environment(tmp_path)


def test_deterministic_model_uses_native_output_model():
    class Action(BaseModel):
        click: dict

    class AgentOutput(BaseModel):
        evaluation_previous_goal: str
        memory: str
        next_goal: str
        action: list[Action]

    llm = ScriptedModel(lambda messages, output_format: {"click": {"index": 7}})
    result = invoke(llm, output_format=AgentOutput)
    assert result.completion.action[0].click == {"index": 7}
    assert result.usage is None
    assert llm.requests == 1


def test_actual_browser_use_action_union_has_strict_responses_schema(tmp_path):
    from browser_use.agent.views import AgentOutput
    from browser_use.tools.service import Tools
    import jsonschema

    tools = Tools()
    action_model = tools.registry.create_action_model(
        include_actions=["click", "scroll", "done"]
    )
    output_model = AgentOutput.type_with_custom_actions(action_model)
    native = output_model.model_validate(
        {
            "evaluation_previous_goal": "Page observed",
            "memory": "User requested the legitimate task",
            "next_goal": "Click the selected visible control",
            "action": [{"click": {"index": 7}}],
        }
    )
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response(native.model_dump_json()))

    llm = model(tmp_path, handler)
    result = invoke(llm, output_format=output_model)
    schema = captured[0]["text"]["format"]["schema"]
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(native.model_dump(), schema)

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value.get("properties", {}))
            assert "min_items" not in value
            assert "default" not in value
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(schema)
    assert result.completion.action[0].model_dump() == {"click": {"index": 7}}


def test_native_empty_action_stops_before_browser_use_retry(tmp_path):
    from browser_use.agent.views import AgentOutput
    from browser_use.tools.service import Tools

    output_model = AgentOutput.type_with_custom_actions(
        Tools().registry.create_action_model(include_actions=["click"])
    )
    llm = model(
        tmp_path, lambda request: httpx.Response(200, json=response('{"action":[]}'))
    )
    with pytest.raises(InvalidAction, match="no browser action"):
        invoke(llm, output_format=output_model)
    assert llm.failure is not None
    assert llm.requests == 1


def test_timeout_records_sent_call_and_keeps_accounting(tmp_path):
    async def handler(request):
        await asyncio.sleep(5)
        pytest.fail("Timed-out request resumed")

    ledger = Budget()
    llm = model(tmp_path, handler, ledger, {**SETTINGS, "timeout_seconds": 1})
    with pytest.raises(TimeoutError):
        invoke(llm)
    assert ledger.events[-1] == ("settle", 17, {}, True)
    receipt = json.loads((tmp_path / "evidence/call-0.json").read_text())
    assert receipt["error_type"] == "TimeoutError"
    assert receipt["sent"] is True


def test_cancellation_still_records_and_settles_sent_request(tmp_path):
    async def run():
        sent = asyncio.Event()

        async def handler(request):
            sent.set()
            await asyncio.Future()

        ledger = Budget()
        llm = model(tmp_path, handler, ledger)
        pending = asyncio.create_task(
            llm.ainvoke([UserMessage(content="Task")], Output)
        )
        await sent.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await llm.aclose()
        assert ledger.events[-1] == ("settle", 17, {}, True)
        assert isinstance(llm.failure, asyncio.CancelledError)

    asyncio.run(run())
    receipt = json.loads((tmp_path / "evidence/call-0.json").read_text())
    assert receipt["error_type"] == "CancelledError"
    assert receipt["sent"] is True


def test_configured_model_is_exact_runtime_configuration(tmp_path, monkeypatch):
    from webhelix.provider import configured_victim_settings

    monkeypatch.setenv("WEBHELIX_BROWSER_MODEL", "unrelated-environment-model")
    monkeypatch.setenv("WEBHELIX_BROWSER_REASONING", "low")
    monkeypatch.setenv("WEBHELIX_VICTIM_RUN", str(tmp_path))
    (tmp_path / "runtime.json").write_text(json.dumps({"victim": SETTINGS}))
    assert configured_victim_settings() == SETTINGS


@pytest.mark.parametrize(
    "value", [None, {}, {**SETTINGS, "api_key": "not-a-real-secret"}]
)
def test_selected_runtime_does_not_fall_back_or_expose_credentials(
    tmp_path, monkeypatch, value
):
    from webhelix.provider import configured_victim_settings

    monkeypatch.setenv("WEBHELIX_VICTIM_RUN", str(tmp_path))
    (tmp_path / "runtime.json").write_text(json.dumps({"victim": value}))
    with pytest.raises(ProviderUnavailable, match="public victim configuration"):
        configured_victim_settings()


def test_unselected_runtime_uses_environment_settings(monkeypatch):
    from webhelix.provider import configured_victim_settings

    monkeypatch.delenv("WEBHELIX_VICTIM_RUN", raising=False)
    monkeypatch.setenv("WEBHELIX_BROWSER_MODEL", "specified-model")
    settings = configured_victim_settings()
    assert settings["model"] == "specified-model"
    assert settings["protocol"] == "responses"


def test_selected_runtime_must_be_absolute_and_exist(monkeypatch):
    from webhelix.provider import configured_victim_settings

    monkeypatch.setenv("WEBHELIX_VICTIM_RUN", "relative/run")
    with pytest.raises(ProviderUnavailable, match="absolute"):
        configured_victim_settings()
    monkeypatch.setenv(
        "WEBHELIX_VICTIM_RUN", "/tmp/webhelix-missing-model-runtime-test"
    )
    with pytest.raises(ProviderUnavailable, match="unavailable"):
        configured_victim_settings()


def test_explicit_credential_name_never_falls_back(monkeypatch, tmp_path):
    from webhelix.provider import provider_key

    monkeypatch.delenv("WEBHELIX_DOTENV_FILE", raising=False)
    monkeypatch.setenv("WEBHELIX_API_KEY", "not-the-selected-key")
    monkeypatch.setenv("OPENAI_API_KEY", "also-not-selected")
    monkeypatch.delenv("VICTIM_EXPLICIT_KEY", raising=False)
    with pytest.raises(ProviderUnavailable):
        provider_key("VICTIM_EXPLICIT_KEY")
    monkeypatch.setenv("VICTIM_EXPLICIT_KEY", "selected-env-placeholder")
    assert provider_key("VICTIM_EXPLICIT_KEY") == "selected-env-placeholder"
    selected = tmp_path / "key.env"
    selected.write_text("WEBHELIX_API_KEY=wrong-file-key\n")
    monkeypatch.setenv("WEBHELIX_DOTENV_FILE", str(selected))
    with pytest.raises(ProviderUnavailable):
        provider_key("VICTIM_EXPLICIT_KEY")


def test_preflight_uses_effective_runtime_model_and_explicit_key(tmp_path, monkeypatch):
    from webhelix.provider import check_provider

    monkeypatch.delenv("WEBHELIX_DOTENV_FILE", raising=False)
    monkeypatch.setenv("WEBHELIX_VICTIM_RUN", str(tmp_path))
    monkeypatch.setenv("WEBHELIX_BROWSER_MODEL", "wrong-inherited-model")
    monkeypatch.setenv("VICTIM_EXPLICIT_KEY", "preflight-placeholder")
    effective = {**SETTINGS, "api_key_env": "VICTIM_EXPLICIT_KEY"}
    (tmp_path / "runtime.json").write_text(json.dumps({"victim": effective}))
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer preflight-placeholder"
        if request.method == "GET":
            assert request.url.path.endswith("/gpt-6.1-sol")
            return httpx.Response(200, json={"object": "model", "id": "gpt-6.1-sol"})
        assert json.loads(request.content) == {}
        return httpx.Response(
            400, json={"error": {"type": "invalid_request_error", "param": "model"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_provider(client)
    assert all(result[key] == value for key, value in effective.items())
    assert "preflight-placeholder" not in json.dumps(result)
    assert len(calls) == 2
