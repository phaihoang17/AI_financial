"""Bounded canonical-JSON protocol shared by the M7 parent and worker."""

from __future__ import annotations

from enum import Enum
import json
from typing import Any

from src.programmer.schemas import canonical_json
from src.sandbox.limits import M7_LIMITS_V1


MAX_PROTOCOL_REQUEST_BYTES = M7_LIMITS_V1.max_request_bytes
MAX_PROTOCOL_RESPONSE_BYTES = M7_LIMITS_V1.max_output_bytes


class ProtocolFailureCode(str, Enum):
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    INVALID_UTF8 = "INVALID_UTF8"
    MALFORMED_JSON = "MALFORMED_JSON"
    NON_CANONICAL_JSON = "NON_CANONICAL_JSON"


class ProtocolError(ValueError):
    def __init__(self, code: ProtocolFailureCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


def encode_canonical_json(value: Any, *, max_bytes: int) -> bytes:
    encoded = canonical_json(value).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ProtocolError(
            ProtocolFailureCode.PAYLOAD_TOO_LARGE,
            f"canonical JSON exceeds {max_bytes} bytes",
        )
    return encoded


def decode_canonical_json(payload: bytes, *, max_bytes: int) -> Any:
    if not isinstance(payload, bytes):
        raise ProtocolError(
            ProtocolFailureCode.MALFORMED_JSON,
            "protocol payload must be bytes",
        )
    if len(payload) > max_bytes:
        raise ProtocolError(
            ProtocolFailureCode.PAYLOAD_TOO_LARGE,
            f"protocol payload exceeds {max_bytes} bytes",
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(
            ProtocolFailureCode.INVALID_UTF8,
            "protocol payload is not UTF-8",
        ) from error
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError) as error:
        raise ProtocolError(
            ProtocolFailureCode.MALFORMED_JSON,
            "protocol payload is not valid JSON",
        ) from error
    try:
        canonical = canonical_json(value).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolError(
            ProtocolFailureCode.MALFORMED_JSON,
            "protocol JSON cannot be canonicalized",
        ) from error
    if canonical != payload:
        raise ProtocolError(
            ProtocolFailureCode.NON_CANONICAL_JSON,
            "protocol payload is not canonical JSON",
        )
    return value
