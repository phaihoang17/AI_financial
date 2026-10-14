"""Parent-side executor for the isolated Text-to-Pandas worker (ADR-069).

Reuses the M7 process boundary (``src/sandbox/executor.py``): no shell, a new
session/process group, an empty read-only working directory, an exact minimal
environment, a parent RSS monitor, a wall-clock kill, and the Darwin
``sandbox-exec`` / Linux NsJail wrappers. Only the limits profile, the NsJail
config and the worker module differ.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from time import monotonic_ns
from typing import Any
from uuid import uuid4

from src.sandbox.executor import (
    _kill_worker_group,
    _start_memory_monitor,
    _worker_command,
    _worker_exited_on_memory_error,
)
from src.sandbox.host import admitted, exit_signal, popen_isolation_options
from src.sandbox.limits import T2P_PANDAS_LIMITS_V1, ResourceLimitFailureCode, WorkerResourceViolation
from src.sandbox.pandas_protocol import (
    T2P_REQUEST_SCHEMA_VERSION,
    PandasExecutionResult,
    PandasFailureStage,
    encode,
    failure_result,
)
from src.sandbox.pandas_worker import expected_pandas_worker_environment
from src.sandbox.production import (
    DEFAULT_T2P_NSJAIL_CONFIG,
    T2P_NSJAIL_CONFIG_ENV,
    T2P_NSJAIL_CONFIG_SHA256,
    launcher_environment,
    resolve_isolation_mode,
)
from src.sandbox.security import WorkerSecurityViolation
from src.understanding.schemas import SchemaValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _elapsed_ms(start_ns: int) -> int:
    return max(0, (monotonic_ns() - start_ns) // 1_000_000)


def pandas_worker_command() -> list[str]:
    return _worker_command(
        [sys.executable, "-m", "src.sandbox.pandas_worker"],
        config_env=T2P_NSJAIL_CONFIG_ENV,
        default_config=DEFAULT_T2P_NSJAIL_CONFIG,
        config_sha256=T2P_NSJAIL_CONFIG_SHA256,
        limits=T2P_PANDAS_LIMITS_V1,
        worker_environment=expected_pandas_worker_environment,
    )


def _invoke(payload: bytes) -> subprocess.CompletedProcess[bytes]:
    command = pandas_worker_command()
    with admitted("t2p", resolve_isolation_mode().value):
        return _run(command, payload)


def _run(command: list[str], payload: bytes) -> subprocess.CompletedProcess[bytes]:
    limits = T2P_PANDAS_LIMITS_V1
    violation: list[WorkerResourceViolation] = []
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="t2p-worker-") as isolated_cwd:
        os.chmod(isolated_cwd, 0o500)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=isolated_cwd,
                env=launcher_environment(expected_pandas_worker_environment(PROJECT_ROOT)),
                close_fds=True,
                **popen_isolation_options(),
            )
            monitor = _start_memory_monitor(process, violation, stop, memory_bytes=limits.memory_bytes)
            try:
                stdout, stderr = process.communicate(input=payload, timeout=limits.wall_clock_timeout_ms / 1000)
            except subprocess.TimeoutExpired:
                _kill_worker_group(process)
                process.communicate()
                raise WorkerResourceViolation(ResourceLimitFailureCode.WALL_CLOCK_TIMEOUT,
                                              "worker exceeded the wall-clock limit") from None
            finally:
                stop.set()
                monitor.join(timeout=0.2)
        finally:
            os.chmod(isolated_cwd, 0o700)
    if violation:
        raise violation[0]
    if len(stdout) > limits.max_output_bytes:
        raise WorkerResourceViolation(ResourceLimitFailureCode.OUTPUT_SIZE_LIMIT,
                                      "worker exceeded the output size limit")
    return subprocess.CompletedProcess(command, process.returncode, stdout, (stderr or b"")[-8192:])


def execute_pandas_sandboxed(
    code: str,
    datasets: Mapping[str, Mapping[str, Any]],
    *,
    request_id: str | None = None,
) -> PandasExecutionResult:
    """Run generated code over the approved data slice in a fresh isolated worker."""
    start_ns = monotonic_ns()
    request_id = request_id or uuid4().hex
    try:
        payload = encode({
            "schema_version": T2P_REQUEST_SCHEMA_VERSION,
            "request_id": request_id,
            "code": code,
            "datasets": {name: dict(value) for name, value in datasets.items()},
        })
    except (TypeError, ValueError):
        return failure_result(request_id, PandasFailureStage.INFRASTRUCTURE, "MALFORMED_WORKER_PROTOCOL",
                              "request cannot be encoded as JSON", execution_ms=_elapsed_ms(start_ns))
    if len(payload) > T2P_PANDAS_LIMITS_V1.max_request_bytes:
        return failure_result(request_id, PandasFailureStage.RESOURCE,
                              ResourceLimitFailureCode.REQUEST_SIZE_LIMIT.value,
                              "data slice exceeds the request size limit", execution_ms=_elapsed_ms(start_ns))
    try:
        completed = _invoke(payload)
    except WorkerResourceViolation as error:
        return failure_result(request_id, PandasFailureStage.RESOURCE, error.failure.code, error.failure.message,
                              execution_ms=_elapsed_ms(start_ns))
    except WorkerSecurityViolation as error:
        return failure_result(request_id, PandasFailureStage.SECURITY, error.failure.code, error.failure.message,
                              execution_ms=_elapsed_ms(start_ns))
    except OSError:
        return failure_result(request_id, PandasFailureStage.INFRASTRUCTURE, "WORKER_START_FAILED",
                              "isolated worker could not be started", execution_ms=_elapsed_ms(start_ns))
    if completed.returncode != 0:
        stopped = {exit_signal("SIGXCPU"), exit_signal("SIGKILL")} - {None}
        if completed.returncode in stopped and not completed.stdout:
            code_name = (ResourceLimitFailureCode.CPU_TIME_LIMIT if completed.returncode == exit_signal("SIGXCPU")
                         else ResourceLimitFailureCode.WALL_CLOCK_TIMEOUT)
            return failure_result(request_id, PandasFailureStage.RESOURCE, code_name.value,
                                  "worker was stopped by a resource limit", execution_ms=_elapsed_ms(start_ns))
        if _worker_exited_on_memory_error(completed.stderr):
            return failure_result(request_id, PandasFailureStage.RESOURCE, ResourceLimitFailureCode.MEMORY_LIMIT.value,
                                  "worker exhausted its memory budget", execution_ms=_elapsed_ms(start_ns))
        return failure_result(request_id, PandasFailureStage.INFRASTRUCTURE, "WORKER_CRASHED",
                              f"isolated worker exited with code {completed.returncode}",
                              execution_ms=_elapsed_ms(start_ns))
    try:
        result = PandasExecutionResult.from_dict(json.loads(completed.stdout.decode("utf-8")))
    except (UnicodeDecodeError, ValueError, SchemaValidationError):
        return failure_result(request_id, PandasFailureStage.INFRASTRUCTURE, "INVALID_WORKER_RESULT",
                              "isolated worker returned an invalid result", execution_ms=_elapsed_ms(start_ns))
    if result.request_id != request_id:
        return failure_result(request_id, PandasFailureStage.INFRASTRUCTURE, "INVALID_WORKER_RESULT",
                              "worker result does not match the request", execution_ms=_elapsed_ms(start_ns))
    return PandasExecutionResult(result.schema_version, result.request_id, result.success, result.result,
                                 result.evidence, result.failure, _elapsed_ms(start_ns))
