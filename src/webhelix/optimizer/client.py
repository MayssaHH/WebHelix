"""Explicit provider protocols and durable diagnostics; never retry or switch routes."""

import dataclasses
import json
import os
import re
import signal
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

from webhelix.evaluate import lock
from webhelix.v2.contract import read, save


@dataclasses.dataclass(frozen=True)
class Settings:
    endpoint: str
    protocol: str
    model: str
    api_key_env: str = "WEBHELIX_OPTIMIZER_API_KEY"
    reasoning_effort: str | None = None
    timeout_seconds: int = 90
    max_output_tokens: int = 4096
    max_input_bytes: int = 180000
    max_response_bytes: int = 160000
    max_calls: int = 2

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or (
                url.scheme != "https"
                and not (
                    url.scheme == "http"
                    and url.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        ):
            raise ValueError(
                "Use HTTPS (or loopback HTTP), without URL credentials/query"
            )
        if self.protocol not in {"responses", "chat_completions"}:
            raise ValueError("Explicit responses or chat_completions protocol required")
        if not self.model.strip() or self.model.startswith("REPLACE"):
            raise ValueError("Set an available, verified model ID")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.api_key_env):
            raise ValueError("api_key_env must name an environment variable")
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "none",
        }:
            raise ValueError(
                "Unsupported reasoning setting; omit for providers without it"
            )
        for name, upper in {
            "timeout_seconds": 180,
            "max_output_tokens": 8192,
            "max_input_bytes": 500000,
            "max_response_bytes": 500000,
            "max_calls": 2,
        }.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f"{name} must be an integer in 1..{upper}")

    @classmethod
    def load(cls, path):
        return cls(**read(path))


@contextmanager
def deadline(seconds):
    """Total wall timeout, including response streaming (CLI runs on POSIX/main thread)."""

    def expired(*_):
        raise TimeoutError("Optimizer wall timeout")

    old = signal.signal(signal.SIGALRM, expired)
    previous = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous)
        signal.signal(signal.SIGALRM, old)


def payload(settings, system, context):
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]
    body = {"model": settings.model, "tools": []}
    if settings.protocol == "responses":
        body.update(
            input=messages, max_output_tokens=settings.max_output_tokens, store=False
        )
        if settings.reasoning_effort:
            body["reasoning"] = {"effort": settings.reasoning_effort}
    else:
        body.update(messages=messages, max_completion_tokens=settings.max_output_tokens)
        if settings.reasoning_effort:
            body["reasoning_effort"] = settings.reasoning_effort
    return body


def extract(data, protocol):
    """Return a classified response; never interpret a refusal as a usable proposal."""
    if data.get("error"):
        return "service_error", None
    if protocol == "responses":
        content = [
            c
            for x in data.get("output", [])
            if x.get("type") == "message"
            for c in x.get("content", [])
        ]
        if any(c.get("type") == "refusal" for c in content):
            return "model_refusal", None
        if data.get("status") != "completed":
            return "incomplete_response", None
        if any(
            x.get("type") not in {"message", "reasoning"}
            for x in data.get("output", [])
        ):
            return "malformed_output", None
        text = "".join(
            c.get("text", "") for c in content if c.get("type") == "output_text"
        )
    else:
        choices = data.get("choices", [])
        if len(choices) != 1:
            return "malformed_output", None
        message = choices[0].get("message", {})
        if message.get("refusal"):
            return "model_refusal", None
        if choices[0].get("finish_reason") != "stop":
            return "incomplete_response", None
        if message.get("tool_calls"):
            return "malformed_output", None
        text = message.get("content")
    try:
        return "proposal_received", json.loads(text)
    except (ValueError, TypeError):
        # A prose-only refusal is deliberately NOT guessed from keywords.
        return "malformed_output", None


class ProposalClient:
    def __init__(
        self, settings, run_dir, env_file=None, transport=None, call_limit=None
    ):
        self.settings = settings
        self.run_dir = Path(run_dir)
        self.env_file = env_file
        self.transport = transport
        self.call_limit = settings.max_calls if call_limit is None else call_limit
        if (
            type(self.call_limit) is not int
            or not 1 <= self.call_limit <= settings.max_calls
        ):
            raise ValueError("Call limit must not exceed configured maximum")

    def make_payload(self, system, context):
        """Override input presentation without changing transport/budget semantics."""
        return payload(self.settings, system, context)

    def generate(self, system, context, output):
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        settings = self.settings
        body = self.make_payload(system, context)
        save(output / "request.json", body)
        save(output / "settings.json", dataclasses.asdict(settings))
        result = {
            "status": "starting",
            "requested_model": settings.model,
            "protocol": settings.protocol,
            "usage": {},
            "cost_usd": None,
            "retries": 0,
            "request_started": False,
        }
        started = time.monotonic()
        key = ""
        try:
            raw = json.dumps(body, ensure_ascii=False).encode()
            if len(raw) > settings.max_input_bytes:
                raise ValueError(
                    "Input exceeds byte limit; no silent history truncation"
                )
            if self.env_file and not Path(self.env_file).is_file():
                result["status"] = "missing_credentials"
                return result
            values = (
                dotenv_values(self.env_file, interpolate=False)
                if self.env_file
                else os.environ
            )
            key = values.get(settings.api_key_env) or ""
            if not key or key == "your_api_key_here":
                result["status"] = "missing_credentials"
                return result
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with lock(self.run_dir / "optimizer-budget.lock"):
                path = self.run_dir / "optimizer-budget.json"
                ledger = (
                    read(path)
                    if path.exists()
                    else {"maximum": self.call_limit, "calls": []}
                )
                if (
                    ledger["maximum"] != self.call_limit
                    or len(ledger["calls"]) >= self.call_limit
                ):
                    result["status"] = "budget_exhausted"
                    return result
                ledger["calls"].append({"output": str(output), "started": time.time()})
                save(path, ledger)
            result["request_started"] = True
            with (
                deadline(settings.timeout_seconds),
                httpx.Client(
                    timeout=settings.timeout_seconds,
                    follow_redirects=False,
                    transport=self.transport,
                    trust_env=True,  # Respect the environment's HTTPS proxy and CA trust.
                ) as client,
            ):
                with client.stream(
                    "POST",
                    settings.endpoint,
                    content=raw,
                    headers={
                        "Authorization": "Bearer " + key,
                        "Content-Type": "application/json",
                    },
                ) as response:
                    result.update(
                        http_status=response.status_code,
                        request_id=response.headers.get("x-request-id"),
                    )
                    chunks, count = [], 0
                    for chunk in response.iter_bytes():
                        count += len(chunk)
                        if count > settings.max_response_bytes:
                            raise ValueError("Response exceeds byte limit")
                        chunks.append(chunk)
                    raw_response = b"".join(chunks).decode("utf-8", errors="replace")
                    # Preserve body exactly except a credential echoed by a broken service.
                    redacted = raw_response.replace(key, "[REDACTED]")
                    result["credential_redacted"] = redacted != raw_response
                    (output / "response.txt").write_text(redacted)
                    try:
                        data = json.loads(redacted)
                        response_is_json = isinstance(data, dict)
                    except ValueError:
                        data = {}
                        response_is_json = False
                    if not isinstance(data, dict):
                        data = {}
                    save(output / "response.json", data)
                    result.update(
                        returned_model=data.get("model"),
                        usage=data.get("usage")
                        if isinstance(data.get("usage"), dict)
                        else {},
                        provider_error=data.get("error"),
                    )
                    if not 200 <= response.status_code < 300:
                        result["status"] = "service_error"
                    elif not response_is_json:
                        result["status"] = "malformed_output"
                    else:
                        try:
                            result["status"], proposal = extract(
                                data, settings.protocol
                            )
                        except (TypeError, AttributeError, KeyError):
                            result["status"], proposal = "malformed_output", None
                        if proposal is not None:
                            save(output / "proposal.json", proposal)
        except (httpx.TimeoutException, TimeoutError):
            result["status"] = "timeout"
        except httpx.RequestError as exc:
            result.update(status="transport_error", error=type(exc).__name__)
        except (ValueError, OSError) as exc:
            result.update(
                status="local_error",
                error=str(exc).replace(key, "[REDACTED]") if key else str(exc),
            )
        finally:
            result["latency_seconds"] = time.monotonic() - started
            save(output / "transport-result.json", result)
        return result
