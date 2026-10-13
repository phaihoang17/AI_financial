import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.serving.client import HttpTransport
from src.serving.deepseek import deepseek_client
from src.serving.schemas import ServingError


def test_env_file_is_data_and_environment_overrides(tmp_path):
    path = tmp_path / ".env"
    path.write_text('DEEPSEEK_API_KEY="test-key"\nDEEPSEEK_MODEL=deepseek-v4-flash\nUNRELATED=$(false)\n')
    with patch.dict(os.environ, {"DEEPSEEK_MODEL": "override"}, clear=True):
        client = deepseek_client(path)
    assert client.endpoint.model_id == "override"
    assert client.endpoint.base_url == "https://api.deepseek.com"


def test_empty_key_fails_before_network(tmp_path):
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY=\n")
    with patch.dict(os.environ, {}, clear=True), pytest.raises(ServingError) as error:
        deepseek_client(path)
    assert error.value.code == "MISSING_CONFIG"


def test_bearer_header_is_separate_from_payload_and_redirects_blocked():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"choices": []}'
    opener = MagicMock()
    opener.open.return_value = response
    with patch("urllib.request.build_opener", return_value=opener) as build:
        HttpTransport(api_key="private-key").post_json(
            "https://api.deepseek.com/v1/chat/completions", {"model": "deepseek-v4-flash"}, timeout_s=1,
        )
    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer private-key"
    assert "private-key" not in json.dumps(json.loads(request.data))
    assert build.call_args.args[0].redirect_request(None, None, 302, "", {}, "https://other.host") is None


def test_key_is_not_sent_over_http():
    with patch("urllib.request.build_opener") as build, pytest.raises(ServingError):
        HttpTransport(api_key="private-key").post_json("http://api.deepseek.com", {}, timeout_s=1)
    build.assert_not_called()
