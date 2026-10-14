import http.server
import json
import threading

import pytest

from src.serving.client import HttpTransport
from src.serving.providers import (
    DEFAULT_PROVIDERS_FILE,
    AnthropicMessagesClient,
    ProviderChatClient,
    ProviderConfig,
    load_providers,
    provider_client,
)
from src.serving.schemas import ServingError

GENERATOR_PARAMS = {"response_format": {"type": "json_object"}, "max_tokens": 2048,
                    "thinking": {"type": "disabled"}, "temperature": 0.0}
MESSAGES = [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}]
OPENAI_REPLY = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class Recorder:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post_json(self, url, payload, *, timeout_s):
        self.calls.append((url, json.loads(json.dumps(payload))))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(name, *responses, environ=None):
    transport = Recorder(*responses)
    environment = {"DEEPSEEK_API_KEY": "k", "OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k",
                   "AZURE_OPENAI_API_KEY": "k", **(environ or {})}
    built = provider_client(name, env_file="/nonexistent", environ=environment, transport=transport)
    return built, transport


def test_shipped_file_defines_valid_providers_with_deepseek_default():
    default, providers = load_providers(DEFAULT_PROVIDERS_FILE)
    assert default == "deepseek"
    assert {"deepseek", "openai", "azure-openai", "anthropic", "gemini", "qwen", "openrouter", "groq", "mistral",
            "vllm", "ollama"} <= set(providers)
    assert {config.protocol for config in providers.values()} == {"openai-chat", "anthropic-messages"}
    # Credentials are referenced by variable name only.
    assert all(config.api_key_env is None or config.api_key_env.isupper() for config in providers.values())


def test_deepseek_request_is_unchanged_from_the_measured_deployment():
    deepseek, transport = client("deepseek", OPENAI_REPLY)
    assert deepseek.generate(MESSAGES, **GENERATOR_PARAMS) == "{}"
    url, payload = transport.calls[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert payload == {"model": "deepseek-v4-flash", "messages": MESSAGES, **GENERATOR_PARAMS}


def test_openai_reasoning_model_drops_unsupported_parameters():
    openai, transport = client("openai", OPENAI_REPLY, environ={"OPENAI_MODEL": "custom-model"})
    openai.generate(MESSAGES, **GENERATOR_PARAMS)
    url, payload = transport.calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert payload["model"] == "custom-model" and payload["max_completion_tokens"] == 8192
    assert "temperature" not in payload and "thinking" not in payload and "max_tokens" not in payload
    assert payload["response_format"] == {"type": "json_object"}


def test_azure_uses_deployment_route_and_endpoint_override():
    azure, transport = client("azure-openai", OPENAI_REPLY, environ={
        "AZURE_OPENAI_ENDPOINT": "https://contoso.openai.azure.com/", "AZURE_OPENAI_DEPLOYMENT": "fin-gpt"})
    azure.generate(MESSAGES, **GENERATOR_PARAMS)
    url, payload = transport.calls[0]
    assert url == "https://contoso.openai.azure.com/openai/v1/chat/completions" and payload["model"] == "fin-gpt"


def test_anthropic_messages_payload_and_text_blocks():
    reply = {"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"status": '},
                         {"type": "text", "text": '"OK"}'}],
             "stop_reason": "end_turn",
             "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 50}}
    events = []
    anthropic, transport = client("anthropic", reply)
    assert isinstance(anthropic, AnthropicMessagesClient)
    from dataclasses import replace
    observed = replace(anthropic, observer=events.append)
    assert observed.generate(MESSAGES, **GENERATOR_PARAMS) == '{"status": "OK"}'
    url, payload = transport.calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert payload == {"model": "claude-opus-5-5", "max_tokens": 16000, "system": "rules",
                       "messages": [{"role": "user", "content": "question"}], "fallbacks": "default"}
    assert events[0]["prompt_tokens"] == 150 and events[0]["prompt_cache_hit_tokens"] == 50
    assert events[0]["completion_tokens"] == 20 and events[0]["error_code"] is None


@pytest.mark.parametrize("stop,code", [("max_tokens", "SERVING_RESPONSE_TRUNCATED"), ("refusal", "SERVING_REFUSED"),
                                       ("tool_use", "SERVING_PROTOCOL_INVALID")])
def test_anthropic_abnormal_stops_are_typed(stop, code):
    anthropic, _ = client("anthropic", {"content": [{"type": "text", "text": "{}"}], "stop_reason": stop, "usage": {}})
    with pytest.raises(ServingError) as raised:
        anthropic.generate(MESSAGES)
    assert raised.value.code == code


def test_transient_failures_retry_with_bound_and_others_do_not():
    sleeps = []
    flaky = ServingError("SERVING_TRANSPORT_FAILED", "HTTP 429", retryable=True)
    deepseek, transport = client("deepseek", flaky, flaky, OPENAI_REPLY)
    from dataclasses import replace
    assert replace(deepseek, sleep=sleeps.append).generate(MESSAGES, **GENERATOR_PARAMS) == "{}"
    assert len(transport.calls) == 3 and sleeps == [0.5, 1.0]
    deepseek, transport = client("deepseek", flaky, flaky, flaky)
    with pytest.raises(ServingError):
        replace(deepseek, sleep=lambda _: None).generate(MESSAGES, **GENERATOR_PARAMS)
    assert len(transport.calls) == 3
    denied = ServingError("SERVING_TRANSPORT_FAILED", "HTTP 401")
    deepseek, transport = client("deepseek", denied, OPENAI_REPLY)
    with pytest.raises(ServingError):
        deepseek.generate(MESSAGES, **GENERATOR_PARAMS)
    assert len(transport.calls) == 1


def test_selection_secrets_and_env_file(tmp_path):
    with pytest.raises(ServingError) as raised:
        provider_client("openai", env_file="/nonexistent", environ={}, transport=Recorder())
    assert raised.value.code == "MISSING_CONFIG" and "OPENAI_API_KEY" in str(raised.value)
    env = tmp_path / ".env"
    env.write_text("UNRELATED=x\nexport GEMINI_API_KEY='from-file'\nGEMINI_MODEL=gemini-x\n")
    gemini = provider_client(None, env_file=env, environ={"LLM_PROVIDER": "gemini"}, transport=Recorder())
    assert gemini.config.name == "gemini" and gemini.endpoint.model_id == "gemini-x"
    local = provider_client("ollama", env_file="/nonexistent", environ={}, transport=Recorder())
    assert local.endpoint.base_url == "http://127.0.0.1:11434"
    with pytest.raises(ServingError) as raised:
        provider_client("nope", env_file="/nonexistent", environ={}, transport=Recorder())
    assert raised.value.code == "PROVIDER_CONFIG_INVALID"


@pytest.mark.parametrize("entry,message", [
    ({"protocol": "grpc", "base_url": "https://x", "model": "m", "auth": "none"}, "protocol"),
    ({"protocol": "openai-chat", "base_url": "https://x", "model": "m", "auth": "none", "api_key_env": "K"},
     "api_key_env"),
    ({"protocol": "openai-chat", "base_url": "https://x", "model": "m", "auth": "none", "temprature": 0}, "unknown"),
    ({"protocol": "openai-chat", "base_url": "https://x", "model": "m", "auth": "none",
      "extra_body": {"model": "other"}}, "extra_body"),
    ({"protocol": "openai-chat", "base_url": "https://x", "model": "m", "auth": "none",
      "pricing": {"output": 1}}, "pricing"),
])
def test_invalid_entries_fail_closed(entry, message):
    with pytest.raises(ServingError) as raised:
        ProviderConfig.from_mapping("bad", entry)
    assert message in str(raised.value)


class _Echo(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib handler API
        body = self.rfile.read(int(self.headers["Content-Length"]))
        reply = json.dumps({"headers": {key.lower(): value for key, value in self.headers.items()},
                            "body": json.loads(body)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def test_http_transport_sends_configured_auth_header_to_loopback_only():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/messages"
        transport = HttpTransport(api_key="secret", auth_header="x-api-key", auth_prefix="",
                                  headers={"anthropic-version": "2023-06-01"})
        echoed = transport.post_json(url, {"a": 1}, timeout_s=5)
        assert echoed["headers"]["x-api-key"] == "secret" and "authorization" not in echoed["headers"]
        assert echoed["headers"]["anthropic-version"] == "2023-06-01" and echoed["body"] == {"a": 1}
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(ServingError) as raised:
        HttpTransport(api_key="secret").post_json("http://example.com/v1/chat/completions", {}, timeout_s=1)
    assert raised.value.code == "SERVING_AUTH_INVALID"


def test_generator_accepts_fenced_json_from_providers_without_json_mode():
    from src.text_to_pandas.generator import _json_text

    assert json.loads(_json_text('```json\n{"status": "OK"}\n```')) == {"status": "OK"}
    assert _json_text('{"status": "OK"}') == '{"status": "OK"}'


def test_chat_client_types():
    assert isinstance(client("qwen", environ={"DASHSCOPE_API_KEY": "k"})[0], ProviderChatClient)
    qwen, transport = client("qwen", OPENAI_REPLY, environ={"DASHSCOPE_API_KEY": "k"})
    qwen.generate(MESSAGES, **GENERATOR_PARAMS)
    url, payload = transport.calls[0]
    assert url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert payload["enable_thinking"] is False and "thinking" not in payload
