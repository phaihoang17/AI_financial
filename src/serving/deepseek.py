"""DeepSeek API deployment settings; credentials never enter model payloads."""

import os
from pathlib import Path
import shlex

from src.serving.client import HttpTransport, OpenAICompatibleClient
from src.serving.schemas import ServingEndpoint, ServingError, ServingTask


def deepseek_client(env_file: str | Path = ".env") -> OpenAICompatibleClient:
    settings = {}
    path = Path(env_file)
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            name, separator, raw = line.strip().partition("=")
            if not separator or name not in {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"}:
                continue
            try:
                parts = shlex.split(raw, comments=True)
            except ValueError:
                raise ServingError("MISSING_CONFIG", f"Invalid .env syntax on line {number}") from None
            if len(parts) > 1:
                raise ServingError("MISSING_CONFIG", f"Quote .env values containing spaces on line {number}")
            settings[name] = parts[0] if parts else ""
    settings.update({key: value for key, value in os.environ.items() if key.startswith("DEEPSEEK_")})
    key = settings.get("DEEPSEEK_API_KEY")
    if not key or not key.strip():
        raise ServingError("MISSING_CONFIG", "Set DEEPSEEK_API_KEY in .env or the environment")
    endpoint = ServingEndpoint(
        name="deepseek-flash-api",
        base_url=settings.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
        model_id=settings.get("DEEPSEEK_MODEL") or "deepseek-v4-flash",
        task=ServingTask.GENERATE,
        timeout_s=120.0,
    )
    return OpenAICompatibleClient(endpoint, HttpTransport(api_key=key))
