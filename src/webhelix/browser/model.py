"""Browser-Use model protocol over the existing, fully accounted Responses API.

This adapter transports Browser-Use's own messages and output schema. It does not
construct browser instructions, select observations, maintain agent memory, or
choose actions. Every HTTP attempt is recorded and reserved in WebHelix's ledger.
"""

import asyncio
import inspect
import json
import os
from pathlib import Path

from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage
from dotenv import dotenv_values
import httpx
from pydantic import BaseModel, ValidationError

from webhelix.provider import (
    InvalidAction,
    ProviderUnavailable,
    _error_record,
    provider_key,
    configured_victim_settings,
)


ENDPOINT = "https://api.openai.com/v1/responses"


def _save(path, value, secret=None):
    text = json.dumps(value, indent=2, ensure_ascii=False)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def responses_messages(messages):
    """Translate native message content without changing its text or image bytes."""
    result = []
    for message in messages:
        if message.role not in {"system", "user", "assistant"}:
            raise ProviderUnavailable("Unsupported Browser-Use message role")
        if getattr(message, "tool_calls", None):
            raise ProviderUnavailable(
                "Unexpected provider tool calls in structured agent output"
            )
        content = message.content
        if content is None:
            content = ""
        elif not isinstance(content, str):
            translated = []
            for part in content:
                if part.type == "text":
                    translated.append({"type": "input_text", "text": part.text})
                elif part.type == "image_url":
                    if message.role != "user" or not part.image_url.url.startswith(
                        "data:image/"
                    ):
                        raise ProviderUnavailable(
                            "Only embedded user screenshot images are supported"
                        )
                    translated.append(
                        {
                            "type": "input_image",
                            "image_url": part.image_url.url,
                            "detail": part.image_url.detail,
                        }
                    )
                else:
                    raise ProviderUnavailable("Unsupported Browser-Use message content")
            if message.role == "assistant":
                # Assistant history is textual JSON in Browser-Use. The Responses
                # easy-input message permits a string and avoids replaying a fake
                # provider output item with invented metadata.
                content = "\n".join(item["text"] for item in translated)
            else:
                content = translated
        result.append({"role": message.role, "content": content})
    return result


def _usage(raw):
    inp, out = raw.get("input_tokens"), raw.get("output_tokens")
    if any(type(number) is not int or number < 0 for number in (inp, out)):
        return None
    details = raw.get("input_tokens_details") or {}
    return ChatInvokeUsage(
        prompt_tokens=inp,
        completion_tokens=out,
        total_tokens=inp + out,
        prompt_cached_tokens=details.get("cached_tokens"),
        prompt_cache_creation_tokens=None,
        prompt_image_tokens=None,
    )


class ResponsesModel:
    """A native Browser-Use BaseChatModel implementation with no implicit retries."""

    _verified_api_keys = False

    def __init__(self, settings, output, env_file=None, budget=None, transport=None):
        self.settings = dict(settings)
        if (
            settings.get("endpoint") != ENDPOINT
            or settings.get("protocol") != "responses"
        ):
            raise ProviderUnavailable(
                "Browser-Use victim requires the configured OpenAI Responses endpoint"
            )
        if not settings.get("model"):
            raise ProviderUnavailable("An explicit victim model is required")
        if budget is None:
            raise ProviderUnavailable(
                "Paid victim calls require a configured WebHelix spending ledger"
            )
        for name in ("max_output_tokens", "max_input_bytes", "timeout_seconds"):
            if type(settings.get(name)) is not int or settings[name] <= 0:
                raise ProviderUnavailable(f"Victim {name} must be a positive integer")
        if settings.get("reasoning_effort") not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            raise ProviderUnavailable(
                "Explicit supported victim reasoning_effort is required"
            )
        if env_file is not None:
            selected = Path(env_file)
            if not selected.is_absolute() or not selected.is_file():
                raise ProviderUnavailable(
                    "Selected victim credential file is unavailable"
                )
            self._key = dotenv_values(selected, interpolate=False).get(
                settings.get("api_key_env", "WEBHELIX_API_KEY")
            )
        else:
            self._key = provider_key(settings.get("api_key_env", "WEBHELIX_API_KEY"))
        if not self._key or self._key == "your_api_key_here":
            raise ProviderUnavailable(
                "Victim API credential is missing from the selected private source"
            )
        self.model = settings["model"]
        self.output = Path(output)
        self.budget = budget
        self.usage = []
        self.requests = 0
        self.failure = None
        self.client = httpx.AsyncClient(
            timeout=settings["timeout_seconds"],
            transport=transport,
            follow_redirects=False,
        )

    @property
    def provider(self):
        return "openai"

    @property
    def name(self):
        return self.model

    @property
    def model_name(self):
        return self.model

    async def ainvoke(self, messages, output_format=None, **kwargs):
        # Agent implementations can retry internally. A failed request is sticky:
        # another invocation fails locally and cannot produce an uncounted charge.
        if self.failure is not None:
            raise self.failure
        number, reserved, sent, usage = self.requests, None, False, {}
        self.requests += 1
        receipt = {"request": number, "sent": False, "status": "preparing"}
        try:
            body = {
                "model": self.model,
                "input": responses_messages(messages),
                "reasoning": {"effort": self.settings["reasoning_effort"]},
                "max_output_tokens": self.settings["max_output_tokens"],
                "store": False,
                "tools": [],
                "tool_choice": "none",
                "service_tier": "default",
            }
            if output_format is not None:
                body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "agent_output",
                        "strict": True,
                        "schema": SchemaOptimizer.create_optimized_json_schema(
                            output_format, remove_defaults=True, remove_min_items=True
                        ),
                    }
                }
            encoded = json.dumps(body, ensure_ascii=False).encode()
            if len(encoded) > self.settings["max_input_bytes"]:
                raise ProviderUnavailable(
                    "Victim input bound reached; context was not silently truncated"
                )
            _save(
                self.output / f"agent-messages-{number}.json",
                [m.model_dump(mode="json") for m in messages],
                self._key,
            )
            _save(self.output / f"request-{number}.json", body, self._key)
            reserved = self.budget.reserve(
                "victim", self.model, self.settings["max_output_tokens"]
            )
            receipt.update(status="reserved", ledger_call=reserved)
            # A timeout after dispatch is conservatively charged unless the provider
            # reports usage. We do not pretend that a connection error proves zero cost.
            sent = True
            receipt["sent"] = True
            async with asyncio.timeout(self.settings["timeout_seconds"]):
                response = await self.client.post(
                    ENDPOINT,
                    content=encoded,
                    headers={
                        "Authorization": "Bearer " + self._key,
                        "Content-Type": "application/json",
                    },
                )
            if response.status_code != 200:
                _save(self.output / f"response-{number}.json", _error_record(response))
                raise ProviderUnavailable(
                    f"Victim HTTP {response.status_code}; no retry"
                )
            if len(response.content) > self.settings.get(
                "max_response_bytes", 4_000_000
            ):
                raise ProviderUnavailable(
                    "Victim response exceeds the recorded response bound"
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderUnavailable(
                    "Victim provider returned malformed JSON"
                ) from exc
            if not isinstance(data, dict):
                raise ProviderUnavailable("Victim provider response is not an object")
            usage = data.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            self.usage.append(usage)
            _save(self.output / f"response-{number}.json", data, self._key)
            parts = [
                part
                for item in data.get("output", [])
                if item.get("type") == "message"
                for part in item.get("content", [])
            ]
            if any(part.get("type") == "refusal" for part in parts):
                raise InvalidAction("Victim declined to act")
            if data.get("status") != "completed":
                raise InvalidAction("Victim response incomplete")
            answer = "".join(
                part.get("text", "")
                for part in parts
                if part.get("type") == "output_text"
            )
            try:
                completion = (
                    output_format.model_validate_json(answer)
                    if output_format is not None
                    else answer
                )
            except (ValidationError, ValueError) as exc:
                raise InvalidAction(
                    "Victim returned malformed Browser-Use output"
                ) from exc
            if hasattr(completion, "action") and (
                not completion.action
                or all(
                    not action.model_dump(exclude_none=True)
                    for action in completion.action
                )
            ):
                raise InvalidAction("Victim returned no browser action")
            receipt["status"] = "completed"
            return ChatInvokeCompletion(
                completion=completion,
                usage=_usage(usage),
                stop_reason=data.get("status"),
            )
        except BaseException as exc:
            self.failure = exc
            receipt.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            try:
                if reserved is not None:
                    self.budget.settle(reserved, usage, started=sent)
            except BaseException as exc:
                self.failure = exc
                receipt.update(
                    status="accounting_failed", error_type=type(exc).__name__
                )
                raise
            finally:
                receipt["usage"] = usage
                _save(self.output / f"call-{number}.json", receipt)

    async def aclose(self):
        await self.client.aclose()


def model_from_environment(output):
    """Legacy task entrypoints use the same explicitly configured spending ledger."""
    from webhelix.search.budget import Budget, validate_plan

    directory = os.environ.get("WEBHELIX_VICTIM_RUN")
    if not directory or not Path(directory).is_absolute():
        raise ProviderUnavailable(
            "Set WEBHELIX_VICTIM_RUN to an absolute run directory containing runtime.json and spending-plan.json before paid victim execution"
        )
    run = Path(directory)
    try:
        runtime = json.loads((run / "runtime.json").read_text())
        plan = json.loads((run / "spending-plan.json").read_text())
    except (OSError, ValueError) as exc:
        raise ProviderUnavailable(
            "Selected victim run configuration is unavailable"
        ) from exc
    settings = configured_victim_settings()
    validate_plan(plan, {"victim": settings["model"]})
    for field in ("max_real_episodes", "max_actions"):
        if type(runtime.get(field)) is not int or runtime[field] <= 0:
            raise ProviderUnavailable(
                f"Selected victim runtime requires positive {field}"
            )
    budget = Budget(run, runtime)
    model = ResponsesModel(
        settings, output, env_file=os.environ.get("WEBHELIX_DOTENV_FILE"), budget=budget
    )
    budget.episode(str(output))
    return model


class ScriptedModel:
    """Deterministic test/control double exercised through the native Agent loop."""

    _verified_api_keys = True
    model = "webhelix-scripted-control"
    provider = "webhelix-test"
    name = model
    model_name = model

    def __init__(self, callback):
        self.callback = callback
        self.usage = []
        self.requests = 0
        self.failure = None

    async def ainvoke(self, messages, output_format=None, **kwargs):
        self.requests += 1
        try:
            value = self.callback(messages, output_format)
            if inspect.isawaitable(value):
                value = await value
            if output_format is not None and not isinstance(value, BaseModel):
                if "action" not in value:
                    value = {
                        **{
                            name: "Deterministic engineering control"
                            for name in (
                                "evaluation_previous_goal",
                                "memory",
                                "next_goal",
                            )
                            if name in output_format.model_fields
                        },
                        "action": [value],
                    }
                value = output_format.model_validate(value)
            return ChatInvokeCompletion(
                completion=value, usage=None, stop_reason="scripted_control"
            )
        except BaseException as exc:
            self.failure = exc
            raise

    async def aclose(self):
        return None
