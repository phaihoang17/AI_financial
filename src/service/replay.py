"""Replay of recorded model programs for drills and canaries (ADR-072).

A live run keeps every request's attempts under its history root. ``ReplayClient``
answers a generation request with the program the real model produced for the
same question in that run, so reliability drills and release canaries exercise
the whole pipeline (understanding, validation, sandbox, verification) without
model calls or cost. It never invents code: an unknown question raises the same
typed transport error a provider outage would.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from src.serving.schemas import ServingEndpoint, ServingError, ServingTask


def load_programs(history_root: str | Path) -> Dict[str, Dict[str, Any]]:
    """question -> final generated program envelope, from one run's history."""
    programs: Dict[str, Dict[str, Any]] = {}
    for response_file in sorted(Path(history_root).glob("*/response.json")):
        response = json.loads(response_file.read_text(encoding="utf-8"))
        attempts = [attempt for attempt in response.get("attempts", []) if attempt.get("program")]
        if not attempts:
            continue
        passed = [attempt for attempt in attempts if attempt.get("outcome") == "PASSED"]
        programs[response["question"]] = (passed or attempts)[-1]["program"]
    return programs


@dataclass(frozen=True)
class ReplayClient:
    programs: Mapping[str, Mapping[str, Any]]
    endpoint: ServingEndpoint = field(default_factory=lambda: ServingEndpoint(
        "replay", "http://127.0.0.1:1", "replay", ServingTask.GENERATE, 1.0))
    observer: Optional[Callable[[Mapping[str, Any]], None]] = None

    def generate(self, messages: Sequence[Mapping[str, str]], **params: Any) -> str:
        question = json.loads(messages[-1]["content"])["question"]
        program = self.programs.get(question)
        if program is None:
            raise ServingError("SERVING_TRANSPORT_FAILED", "no recorded program for this question")
        if self.observer is not None:
            self.observer({"model_id": "replay", "thinking": None, "finish_reason": "stop", "prompt_tokens": 0,
                           "completion_tokens": 0, "prompt_cache_hit_tokens": 0, "latency_ms": 0.0,
                           "error_code": None})
        return json.dumps(dict(program), ensure_ascii=False)


@dataclass(frozen=True)
class FailingClient:
    """A provider outage: every generation fails with a typed transport error."""

    endpoint: ServingEndpoint = field(default_factory=lambda: ServingEndpoint(
        "outage", "http://127.0.0.1:1", "outage", ServingTask.GENERATE, 1.0))
    observer: Optional[Callable[[Mapping[str, Any]], None]] = None

    def generate(self, messages: Sequence[Mapping[str, str]], **params: Any) -> str:
        raise ServingError("SERVING_TRANSPORT_FAILED", "provider unavailable")
