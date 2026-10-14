"""Thin OpenAI-compatible serving client (ADR-049).

This client only marshals requests/responses to a served model. It performs no
routing, no arithmetic, and no ranking. The retrieval encoder/reranker adapters
in ``retrieval_serving`` layer the existing pinned contracts on top of it.

The client depends on an injected :class:`~src.serving.schemas.Transport`, so it
is fully testable without a network or a GPU. :class:`HttpTransport` is the
production stdlib transport and is exercised only against a live server.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
import time
from typing import Any

from src.serving.schemas import ServingEndpoint, ServingError, ServingTask, Transport


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ServingError("SERVING_PROTOCOL_INVALID", "response must be a JSON object")
    return value


def _ordered_data(response: Any) -> list[Mapping[str, Any]]:
    """Return an OpenAI ``data`` array ordered by its ``index`` field."""

    body = _as_mapping(response)
    data = body.get("data")
    if not isinstance(data, list) or not data:
        raise ServingError("SERVING_PROTOCOL_INVALID", "response is missing a data array")
    items: list[Mapping[str, Any]] = []
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
    observer: Callable[[Mapping[str, Any]], None] | None = None
    #: Chat route under ``base_url``; providers such as Gemini or DashScope
    #: serve the OpenAI-compatible API under a different prefix.
    chat_path: str = "/v1/chat/completions"

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
        started = time.monotonic()
        body = _as_mapping(self._post(self.chat_path, payload))
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ServingError("SERVING_PROTOCOL_INVALID", "response is missing choices")
        choice = _as_mapping(choices[0])
        finish_reason = choice.get("finish_reason")
        error_code = "SERVING_RESPONSE_TRUNCATED" if finish_reason == "length" else None
        logging.getLogger(__name__).info(
            "generation model=%s finish_reason=%s usage=%s error_code=%s",
            self.endpoint.model_id, finish_reason, body.get("usage"), error_code,
        )
        if self.observer is not None:
            usage = body.get("usage")
            usage = usage if isinstance(usage, Mapping) else {}
            try:
                self.observer({
                    "model_id": self.endpoint.model_id,
                    "thinking": params.get("thinking"), "finish_reason": finish_reason,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
                    "latency_ms": (time.monotonic() - started) * 1000,
                    "error_code": error_code,
                })
            except Exception:
                logging.getLogger(__name__).warning("generation observer failed")
        if error_code:
            raise ServingError(error_code, "generation reached the completion token limit")
        if finish_reason not in (None, "stop"):
            raise ServingError("SERVING_PROTOCOL_INVALID", "generation did not finish normally")
        message = choice.get("message")
        content = _as_mapping(message).get("content") if message is not None else None
        if not isinstance(content, str):
            raise ServingError("SERVING_PROTOCOL_INVALID", "choice is missing message content")
        return content

    def embed(self, inputs: Sequence[str]) -> list[list[float]]:
        """Call the embeddings API and return raw (un-normalized) vectors in order."""

        self._require_task(ServingTask.EMBED)
        texts = list(inputs)
        if not texts or any(not isinstance(text, str) for text in texts):
            raise ServingError("SERVING_INPUT_INVALID", "inputs must be non-empty strings")
        payload = {"model": self.endpoint.model_id, "input": texts}
        items = _ordered_data(self._post("/v1/embeddings", payload))
        if len(items) != len(texts):
            raise ServingError("SERVING_PROTOCOL_INVALID", "embedding count does not match inputs")
        vectors: list[list[float]] = []
        for item in items:
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise ServingError("SERVING_PROTOCOL_INVALID", "embedding vector is invalid")
            try:
                vectors.append([float(value) for value in vector])
            except (TypeError, ValueError) as error:
                raise ServingError("SERVING_PROTOCOL_INVALID", "embedding is not numeric") from error
        return vectors

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """Call the cross-encoder score API and return raw scores in order."""

        self._require_task(ServingTask.SCORE)
        docs = list(documents)
        if not isinstance(query, str) or not docs or any(not isinstance(d, str) for d in docs):
            raise ServingError("SERVING_INPUT_INVALID", "query and documents must be strings")
        payload = {"model": self.endpoint.model_id, "text_1": query, "text_2": docs}
        items = _ordered_data(self._post("/score", payload))
        if len(items) != len(docs):
            raise ServingError("SERVING_PROTOCOL_INVALID", "score count does not match documents")
        scores: list[float] = []
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


#: HTTP statuses a bounded retry may repeat: timeout, conflict, rate limit,
#: server errors and provider overload (529).
RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _single_line(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ServingError("SERVING_AUTH_INVALID", f"{what} must be a nonempty single line")
    return value


class HttpTransport:
    """Production stdlib JSON transport. Exercised only against a live server.

    The credential goes in ``auth_header`` (``Authorization: Bearer <key>`` by
    default; ``x-api-key`` for Anthropic, ``api-key`` for Azure OpenAI). It is
    sent only over HTTPS, or plain HTTP to a loopback host, never across a
    redirect. ``headers`` are fixed, non-secret extras such as API versions.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer ",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if api_key is not None:
            _single_line(api_key, "API key")
        _single_line(auth_header, "auth header")
        self._headers = {
            _single_line(name, "header name"): _single_line(value, "header value")
            for name, value in (headers or {}).items()
        }
        self._api_key = api_key
        self._auth_header, self._auth_prefix = auth_header, auth_prefix

    def post_json(
        self, url: str, payload: Mapping[str, Any], *, timeout_s: float
    ) -> Any:  # pragma: no cover - requires a live server
        import json
        from urllib.error import HTTPError, URLError
        from urllib.parse import urlsplit
        from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

        data = json.dumps(dict(payload)).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._headers}
        send = urlopen
        if self._api_key is not None:
            parsed = urlsplit(url)
            secure = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS)
            if not secure or parsed.username is not None or parsed.password is not None:
                raise ServingError("SERVING_AUTH_INVALID", "Authenticated requests require HTTPS without URL credentials")
            headers[self._auth_header] = f"{self._auth_prefix}{self._api_key}"

            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, response_headers, newurl):
                    return None

            # Keep bearer credentials on the explicitly configured endpoint.
            send = build_opener(NoRedirect()).open
        request = Request(url, data=data, headers=headers)
        try:
            with send(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise ServingError("SERVING_TRANSPORT_FAILED", f"HTTP {error.code}",
                               retryable=error.code in RETRYABLE_HTTP_STATUSES) from error
        except URLError as error:
            raise ServingError("SERVING_TRANSPORT_FAILED", str(error.reason), retryable=True) from error
        except TimeoutError as error:
            raise ServingError("SERVING_TRANSPORT_FAILED", "request timed out", retryable=True) from error
        except json.JSONDecodeError as error:
            raise ServingError("SERVING_PROTOCOL_INVALID", "response was not JSON") from error
