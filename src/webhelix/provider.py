"""Provider configuration and sanitized diagnostics; no browser decision policy."""

import json
import os
from contextlib import nullcontext
from pathlib import Path

import httpx
from dotenv import dotenv_values


class ProviderUnavailable(RuntimeError):
    pass


class InvalidAction(ValueError):
    pass


def provider_key(variable=None):
    """Use an explicitly selected credential file even in nested agent workers.

    Never search a candidate directory for credentials or interpolate its env.
    A selected file fails closed; an inherited placeholder is not a fallback.
    """
    path = os.environ.get("WEBHELIX_DOTENV_FILE")
    values = os.environ
    if path:
        selected = Path(path)
        if not selected.is_absolute() or not selected.is_file():
            raise ProviderUnavailable("Selected WEBHELIX_DOTENV_FILE is unavailable")
        try:
            with selected.open() as stream:
                values = dotenv_values(stream=stream, interpolate=False)
        except (OSError, UnicodeError):
            raise ProviderUnavailable(
                "Selected WEBHELIX_DOTENV_FILE is unreadable"
            ) from None
    key = (
        values.get(variable)
        if variable is not None
        else values.get("WEBHELIX_API_KEY") or values.get("OPENAI_API_KEY")
    )
    if not key or key == "your_api_key_here":
        raise ProviderUnavailable(
            (variable or "WEBHELIX_API_KEY (or OPENAI_API_KEY)")
            + " is missing from the selected source. "
            "Codex ChatGPT sign-in is not Responses API authentication."
        )
    return key


def provider_settings():
    return {
        "provider": "openai-responses",
        "model": os.environ.get("WEBHELIX_BROWSER_MODEL", "gpt-6.1-sol"),
        "reasoning": os.environ.get("WEBHELIX_BROWSER_REASONING", "medium"),
        "max_output_tokens": 1024,
        "request_timeout_seconds": 45,
        "retries": 0,
    }


def _error_record(response):
    """Retain diagnostic codes, never free-form errors that can echo credentials."""
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}
    if not isinstance(error, dict):
        error = {}
    allowed = {
        "type": {"invalid_request_error", "authentication_error", "server_error"},
        "code": {
            "invalid_api_key",
            "missing_required_parameter",
            "model_not_found",
            "insufficient_quota",
            "rate_limit_exceeded",
            "unsupported_parameter",
            "unsupported_value",
        },
        "param": {"model", "input"},
    }
    return {
        "http_status": response.status_code,
        "error": {
            field: value if isinstance(value, str) and value in permitted else None
            for field, permitted in allowed.items()
            for value in [error.get(field)]
        },
    }


def check_provider(client=None):
    """Check metadata and Responses authentication without requesting inference."""
    config = configured_victim_settings()
    if (
        config.get("endpoint") != "https://api.openai.com/v1/responses"
        or config.get("protocol") != "responses"
    ):
        raise ProviderUnavailable(
            "Provider preflight requires the configured OpenAI Responses endpoint"
        )
    key = provider_key(config.get("api_key_env", "WEBHELIX_API_KEY"))
    with httpx.Client(timeout=15) if client is None else nullcontext(client) as client:
        response = client.get(
            "https://api.openai.com/v1/models/" + config["model"],
            headers={"Authorization": "Bearer " + key},
        )
        if response.status_code != 200:
            raise ProviderUnavailable(
                f"Model lookup HTTP {response.status_code}; verify the exact model. No model substitution was made."
            )
        metadata = response.json()
        if metadata.get("object") != "model" or metadata.get("id") != config["model"]:
            raise ProviderUnavailable("Model lookup did not return the requested model")
        # Model metadata may be accessible without valid authentication. An empty
        # Responses request cannot generate output; require the expected schema
        # validation result, rather than treating any non-401 response as success.
        response = client.post(
            "https://api.openai.com/v1/responses",
            json={},
            headers={"Authorization": "Bearer " + key},
        )
    record = _error_record(response)
    error = record["error"]
    if not (
        response.status_code == 400
        and error["type"] == "invalid_request_error"
        and (error["code"] == "missing_required_parameter" or error["param"] == "model")
    ):
        raise ProviderUnavailable(
            f"Responses authentication preflight HTTP {response.status_code} "
            f"({error['code'] or 'unclassified'}); expected missing-model validation. "
            "Check the API credential and its api.openai.com secret binding. "
            "No inference was requested."
        )
    return {
        **config,
        "model_lookup": "recognized; metadata alone does not prove access",
        "responses_preflight": "empty request reached parameter validation",
        "generation_access_reasoning_and_schema": "unverified until a successful response call",
    }


def settings_from_environment():
    """Normalize environment settings for the single Responses model adapter."""
    config = provider_settings()
    return {
        "endpoint": "https://api.openai.com/v1/responses",
        "protocol": "responses",
        "model": config["model"],
        "reasoning_effort": config["reasoning"],
        "api_key_env": "WEBHELIX_API_KEY",
        "max_output_tokens": config["max_output_tokens"],
        "max_input_bytes": 500000,
        "timeout_seconds": config["request_timeout_seconds"],
    }


def configured_victim_settings():
    """Return the effective public model configuration used by legacy entrypoints.

    An explicit run is authoritative. Invalid or absent runtime settings do not
    fall back to inherited environment model choices. Credentials are never read
    here; the only allowed credential-related field is its environment variable
    name, not its value.
    """
    selected = os.environ.get("WEBHELIX_VICTIM_RUN")
    if selected is None:
        return settings_from_environment()
    directory = Path(selected)
    if not selected or not directory.is_absolute():
        raise ProviderUnavailable(
            "WEBHELIX_VICTIM_RUN must be an absolute run directory"
        )
    try:
        runtime = json.loads((directory / "runtime.json").read_text())
    except (OSError, ValueError) as exc:
        raise ProviderUnavailable(
            "Selected victim runtime configuration is unavailable"
        ) from exc
    settings = runtime.get("victim") if isinstance(runtime, dict) else None
    allowed = {
        "endpoint",
        "protocol",
        "model",
        "reasoning_effort",
        "api_key_env",
        "max_output_tokens",
        "max_input_bytes",
        "max_response_bytes",
        "timeout_seconds",
    }
    if not isinstance(settings, dict) or not settings or set(settings) - allowed:
        raise ProviderUnavailable(
            "Selected runtime requires a public victim configuration without credential values"
        )
    return dict(settings)
