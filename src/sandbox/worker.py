"""Minimal stdin/stdout entrypoint for the trusted M7 DSL worker."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from src.sandbox.interpreter import failure_result, interpret_execution_request
from src.sandbox.limits import (
    M7_LIMITS_V1,
    ResourceLimitFailureCode,
    WorkerResourceViolation,
    apply_worker_resource_limits,
    resource_failure,
)
from src.sandbox.policy import ExecutionPolicyError, validate_execution_request
from src.sandbox.protocol import (
    MAX_PROTOCOL_REQUEST_BYTES,
    MAX_PROTOCOL_RESPONSE_BYTES,
    ProtocolError,
    decode_canonical_json,
    encode_canonical_json,
)
from src.sandbox.schemas import ExecutionFailure, ExecutionFailureStage
from src.sandbox.security import (
    WorkerSecurityViolation,
    install_worker_security_guards,
    validate_worker_environment,
)


def _program_id(value: object) -> str:
    if isinstance(value, dict):
        program = value.get("program")
        if isinstance(program, dict):
            candidate = program.get("program_id")
            if isinstance(candidate, str) and candidate:
                return candidate
    return "unavailable"


def _protocol_failure(message: str, *, program_id: str = "unavailable"):
    return failure_result(
        program_id,
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
    except ProtocolError as error:
        if error.code.value == "PAYLOAD_TOO_LARGE":
            failure = resource_failure(
                ResourceLimitFailureCode.REQUEST_SIZE_LIMIT,
                "worker request exceeded the canonical request limit",
            )
            result = failure_result("unavailable", failure, execution_ms=0)
        else:
            result = _protocol_failure("worker received malformed protocol data")
    else:
        program_id = _program_id(request)
        project_root = Path(__file__).resolve().parents[2]
        try:
            validate_worker_environment(os.environ, project_root)
            apply_worker_resource_limits(M7_LIMITS_V1)
            # Force all deterministic validator dependencies to load before the
            # runtime filesystem guard closes the worker's read surface. The
            # interpreter immediately performs the same validation again.
            validate_execution_request(request)
            install_worker_security_guards()
            result = interpret_execution_request(request)
        except ExecutionPolicyError as error:
            result = failure_result(program_id, error.failure, execution_ms=0)
        except WorkerResourceViolation as error:
            result = failure_result(program_id, error.failure, execution_ms=0)
        except WorkerSecurityViolation as error:
            result = failure_result(program_id, error.failure, execution_ms=0)
        except MemoryError:
            result = failure_result(
                program_id,
                resource_failure(
                    ResourceLimitFailureCode.MEMORY_LIMIT,
                    "worker exhausted its memory budget",
                ),
                execution_ms=0,
            )

    try:
        response = encode_canonical_json(
            result.to_dict(), max_bytes=MAX_PROTOCOL_RESPONSE_BYTES
        )
    except ProtocolError:
        output_failure = failure_result(
            result.program_id,
            resource_failure(
                ResourceLimitFailureCode.OUTPUT_SIZE_LIMIT,
                "worker result exceeded the canonical output limit",
            ),
            execution_ms=result.execution_ms,
        )
        response = encode_canonical_json(
            output_failure.to_dict(),
            max_bytes=MAX_PROTOCOL_RESPONSE_BYTES,
        )
    sys.stdout.buffer.write(response)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
