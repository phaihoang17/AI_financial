"""Minimal stdin/stdout entrypoint for the trusted M7 DSL worker."""

from __future__ import annotations

import sys

from src.sandbox.interpreter import failure_result, interpret_execution_request
from src.sandbox.protocol import (
    MAX_PROTOCOL_REQUEST_BYTES,
    MAX_PROTOCOL_RESPONSE_BYTES,
    ProtocolError,
    decode_canonical_json,
    encode_canonical_json,
)
from src.sandbox.schemas import ExecutionFailure, ExecutionFailureStage


def _protocol_failure(message: str):
    return failure_result(
        "unavailable",
        ExecutionFailure(
            stage=ExecutionFailureStage.INFRASTRUCTURE,
            code="MALFORMED_WORKER_PROTOCOL",
            message=message,
        ),
        execution_ms=0,
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_PROTOCOL_REQUEST_BYTES + 1)
    try:
        request = decode_canonical_json(
            payload, max_bytes=MAX_PROTOCOL_REQUEST_BYTES
        )
    except ProtocolError:
        result = _protocol_failure("worker received malformed protocol data")
    else:
        result = interpret_execution_request(request)

    try:
        response = encode_canonical_json(
            result.to_dict(), max_bytes=MAX_PROTOCOL_RESPONSE_BYTES
        )
    except ProtocolError:
        response = encode_canonical_json(
            _protocol_failure("worker result exceeded the protocol boundary").to_dict(),
            max_bytes=MAX_PROTOCOL_RESPONSE_BYTES,
        )
    sys.stdout.buffer.write(response)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
