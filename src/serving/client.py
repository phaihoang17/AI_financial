"""Thin OpenAI-compatible serving client (ADR-049).

This client only marshals requests/responses to a served model. It performs no
routing, no arithmetic, and no ranking. The retrieval encoder/reranker adapters
in ``retrieval_serving`` layer the existing pinned contracts on top of it.

The client depends on an injected :class:`~src.serving.schemas.Transport`, so it
is fully testable without a network or a GPU. :class:`HttpTransport` is the
production stdlib transport and is exercised only against a live server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Sequence

from src.serving.schemas import ServingEndpoint, ServingError, ServingTask, Transport


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ServingError("SERVING_PROTOCOL_INVALID", "response must be a JSON object")
    return value


def _ordered_data(response: Any) -> List[Mapping[str, Any]]:
    """Return an OpenAI ``data`` array ordered by its ``index`` field."""

    body = _as_mapping(response)
    data = body.get("data")
    if not isinstance(data, list) or not data:
        raise ServingError("SERVING_PROTOCOL_INVALID", "response is missing a data array")
    items: List[Mapping[str, Any]] = []
    for entry in data:
        items.append(_as_mapping(entry))
    if all("index" in entry for entry in items):
        indexes = [entry["index"] for entry in items]
        if any(
            isinstance(index, bool) or not isinstance(index, int) for index in indexes
        ) or sorted(indexes) != list(range(len(items))):
            raise ServingError("SERVING_PROTOCOL_INVALID", "response indexes are invalid")
        items = [entry for _, entry in sorted(zip(indexes, items), key=lambda pair: pair[0])]
    return items


@dataclass(frozen=True)
class OpenAICompatibleClient:
    """One client bound to one served endpoint."""

    endpoint: ServingEndpoint
    transport: Transport

    def _post(self, path: str, payload: Mapping[str, Any]) -> Any:
        try:
            return self.transport.post_json(
                self.endpoint.url(path), payload, timeout_s=self.endpoint.timeout_s
            )
        except ServingError:
            raise
        except Exception as error:  # transport-level failure
            raise ServingError("SERVING_TRANSPORT_FAILED", str(error)) from error

    def _require_task(self, task: ServingTask) -> None:
        if self.endpoint.task is not task:
            raise ServingError(
                "SERVING_TASK_MISMATCH",
                f"endpoint task {self.endpoint.task.value} cannot serve {task.value}",
            )

    def generate(self, messages: Sequence[Mapping[str, str]], **params: Any) -> str:
        """Call the chat-completions API and return the message content."""

        self._require_task(ServingTask.GENERATE)
        if not isinstance(messages, (list, tuple)) or not messages:
            raise ServingError("SERVING_INPUT_INVALID", "messages must be non-empty")
        payload = {"model": self.endpoint.model_id, "messages": list(messages), **params}
        body = _as_mapping(self._post("/v1/chat/completions", payload))
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ServingError("SERVING_PROTOCOL_INVALID", "response is missing choices")
        message = _as_mapping(choices[0]).get("message")
        content = _as_mapping(message).get("content") if message is not None else None
        if not isinstance(content, str):
            raise ServingError("SERVING_PROTOCOL_INVALID", "choice is missing message content")
        return content

    def embed(self, inputs: Sequence[str]) -> List[List[float]]:
        """Call the embeddings API and return raw (un-normalized) vectors in order."""

        self._require_task(ServingTask.EMBED)
        texts = list(inputs)
        if not texts or any(not isinstance(text, str) for text in texts):
            raise ServingError("SERVING_INPUT_INVALID", "inputs must be non-empty strings")
        payload = {"model": self.endpoint.model_id, "input": texts}
        items = _ordered_data(self._post("/v1/embeddings", payload))
        if len(items) != len(texts):
            raise ServingError("SERVING_PROTOCOL_INVALID", "embedding count does not match inputs")
        vectors: List[List[float]] = []
        for item in items:
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise ServingError("SERVING_PROTOCOL_INVALID", "embedding vector is invalid")
            try:
                vectors.append([float(value) for value in vector])
            except (TypeError, ValueError) as error:
                raise ServingError("SERVING_PROTOCOL_INVALID", "embedding is not numeric") from error
        return vectors

    def score(self, query: str, documents: Sequence[str]) -> List[float]:
        """Call the cross-encoder score API and return raw scores in order."""

        self._require_task(ServingTask.SCORE)
        docs = list(documents)
        if not isinstance(query, str) or not docs or any(not isinstance(d, str) for d in docs):
            raise ServingError("SERVING_INPUT_INVALID", "query and documents must be strings")
        payload = {"model": self.endpoint.model_id, "text_1": query, "text_2": docs}
        items = _ordered_data(self._post("/score", payload))
        if len(items) != len(docs):
            raise ServingError("SERVING_PROTOCOL_INVALID", "score count does not match documents")
        scores: List[float] = []
        for item in items:
            value = item.get("score")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ServingError("SERVING_PROTOCOL_INVALID", "score is not numeric")
            scores.append(float(value))
        return scores

    def count_tokens(self, text: str) -> int:
        """Count tokens of a single input via the tokenize API (no truncation)."""

        if not isinstance(text, str):
            raise ServingError("SERVING_INPUT_INVALID", "text must be a string")
        payload = {"model": self.endpoint.model_id, "prompt": text, "add_special_tokens": True}
        return _read_count(self._post("/tokenize", payload))

    def count_pair_tokens(self, query: str, document: str) -> int:
        """Count tokens of a cross-encoder pair via the tokenize API (no truncation)."""

        if not isinstance(query, str) or not isinstance(document, str):
            raise ServingError("SERVING_INPUT_INVALID", "query and document must be strings")
        payload = {
            "model": self.endpoint.model_id,
            "text_1": query,
            "text_2": document,
            "add_special_tokens": True,
        }
        return _read_count(self._post("/tokenize", payload))


def _read_count(response: Any) -> int:
    body = _as_mapping(response)
    count = body.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ServingError("SERVING_PROTOCOL_INVALID", "tokenize did not return a count")
    return count


class HttpTransport:
    """Production stdlib JSON transport. Exercised only against a live server."""

    def post_json(
        self, url: str, payload: Mapping[str, Any], *, timeout_s: float
    ) -> Any:  # pragma: no cover - requires a live server
        import json
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        data = json.dumps(dict(payload)).encode("utf-8")
        request = Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise ServingError("SERVING_TRANSPORT_FAILED", f"HTTP {error.code}") from error
        except URLError as error:
            raise ServingError("SERVING_TRANSPORT_FAILED", str(error.reason)) from error
        except json.JSONDecodeError as error:
            raise ServingError("SERVING_PROTOCOL_INVALID", "response was not JSON") from error
