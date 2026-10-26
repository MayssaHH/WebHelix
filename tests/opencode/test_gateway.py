"""Real loopback gateway tests; all provider requests use an in-memory transport."""

import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import httpx
import pytest

from webhelix.opencode.gateway import Gateway, RequestRejected, effective_payload
from webhelix.search.budget import Budget
from webhelix.v2.contract import read, save


def setup(tmp_path, **changes):
    settings = {
        "endpoint": "https://api.openai.com/v1/responses",
        "model": "offline-model",
        "reasoning_effort": "medium",
        "max_output_tokens": 100,
        "max_input_bytes": 400000,
        "max_response_bytes": 400000,
        "timeout_seconds": 5,
        "max_requests_per_session": 3,
    }
    settings.update(changes)
    save(
        tmp_path / "runtime.json",
        {
            "run_deadline_epoch": time.time() + 60,
            "max_optimizer_calls": 10,
            "max_real_episodes": 0,
            "max_actions": 24,
        },
    )
    save(
        tmp_path / "spending-plan.json",
        {
            "authorized_usd": 10,
            "verified_on": "offline fixture",
            "roles": {
                "optimizer": {
                    "model": "offline-model",
                    "input_usd_per_million": 1,
                    "output_usd_per_million": 2,
                    "maximum_context_input_tokens": 1000,
                    "pricing_source": "offline fixture",
                    "context_source": "offline fixture",
                }
            },
        },
    )
    return settings


def completed(usage=True, **changes):
    body = {
        "id": "resp_offline",
        "status": "completed",
        "model": "offline-model",
        "output": [
            {
                "type": "function_call",
                "name": "read",
                "call_id": "call_1",
                "arguments": '{"filePath":"/work/TASK.json"}',
            }
        ],
    }
    if usage:
        body["usage"] = {"input_tokens": 10, "output_tokens": 3}
    body.update(changes)
    return body


def post(gateway, body=None, **kwargs):
    return httpx.post(
        gateway.url + "/v1/responses",
        headers={"Authorization": "Bearer " + gateway.token},
        json=body
        or {"model": "offline-model", "input": "Read TASK.json", "stream": True},
        timeout=10,
        trust_env=False,
        **kwargs,
    )


def sse(body):
    events = [
        {
            "type": "response.created",
            "response": {"id": "resp_offline", "status": "in_progress"},
        },
        {
            "type": "response.output_item.added",
            "item": body["output"][0] if body.get("output") else {},
        },
        {"type": "response.completed", "response": body},
    ]
    return "".join(
        "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
        for event in events
    ).encode()


def test_real_streaming_proxy_preserves_tools_and_accounts_each_request(tmp_path):
    settings = setup(tmp_path)
    captured = []
    raw = sse(completed())

    def transport(request):
        assert (
            request.headers["Authorization"]
            == "Bearer private-offline-provider-credential"
        )
        captured.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=raw
        )

    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "private-offline-provider-credential",
        transport=httpx.MockTransport(transport),
    ) as gateway:
        response = post(
            gateway,
            {
                "model": "offline-model",
                "input": "Read TASK.json",
                "stream": True,
                "max_output_tokens": 9999,
                "service_tier": "priority",
                "store": True,
                "reasoning": {"effort": "high"},
                "tools": [
                    {
                        "type": "function",
                        "name": "read",
                        "parameters": {"type": "object"},
                    }
                ],
            },
        )
        assert response.status_code == 200 and response.content == raw
        response = post(
            gateway,
            {
                "model": "offline-model",
                "stream": True,
                "input": [
                    {
                        "type": "function_call_output",
                        "call_id": "call_1",
                        "output": "Frozen legitimate task",
                    }
                ],
            },
        )
        assert response.status_code == 200
        session_token = gateway.token
    assert len(captured) == 2
    assert captured[0]["tools"][0]["name"] == "read"
    assert captured[0]["max_output_tokens"] == 100
    assert captured[0]["reasoning"]["effort"] == "medium"
    assert captured[0]["service_tier"] == "default" and captured[0]["store"] is False
    assert captured[1]["input"][0]["type"] == "function_call_output"
    summary = read(tmp_path / "gateway/summary.json")
    assert summary["requests_forwarded"] == 2
    assert summary["status_counts"] == {"completed": 2}
    ledger = read(tmp_path / "budget.json")
    assert len(ledger["calls"]) == 2 and ledger["episodes"] == []
    assert all(
        c["usage"] == {"input_tokens": 10, "output_tokens": 3} for c in ledger["calls"]
    )
    for path in (tmp_path / "gateway").rglob("*"):
        if path.is_file():
            assert "private-offline-provider-credential" not in path.read_text()
            assert session_token not in path.read_text()


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "other-model"},
        {"tools": [{"type": "web_search_preview"}]},
        {"tools": [{"type": "mcp", "server_url": "https://example.invalid"}]},
        {"tool_choice": {"type": "computer_use_preview"}},
        {"previous_response_id": "resp_other"},
        {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "https://example.invalid/image.png",
                        }
                    ],
                }
            ]
        },
        {"input": [{"type": "input_file", "file_id": "file_other"}]},
        {"max_output_tokens": -1},
        {"stream": "yes"},
    ],
)
def test_unsupported_requests_never_reach_provider(tmp_path, changes):
    settings = setup(tmp_path)
    calls = []
    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        transport=httpx.MockTransport(lambda r: calls.append(r)),
    ) as gateway:
        result = post(gateway, {"model": "offline-model", "input": "hello", **changes})
        assert result.status_code == 400
    assert not calls and not (tmp_path / "budget.json").exists()
    assert gateway.summary["status_counts"] == {"request_rejected": 1}


def test_authentication_routes_and_preview_do_not_make_model_calls(tmp_path):
    settings = setup(tmp_path, max_previews_per_session=1)
    previews = []
    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        preview_callback=lambda: (
            previews.append(True)
            or {"artifacts": [{"path": "frame.png", "content_base64": "offline"}]}
        ),
    ) as gateway:
        headers = {"Authorization": "Bearer " + gateway.token}
        assert (
            httpx.post(
                gateway.url + "/v1/responses", json={"input": "hello"}, trust_env=False
            ).status_code
            == 401
        )
        assert (
            httpx.post(
                gateway.url + "/v1/chat/completions",
                headers=headers,
                json={},
                trust_env=False,
            ).status_code
            == 400
        )
        response = httpx.post(
            gateway.url + "/preview", headers=headers, json={}, trust_env=False
        )
        assert response.status_code == 200 and response.json()["artifacts"]
        assert (
            httpx.post(
                gateway.url + "/preview", headers=headers, json={}, trust_env=False
            ).status_code
            == 400
        )
    assert len(previews) == 1 and gateway.summary["preview_calls"] == 1
    assert not (tmp_path / "budget.json").exists()


@pytest.mark.parametrize("with_usage", [True, False])
def test_zero_usage_and_missing_usage_are_distinct(tmp_path, with_usage):
    settings = setup(tmp_path)
    body = completed(usage=False)
    if with_usage:
        body["usage"] = {"input_tokens": 0, "output_tokens": 0}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    with Gateway(
        tmp_path, tmp_path / "gateway", settings, "offline-secret", transport=transport
    ) as gateway:
        assert post(gateway).status_code == 200
    call = read(tmp_path / "budget.json")["calls"][0]
    if with_usage:
        assert call["status"] == "usage_reported" and float(call["accounted_usd"]) == 0
    else:
        assert call["status"] == "usage_missing_full_reservation_retained"
        assert call["accounted_usd"] == call["reserved_usd"]


@pytest.mark.parametrize("mode", ["session", "total_requests", "spending"])
def test_budget_checks_happen_before_forwarding_and_retries_are_blocked(tmp_path, mode):
    settings = setup(tmp_path, max_requests_per_session=1 if mode == "session" else 3)
    if mode == "total_requests":
        runtime = read(tmp_path / "runtime.json")
        runtime["max_optimizer_calls"] = 1
        save(tmp_path / "runtime.json", runtime)
    if mode == "spending":
        plan = read(tmp_path / "spending-plan.json")
        plan["authorized_usd"] = 0.00001
        save(tmp_path / "spending-plan.json", plan)
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=completed())

    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        transport=httpx.MockTransport(transport),
    ) as gateway:
        if mode != "spending":
            assert post(gateway).status_code == 200
        assert post(gateway).status_code == 429
        assert post(gateway).status_code == 400  # CLI retry cannot bypass sticky stop.
    assert len(calls) == (0 if mode == "spending" else 1)
    assert gateway.summary["halted"] == "budget_blocked"


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("refusal", "refusal"),
        ("http_error", "service_error"),
        ("timeout", "timeout"),
        ("malformed", "malformed_response"),
        ("incomplete", "incomplete_response"),
        ("wrong_model", "model_mismatch"),
    ],
)
def test_failure_classes_and_no_automatic_provider_retry(tmp_path, mode, expected):
    settings = setup(tmp_path)
    calls = []

    def transport(request):
        calls.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("offline timeout")
        if mode == "http_error":
            return httpx.Response(
                429, json={"error": {"message": "offline rate limit"}}
            )
        if mode == "malformed":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content='data: {"type":"response.created"}\n\n',
            )
        body = completed()
        if mode == "refusal":
            body["output"] = [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "offline refusal"}],
                }
            ]
        elif mode == "incomplete":
            body["status"] = "incomplete"
        elif mode == "wrong_model":
            body["model"] = "unrequested-model"
        return httpx.Response(200, json=body)

    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        transport=httpx.MockTransport(transport),
    ) as gateway:
        post(gateway)
        assert post(gateway).status_code == 400
    assert len(calls) == 1
    assert gateway.summary["requests"][0]["status"] == expected
    assert gateway.summary["halted"] == expected
    if mode in {"timeout", "http_error", "malformed"}:
        assert (
            read(tmp_path / "budget.json")["calls"][0]["status"]
            == "usage_missing_full_reservation_retained"
        )


def test_input_image_data_and_local_tool_outputs_are_supported(tmp_path):
    settings = setup(tmp_path)
    body = effective_payload(
        {
            "model": "offline-model",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,ZmFrZQ==",
                        }
                    ],
                },
                {
                    "type": "function_call_output",
                    "call_id": "preview",
                    "output": "Local preview ready",
                },
            ],
        },
        settings,
    )
    assert body["input"][0]["content"][0]["type"] == "input_image"
    with pytest.raises(RequestRejected):
        effective_payload(
            {
                "model": "offline-model",
                "input": "hello",
                "tools": [{"type": "file_search"}],
            },
            settings,
        )


def test_response_bound_and_secret_redaction(tmp_path):
    settings = setup(tmp_path, max_response_bytes=100)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"offline-provider-credential" * 20)
    )
    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-provider-credential",
        transport=transport,
    ) as gateway:
        assert post(gateway).status_code == 502
    assert gateway.summary["requests"][0]["status"] == "malformed_response"
    for path in Path(tmp_path / "gateway").rglob("*"):
        if path.is_file():
            assert "offline-provider-credential" not in path.read_text()


def test_finalized_summary_waits_for_usage_settlement_after_http_response(
    tmp_path, monkeypatch
):
    settings = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = Budget.settle

    def delayed_settlement(self, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Budget, "settle", delayed_settlement)
    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completed())),
    ) as gateway:
        assert post(gateway).status_code == 200
        assert entered.wait(2)
        assert not gateway.summary["finalized"]
        assert not gateway.wait_idle(timeout_seconds=0.01)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(gateway.finalized_summary, 3)
            time.sleep(0.02)
            assert not future.done()
            release.set()
            summary = future.result(timeout=3)
        assert summary["finalized"]
        assert summary["usage"] == {
            "input_tokens": 10,
            "output_tokens": 3,
            "requests_with_reported_usage": 1,
            "requests_missing_usage": 0,
        }
        assert read(tmp_path / "budget.json")["calls"][0]["status"] == "usage_reported"
        assert post(gateway).status_code == 400


def test_finalize_timeout_cancels_inflight_and_keeps_conservative_reservation(tmp_path):
    settings = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def transport(_):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json=completed())

    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        transport=httpx.MockTransport(transport),
    ) as gateway:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(post, gateway)
            assert entered.wait(2)
            summary = gateway.finalized_summary(timeout_seconds=0.02)
            assert not summary["finalized"] and summary["halted"] == "timeout"
            assert summary["requests"][0]["cancellation_requested"]
            call = read(tmp_path / "budget.json")["calls"][0]
            assert call["accounted_usd"] == call["reserved_usd"]
            release.set()
            assert future.result(timeout=3).status_code == 504
        assert gateway.wait_idle(2)
    assert gateway.summary["requests"][0]["status"] == "timeout"
    call = read(tmp_path / "budget.json")["calls"][0]
    assert call["status"] == "usage_missing_full_reservation_retained"


def test_preview_respects_whole_run_deadline_without_starting_callback(tmp_path):
    settings = setup(tmp_path)
    runtime = read(tmp_path / "runtime.json")
    runtime["run_deadline_epoch"] = time.time() - 1
    save(tmp_path / "runtime.json", runtime)
    previews = []
    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        preview_callback=lambda: previews.append(True),
    ) as gateway:
        response = httpx.post(
            gateway.url + "/preview",
            json={},
            trust_env=False,
            headers={"Authorization": "Bearer " + gateway.token},
        )
        assert response.status_code == 429
        assert gateway.finalized_summary()["halted"] == "budget_blocked"
    assert not previews and not (tmp_path / "budget.json").exists()


def test_concurrent_request_cannot_bypass_single_inflight_limit(tmp_path):
    settings = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def transport(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json=completed())

    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        transport=httpx.MockTransport(transport),
    ) as gateway:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(post, gateway)
            assert entered.wait(2)
            assert post(gateway).status_code == 400
            release.set()
            assert future.result(timeout=3).status_code == 200
        summary = gateway.finalized_summary()
        assert summary["finalized"]
        assert summary["status_counts"] == {"completed": 1, "request_rejected": 1}
    assert len(calls) == 1 and len(read(tmp_path / "budget.json")["calls"]) == 1


@pytest.mark.parametrize(
    "failure, released",
    [(httpx.ConnectError, True), (httpx.ReadTimeout, False), (httpx.WriteError, False)],
)
def test_only_known_connection_failure_releases_unsent_reservation(
    tmp_path, failure, released
):
    settings = setup(tmp_path)

    def transport(request):
        raise failure("offline transport failure")

    with Gateway(
        tmp_path,
        tmp_path / "gateway",
        settings,
        "offline-secret",
        transport=httpx.MockTransport(transport),
    ) as gateway:
        assert post(gateway).status_code >= 500
        summary = gateway.finalized_summary()
        assert summary["finalized"]
    ledger = read(tmp_path / "budget.json")
    assert len(ledger["calls"]) == 1  # The failed attempt is never erased.
    call = ledger["calls"][0]
    if released:
        assert call["status"] == "not_sent" and call["accounted_usd"] == "0"
        assert summary["halted"] == "connection_error"
        assert (
            summary["requests"][0]["delivery"]
            == "connection_failed_before_http_request"
        )
    else:
        assert call["status"] == "usage_missing_full_reservation_retained"
        assert call["accounted_usd"] == call["reserved_usd"]


def test_real_host_transport_honors_proxy_environment_without_exposing_it(
    tmp_path, monkeypatch
):
    settings = setup(tmp_path)
    original = httpx.Client
    flags = []

    def client(*args, **kwargs):
        if "transport" in kwargs:
            flags.append(kwargs["trust_env"])
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(200, json=completed())
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    with Gateway(tmp_path, tmp_path / "gateway", settings, "offline-secret") as gateway:
        assert post(gateway).status_code == 200
        assert gateway.finalized_summary()["finalized"]
    assert flags == [True]
