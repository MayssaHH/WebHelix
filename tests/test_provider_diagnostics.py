"""Sanitized provider error receipts; every response is an offline fixture."""

import json

import httpx
import pytest

from webhelix.provider import _error_record


KEY = "unit-test-private-credential"


def test_credit_balance_error_retains_actionable_message_and_request_id():
    message = (
        "You have no credits remaining. Add credits to continue using the API at "
        "https://platform.openai.com/settings/organization/billing/."
    )
    response = httpx.Response(
        429,
        json={
            "error": {
                "message": message,
                "type": "insufficient_quota",
                "code": "credit_balance_exhausted",
                "param": None,
            }
        },
        headers={
            "x-request-id": "req_test-123",
            "authorization": "Bearer must-not-save-this",
            "set-cookie": "private-session=must-not-save-this",
        },
    )
    record = _error_record(response, secret=KEY)
    assert record["http_status"] == 429
    assert record["body_shape"] == "json_error_object"
    assert record["error"] == {
        "type": "insufficient_quota",
        "code": "credit_balance_exhausted",
        "param": None,
        "message": message,
    }
    assert record["headers"] == {"x-request-id": "req_test-123"}
    assert "must-not-save-this" not in json.dumps(record)
    # Callers that do not supply a key still get recognized machine codes,
    # but no free-form error text or additional headers.
    legacy = _error_record(response)
    assert legacy["error"]["code"] == "credit_balance_exhausted"
    assert set(legacy) == {"http_status", "error"}
    assert "message" not in legacy["error"]


def test_rate_limit_diagnostics_retain_only_valid_response_header_values():
    headers = {
        "retry-after": "Thu, 01 Jan 2026 00:00:00 GMT",
        "retry-after-ms": "1500",
        "x-ratelimit-limit-requests": "60",
        "x-ratelimit-remaining-requests": "0",
        "x-ratelimit-limit-tokens": "30000",
        "x-ratelimit-remaining-tokens": "17",
        "x-ratelimit-reset-requests": "1m2.5s",
        "x-ratelimit-reset-tokens": "750ms",
    }
    response = httpx.Response(
        429,
        json={"error": {"type": "rate_limit_exceeded", "code": "rate_limit_exceeded"}},
        headers={**headers, "x-request-id": "x" * 200, "x-secret-info": KEY},
    )
    record = _error_record(response, secret=KEY)
    assert record["headers"] == headers
    assert record["error"]["code"] == "rate_limit_exceeded"
    assert KEY not in json.dumps(record)


def test_unknown_benign_machine_codes_are_not_silently_discarded():
    response = httpx.Response(
        429,
        json={"error": {"type": "account_limit_error", "code": "project_daily_limit"}},
    )
    record = _error_record(response, secret=KEY)
    assert record["error"]["type"] == "account_limit_error"
    assert record["error"]["code"] == "project_daily_limit"
    assert "classification" not in record


def test_redaction_happens_before_truncation_and_covers_common_credentials():
    other_key = "sk-proj-other-example-credential123"
    response = httpx.Response(
        429,
        json={
            "error": {
                "type": KEY,
                "code": other_key,
                "param": KEY,
                "message": "A" * 995 + KEY + " extra text",
            }
        },
        headers={"x-request-id": KEY, "retry-after": KEY},
    )
    record = _error_record(response, secret=KEY)
    assert len(record["error"]["message"]) == 1000
    assert record["error"]["message"] == "A" * 995 + "[REDA"
    assert record["error"]["type"] is None and record["error"]["code"] is None
    assert record["error"]["param"] is None and record["headers"] == {}
    assert KEY not in json.dumps(record) and other_key not in json.dumps(record)

    response = httpx.Response(
        429,
        text=f"Upstream unavailable. Active key {KEY}; other key {other_key}; Bearer token-other-value.",
    )
    record = _error_record(response, secret=KEY)
    assert record["body_shape"] == "non_json"
    assert "Upstream unavailable" in record["error"]["message"]
    assert all(
        value not in json.dumps(record)
        for value in (KEY, other_key, "token-other-value")
    )


@pytest.mark.parametrize(
    "body,shape,message",
    [
        (
            {"error": "Upstream temporarily unavailable"},
            "json_error_string",
            "Upstream temporarily unavailable",
        ),
        (
            {"message": "Provider unavailable"},
            "json_object_without_error_object",
            "Provider unavailable",
        ),
        (
            {"error": {"message": ["unexpected", "structure"]}},
            "json_error_object",
            None,
        ),
        (["unexpected", "structure"], "json_non_object", None),
        (None, "json_non_object", None),
    ],
)
def test_nonstandard_error_shapes_are_recorded_without_masking_http_error(
    body, shape, message
):
    response = httpx.Response(
        429, content=json.dumps(body), headers={"content-type": "application/json"}
    )
    record = _error_record(response, secret=KEY)
    assert record["http_status"] == 429
    assert record["body_shape"] == shape
    assert record["error"]["message"] == message


def test_invalid_machine_fields_and_retry_headers_are_omitted():
    response = httpx.Response(
        429,
        json={"error": {"type": {"nested": KEY}, "code": "free form message " + KEY}},
        headers={
            "x-request-id": "request with spaces",
            "retry-after": "not a time",
            "retry-after-ms": "inf",
            "x-ratelimit-reset-tokens": "forever",
            "x-ratelimit-remaining-tokens": "-10",
        },
    )
    record = _error_record(response, secret=KEY)
    assert record["error"]["type"] is None and record["error"]["code"] is None
    assert record["headers"] == {}
    assert KEY not in json.dumps(record)
