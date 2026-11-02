"""Explicit, reproducible configuration for an interactive optimizer session."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class OpenCodeSettings:
    model: str = "gpt-6.1-sol"
    reasoning_effort: str = "medium"
    api_key_env: str = "WEBHELIX_API_KEY"
    endpoint: str = "https://api.openai.com/v1/responses"
    timeout_seconds: int = 180
    session_timeout_seconds: int = 600
    max_output_tokens: int = 8192
    # Verified provider model metadata, independent of the input byte limit.
    context_window_tokens: int = 1050000
    max_input_bytes: int = 4000000
    max_response_bytes: int = 4000000
    max_requests_per_session: int = 10
    max_previews_per_session: int = 3
    preview_timeout_seconds: int = 120

    def __post_init__(self):
        if self.endpoint != "https://api.openai.com/v1/responses":
            raise ValueError(
                "This integration verifies the OpenAI Responses endpoint only"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.model):
            raise ValueError(
                "Use an explicit provider model ID without a provider prefix"
            )
        if self.reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("Select a verified OpenCode reasoning variant")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.api_key_env):
            raise ValueError("api_key_env must name an environment variable")
        for field, upper in {
            "timeout_seconds": 180,
            "session_timeout_seconds": 900,
            "max_output_tokens": 8192,
            "context_window_tokens": 1050000,
            "max_input_bytes": 4000000,
            "max_response_bytes": 8000000,
            "max_requests_per_session": 20,
            "max_previews_per_session": 5,
            "preview_timeout_seconds": 120,
        }.items():
            value = getattr(self, field)
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f"{field} must be an integer in 1..{upper}")
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError(
                "max_output_tokens must be smaller than context_window_tokens"
            )

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text()))

    def as_dict(self):
        return asdict(self)
