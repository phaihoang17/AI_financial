"""Configurable chat-model providers for the Pandas code generator (ADR-072).

One TOML file (``deploy/llm/providers.toml``) names every supported provider:
the wire protocol, endpoint, model, credential variable and the request
parameters that provider accepts. The generator keeps one request shape (the
DeepSeek-style ``response_format`` / ``max_tokens`` / ``thinking`` /
``temperature`` parameters); the client drops or renames what a provider does
not accept, so switching provider never touches pipeline code.

Two protocols cover the supported providers:

- ``openai-chat``: OpenAI-compatible ``chat/completions`` (DeepSeek, OpenAI,
  Azure OpenAI, Gemini, Qwen/DashScope, OpenRouter, Groq, Mistral, vLLM, Ollama);
- ``anthropic-messages``: the Anthropic Messages API.

Credentials come only from the environment or a ``.env`` file, never from the
TOML file, and never enter model payloads, reports or error messages.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import time
import tomllib
from typing import Any

from src.serving.client import HttpTransport, OpenAICompatibleClient
from src.serving.schemas import ServingEndpoint, ServingError, ServingTask, Transport

DEFAULT_PROVIDERS_FILE = Path(__file__).resolve().parents[2] / "deploy" / "llm" / "providers.toml"
PROVIDER_ENV = "LLM_PROVIDER"
PROTOCOLS = ("openai-chat", "anthropic-messages")
_AUTH = {"bearer": ("Authorization", "Bearer "), "x-api-key": ("x-api-key", ""), "api-key": ("api-key", "")}
_FIELDS = {
    "protocol", "base_url", "chat_path", "model", "api_key_env", "model_env", "base_url_env", "auth",
    "json_mode", "thinking", "send_temperature", "max_tokens", "max_tokens_field", "timeout_s", "retries",
    "headers", "extra_body", "pricing",
}
_PRICING = {"cache_miss", "cache_hit", "output"}


def _config_error(message: str) -> ServingError:
    return ServingError("PROVIDER_CONFIG_INVALID", message)


@dataclass(frozen=True)
class ProviderConfig:
    """One provider entry, validated; it holds names of secrets, never secrets."""

    name: str
    protocol: str
    base_url: str
    model: str
    chat_path: str = "/v1/chat/completions"
    api_key_env: str | None = None
    model_env: str | None = None
    base_url_env: str | None = None
    auth: str = "bearer"
    json_mode: bool = True
    thinking: str = "none"
    send_temperature: bool = True
    max_tokens: int | None = None
    max_tokens_field: str = "max_tokens"
    timeout_s: float = 120.0
    retries: int = 2
    headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    pricing: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise _config_error(f"{self.name}: protocol must be one of {', '.join(PROTOCOLS)}")
        if self.auth not in (*_AUTH, "none"):
            raise _config_error(f"{self.name}: auth must be bearer, x-api-key, api-key or none")
        if (self.auth == "none") != (self.api_key_env is None):
            raise _config_error(f"{self.name}: api_key_env is required exactly when auth is not none")
        if self.thinking not in ("none", "deepseek"):
            raise _config_error(f"{self.name}: thinking must be none or deepseek")
        if self.max_tokens_field not in ("max_tokens", "max_completion_tokens"):
            raise _config_error(f"{self.name}: max_tokens_field must be max_tokens or max_completion_tokens")
        if not self.chat_path.startswith("/"):
            raise _config_error(f"{self.name}: chat_path must start with '/'")
        if self.max_tokens is not None and (type(self.max_tokens) is not int or self.max_tokens < 1):
            raise _config_error(f"{self.name}: max_tokens must be a positive integer")
        if type(self.retries) is not int or not 0 <= self.retries <= 5:
            raise _config_error(f"{self.name}: retries must be an integer from 0 to 5")
        if self.pricing is not None and (set(self.pricing) != _PRICING or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                for value in self.pricing.values())):
            raise _config_error(f"{self.name}: pricing needs non-negative cache_miss, cache_hit and output")
        for key in ("model", "messages"):
            if key in self.extra_body:
                raise _config_error(f"{self.name}: extra_body must not set {key}")

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any]) -> ProviderConfig:
        if not isinstance(value, Mapping):
            raise _config_error(f"{name}: provider entry must be a table")
        unknown = set(value) - _FIELDS
        if unknown:
            raise _config_error(f"{name}: unknown keys {sorted(unknown)}")
        missing = {"protocol", "base_url", "model"} - set(value)
        if missing:
            raise _config_error(f"{name}: missing keys {sorted(missing)}")
        return cls(name=name, **dict(value))


def load_providers(path: str | Path = DEFAULT_PROVIDERS_FILE) -> tuple[str, dict[str, ProviderConfig]]:
    """(default provider name, providers by name) from one TOML file."""
    try:
        document = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise _config_error(f"cannot read {path}: {error}") from None
    entries = document.get("providers")
    if not isinstance(entries, Mapping) or not entries:
        raise _config_error("the file must define at least one [providers.<name>] table")
    providers = {name: ProviderConfig.from_mapping(name, entry) for name, entry in entries.items()}
    default = document.get("default")
    if default not in providers:
        raise _config_error("default must name a defined provider")
    return default, providers


def read_env_file(path: str | Path, names: Sequence[str]) -> dict[str, str]:
    """Only the requested ``NAME=value`` lines of a ``.env`` file."""
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.is_file():
        return values
    wanted = set(names)
    for number, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        name, separator, raw = line.strip().removeprefix("export ").partition("=")
        if not separator or name not in wanted:
            continue
        try:
            parts = shlex.split(raw, comments=True)
        except ValueError:
            raise ServingError("MISSING_CONFIG", f"Invalid .env syntax on line {number}") from None
        if len(parts) > 1:
            raise ServingError("MISSING_CONFIG", f"Quote .env values containing spaces on line {number}")
        values[name] = parts[0] if parts else ""
    return values


Observer = Callable[[Mapping[str, Any]], None] | None


def _with_retries(config: ProviderConfig, call: Callable[[], Any], sleep: Callable[[float], None]) -> Any:
    for attempt in range(config.retries + 1):
        try:
            return call()
        except ServingError as error:
            if not error.retryable or attempt == config.retries:
                raise
            sleep(min(8.0, 0.5 * 2 ** attempt))
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class ProviderChatClient:
    """An ``openai-chat`` provider: the shared client plus per-provider parameters."""

    config: ProviderConfig
    endpoint: ServingEndpoint
    transport: Transport
    observer: Observer = None
    sleep: Callable[[float], None] = time.sleep

    def request_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.config.json_mode and "response_format" in params:
            out["response_format"] = params["response_format"]
        if self.config.thinking == "deepseek" and "thinking" in params:
            out["thinking"] = params["thinking"]
        if self.config.send_temperature and "temperature" in params:
            out["temperature"] = params["temperature"]
        limit = self.config.max_tokens or params.get("max_tokens")
        if limit is not None:
            out[self.config.max_tokens_field] = limit
        out.update(self.config.extra_body)
        return out

    def generate(self, messages: Sequence[Mapping[str, str]], **params: Any) -> str:
        client = OpenAICompatibleClient(self.endpoint, self.transport, self.observer, chat_path=self.config.chat_path)
        translated = self.request_params(params)
        return _with_retries(self.config, lambda: client.generate(messages, **translated), self.sleep)


@dataclass(frozen=True)
class AnthropicMessagesClient:
    """An ``anthropic-messages`` provider (``POST /v1/messages``).

    System prompts move to the top-level ``system`` field. Sampling and
    thinking parameters are sent only when the provider entry allows them,
    because current Claude models reject ``temperature`` and disabled thinking.
    """

    config: ProviderConfig
    endpoint: ServingEndpoint
    transport: Transport
    observer: Observer = None
    sleep: Callable[[float], None] = time.sleep

    def request_payload(self, messages: Sequence[Mapping[str, str]], params: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(messages, (list, tuple)) or not messages:
            raise ServingError("SERVING_INPUT_INVALID", "messages must be non-empty")
        system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
        turns = [{"role": message["role"], "content": message["content"]}
                 for message in messages if message["role"] != "system"]
        if not turns:
            raise ServingError("SERVING_INPUT_INVALID", "at least one user message is required")
        payload: dict[str, Any] = {"model": self.endpoint.model_id,
                                   "max_tokens": self.config.max_tokens or params.get("max_tokens") or 4096,
                                   "messages": turns}
        if system:
            payload["system"] = system
        if self.config.send_temperature and "temperature" in params:
            payload["temperature"] = params["temperature"]
        payload.update(self.config.extra_body)
        return payload

    def generate(self, messages: Sequence[Mapping[str, str]], **params: Any) -> str:
        payload = self.request_payload(messages, params)

        def call() -> Any:
            try:
                return self.transport.post_json(self.endpoint.url(self.config.chat_path), payload,
                                                timeout_s=self.endpoint.timeout_s)
            except ServingError:
                raise
            except Exception as error:
                raise ServingError("SERVING_TRANSPORT_FAILED", type(error).__name__) from error

        started = time.monotonic()
        body = _with_retries(self.config, call, self.sleep)
        if not isinstance(body, Mapping):
            raise ServingError("SERVING_PROTOCOL_INVALID", "response must be a JSON object")
        stop = body.get("stop_reason")
        error_code = {"max_tokens": "SERVING_RESPONSE_TRUNCATED", "refusal": "SERVING_REFUSED"}.get(stop)
        usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else {}
        if self.observer is not None:
            cache_read = usage.get("cache_read_input_tokens") or 0
            uncached = usage.get("input_tokens")
            prompt = None if uncached is None else uncached + cache_read + (usage.get("cache_creation_input_tokens") or 0)
            try:
                self.observer({
                    "model_id": self.endpoint.model_id, "thinking": None, "finish_reason": stop,
                    "prompt_tokens": prompt, "completion_tokens": usage.get("output_tokens"),
                    "prompt_cache_hit_tokens": cache_read, "latency_ms": (time.monotonic() - started) * 1000,
                    "error_code": error_code,
                })
            except Exception:
                pass
        if error_code == "SERVING_RESPONSE_TRUNCATED":
            raise ServingError(error_code, "generation reached the completion token limit")
        if error_code:
            raise ServingError(error_code, "the provider declined the request")
        if stop not in ("end_turn", "stop_sequence"):
            raise ServingError("SERVING_PROTOCOL_INVALID", "generation did not finish normally")
        content = body.get("content")
        if not isinstance(content, list):
            raise ServingError("SERVING_PROTOCOL_INVALID", "response is missing content")
        text = "".join(block.get("text", "") for block in content
                       if isinstance(block, Mapping) and block.get("type") == "text")
        if not text:
            raise ServingError("SERVING_PROTOCOL_INVALID", "response has no text block")
        return text


def provider_client(
    name: str | None = None,
    *,
    config_path: str | Path = DEFAULT_PROVIDERS_FILE,
    env_file: str | Path = ".env",
    environ: Mapping[str, str] | None = None,
    transport: Transport | None = None,
) -> ProviderChatClient | AnthropicMessagesClient:
    """The chat client of one configured provider (``name`` > ``LLM_PROVIDER`` > file default)."""
    environment = os.environ if environ is None else environ
    default, providers = load_providers(config_path)
    selected = name or environment.get(PROVIDER_ENV) or default
    if selected not in providers:
        raise _config_error(f"unknown provider {selected!r}; configured: {', '.join(sorted(providers))}")
    config = providers[selected]
    names = [item for item in (config.api_key_env, config.model_env, config.base_url_env) if item]
    settings = read_env_file(env_file, names)
    settings.update({item: environment[item] for item in names if item in environment})
    key = settings.get(config.api_key_env) if config.api_key_env else None
    if config.api_key_env and (not key or not key.strip()):
        raise ServingError("MISSING_CONFIG", f"Set {config.api_key_env} in .env or the environment")
    model = (settings.get(config.model_env) if config.model_env else None) or config.model
    base_url = (settings.get(config.base_url_env) if config.base_url_env else None) or config.base_url
    endpoint = ServingEndpoint(name=f"{selected}-chat", base_url=base_url.rstrip("/"), model_id=model,
                               task=ServingTask.GENERATE, timeout_s=float(config.timeout_s))
    if transport is None:
        header, prefix = _AUTH.get(config.auth, ("Authorization", "Bearer "))
        transport = HttpTransport(api_key=key, auth_header=header, auth_prefix=prefix, headers=config.headers)
    if config.protocol == "anthropic-messages":
        return AnthropicMessagesClient(config, endpoint, transport)
    return ProviderChatClient(config, endpoint, transport)


def provider_report(client: ProviderChatClient | AnthropicMessagesClient) -> dict[str, Any]:
    """Non-secret provider identity recorded in run reports."""
    return {"name": client.config.name, "protocol": client.config.protocol, "model_id": client.endpoint.model_id,
            "base_url": client.endpoint.base_url,
            "pricing": dict(client.config.pricing) if client.config.pricing else None}


__all__ = [
    "AnthropicMessagesClient", "DEFAULT_PROVIDERS_FILE", "PROVIDER_ENV", "ProviderChatClient", "ProviderConfig",
    "load_providers", "provider_client", "provider_report", "read_env_file",
]
