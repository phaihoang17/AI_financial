"""Serving contracts for the ADR-049 OpenAI-compatible boundary.

These are configuration/transport contracts only. They carry *where* a model is
served and *what task* it serves, never how the pipeline routes work. Model
tier routing stays in ``src/supervisor`` and is only consumed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse


SERVING_SCHEMA_VERSION = "m10-serving-v1"


class ServingError(Exception):
    """A typed serving-configuration, transport, or protocol failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ServingTask(str, Enum):
    """The kind of model a serving endpoint hosts."""

    GENERATE = "GENERATE"  # autoregressive decoder LLM (Qwen)
    EMBED = "EMBED"  # BGE-M3 pooling/embedding
    SCORE = "SCORE"  # BGE cross-encoder reranking


class ServingRole(str, Enum):
    """A pipeline role that consumes a served model."""

    NLU = "NLU"
    SUPERVISOR = "SUPERVISOR"
    VERIFIER = "VERIFIER"
    PROGRAMMER = "PROGRAMMER"


def _require_nonempty_str(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServingError("INVALID_ENDPOINT", f"{path} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ServingEndpoint:
    """One served model process reachable over an OpenAI-compatible HTTP API."""

    name: str
    base_url: str
    model_id: str
    task: ServingTask
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        _require_nonempty_str(self.name, "name")
        _require_nonempty_str(self.base_url, "base_url")
        _require_nonempty_str(self.model_id, "model_id")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ServingError("INVALID_ENDPOINT", "base_url must be an http(s) URL")
        if self.base_url.endswith("/"):
            raise ServingError("INVALID_ENDPOINT", "base_url must not end with '/'")
        if not isinstance(self.task, ServingTask):
            raise ServingError("INVALID_ENDPOINT", "task must be a ServingTask")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or self.timeout_s <= 0
        ):
            raise ServingError("INVALID_ENDPOINT", "timeout_s must be positive")

    def url(self, path: str) -> str:
        if not path.startswith("/"):
            raise ServingError("INVALID_ENDPOINT", "path must start with '/'")
        return f"{self.base_url}{path}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model_id": self.model_id,
            "task": self.task.value,
            "timeout_s": float(self.timeout_s),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ServingEndpoint":
        if not isinstance(value, Mapping):
            raise ServingError("INVALID_ENDPOINT", "endpoint must be a mapping")
        expected = {"name", "base_url", "model_id", "task", "timeout_s"}
        if set(value) != expected:
            raise ServingError("INVALID_ENDPOINT", "endpoint fields are incompatible")
        try:
            task = ServingTask(value["task"])
        except ValueError as error:
            raise ServingError("INVALID_ENDPOINT", "unknown serving task") from error
        return cls(
            name=value["name"],
            base_url=value["base_url"],
            model_id=value["model_id"],
            task=task,
            timeout_s=value["timeout_s"],
        )


class Transport(Protocol):
    """Minimal JSON POST transport so serving is testable without a network."""

    def post_json(self, url: str, payload: Mapping[str, Any], *, timeout_s: float) -> Any: ...
