"""Fixed subprocess probes for TASK-072/TASK-074 tests only."""

from __future__ import annotations

import os
from pathlib import Path
import resource
import socket
import subprocess
import sys
import time

from src.programmer.schemas import canonical_json
from src.sandbox.interpreter import failure_result
from src.sandbox.limits import (
    WorkerResourceViolation,
    apply_worker_resource_limits,
)
from src.sandbox.protocol import MAX_PROTOCOL_REQUEST_BYTES, decode_canonical_json
from src.sandbox.security import (
    SecurityFailureCode,
    WorkerSecurityViolation,
    install_worker_security_guards,
    security_failure,
    validate_worker_environment,
)


def _read_request() -> tuple[dict[str, object], str]:
    payload = sys.stdin.buffer.read(MAX_PROTOCOL_REQUEST_BYTES + 1)
    request = decode_canonical_json(payload, max_bytes=MAX_PROTOCOL_REQUEST_BYTES)
    program = request.get("program", {})
    program_id = program.get("program_id", "unavailable")
    return request, program_id


def _write_failure(program_id: str, failure) -> None:
    result = failure_result(program_id, failure, execution_ms=0)
    sys.stdout.buffer.write(canonical_json(result.to_dict()).encode("utf-8"))
    sys.stdout.buffer.flush()


def _security_probe(mode: str, program_id: str) -> None:
    apply_worker_resource_limits()
    install_worker_security_guards()
    try:
        if mode == "filesystem_read":
            with open("/etc/passwd", "rb"):
                pass
        elif mode == "filesystem_write":
            with open("m7-forbidden-write", "wb"):
                pass
        elif mode == "network":
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        elif mode == "process_spawn":
            subprocess.run([sys.executable, "--version"], check=False)
        else:
            raise AssertionError("unknown fixed security probe")
    except WorkerSecurityViolation as error:
        _write_failure(program_id, error.failure)
        return
    raise AssertionError("security probe unexpectedly succeeded")


def _limits_report() -> None:
    apply_worker_resource_limits()
    payload = {
        "cpu": list(resource.getrlimit(resource.RLIMIT_CPU)),
        "processes": list(resource.getrlimit(resource.RLIMIT_NPROC)),
        "file_descriptors": list(resource.getrlimit(resource.RLIMIT_NOFILE)),
        "file_size": list(resource.getrlimit(resource.RLIMIT_FSIZE)),
    }
    sys.stdout.buffer.write(canonical_json(payload).encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> int:
    mode = sys.argv[1]
    _request, program_id = _read_request()

    if mode == "wall":
        while True:
            time.sleep(1)
    if mode == "cpu":
        try:
            apply_worker_resource_limits()
            while True:
                pass
        except WorkerResourceViolation as error:
            _write_failure(program_id, error.failure)
            return 0
    if mode == "memory":
        apply_worker_resource_limits()
        allocations = []
        while True:
            allocations.append(bytearray(16 * 1024 * 1024))
            time.sleep(0.02)
    if mode in {"filesystem_read", "filesystem_write", "network", "process_spawn"}:
        _security_probe(mode, program_id)
        return 0
    if mode == "environment":
        project_root = Path(__file__).resolve().parents[2]
        try:
            validate_worker_environment(os.environ, project_root)
        except WorkerSecurityViolation as error:
            _write_failure(program_id, error.failure)
            return 0
        if "M7_ABUSE_SECRET" not in os.environ:
            _write_failure(
                program_id,
                security_failure(
                    SecurityFailureCode.ENVIRONMENT_INHERITANCE_DENIED,
                    "parent secret was not inherited by the worker",
                ),
            )
            return 0
        raise AssertionError("parent secret reached the worker")
    if mode == "crash":
        os._exit(17)
    if mode == "corrupt_result":
        sys.stdout.buffer.write(canonical_json({"corrupt": True}).encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0
    if mode == "limits":
        _limits_report()
        return 0
    raise AssertionError("unknown fixed abuse probe")


if __name__ == "__main__":
    raise SystemExit(main())
