"""Parent-side TASK-071 isolated worker executor."""

from __future__ import annotations

from enum import Enum
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import threading
from time import monotonic_ns
from typing import Any, Mapping

from src.sandbox.interpreter import failure_result
from src.sandbox.limits import (
    M7_LIMITS_V1,
    ResourceLimitFailureCode,
    WorkerResourceViolation,
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
from src.sandbox.schemas import (
    ExecutionFailure,
    ExecutionFailureStage,
    ExecutionResult,
    SandboxExecutionRequest,
)
from src.sandbox.security import (
    SecurityFailureCode,
    WorkerSecurityViolation,
    darwin_sandbox_profile,
    expected_worker_environment,
    security_failure,
)
from src.understanding.schemas import SchemaValidationError

try:
    import psutil
except ImportError:  # pragma: no cover - exercised by fail-closed unit test
    psutil = None


class ExecutionInfrastructureFailureCode(str, Enum):
    WORKER_START_FAILED = "WORKER_START_FAILED"
    WORKER_CRASHED = "WORKER_CRASHED"
    MALFORMED_WORKER_PROTOCOL = "MALFORMED_WORKER_PROTOCOL"
    INVALID_WORKER_RESULT = "INVALID_WORKER_RESULT"


def _worker_module_name() -> str:
    return "src.sandbox.worker"


def _elapsed_ms(start_ns: int) -> int:
    return max(0, (monotonic_ns() - start_ns) // 1_000_000)


def _infrastructure_failure(
    request: SandboxExecutionRequest,
    code: ExecutionInfrastructureFailureCode,
    message: str,
    start_ns: int,
) -> ExecutionResult:
    return failure_result(
        request.program.program_id,
        ExecutionFailure(
            stage=ExecutionFailureStage.INFRASTRUCTURE,
            code=code.value,
            message=message,
        ),
        execution_ms=_elapsed_ms(start_ns),
    )


def _kill_worker_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _worker_command(module_name: str) -> list[str]:
    command = [sys.executable, "-m", module_name]
    if platform.system() != "Darwin":
        return command
    sandbox_executable = Path("/usr/bin/sandbox-exec")
    if not sandbox_executable.is_file():
        raise WorkerSecurityViolation(
            SecurityFailureCode.ISOLATION_SETUP_FAILED,
            "Darwin sandbox-exec is unavailable",
        )
    return [
        str(sandbox_executable),
        "-p",
        darwin_sandbox_profile(),
        *command,
    ]


def _start_memory_monitor(
    process: subprocess.Popen[bytes],
    violation: list[WorkerResourceViolation],
    stop: threading.Event,
) -> threading.Thread:
    if psutil is None:
        raise WorkerResourceViolation(
            ResourceLimitFailureCode.MEMORY_MONITOR_UNAVAILABLE,
            "RSS monitor is unavailable",
        )

    def monitor() -> None:
        try:
            observed = psutil.Process(process.pid)
            while not stop.wait(0.01):
                try:
                    rss = observed.memory_info().rss
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    return
                if rss > M7_LIMITS_V1.memory_bytes:
                    violation.append(
                        WorkerResourceViolation(
                            ResourceLimitFailureCode.MEMORY_LIMIT,
                            "worker exceeded the RSS memory limit",
                        )
                    )
                    _kill_worker_group(process)
                    return
        except psutil.Error:
            violation.append(
                WorkerResourceViolation(
                    ResourceLimitFailureCode.MEMORY_MONITOR_UNAVAILABLE,
                    "RSS monitor failed closed",
                )
            )
            _kill_worker_group(process)

    thread = threading.Thread(target=monitor, name="m7-rss-monitor", daemon=True)
    thread.start()
    return thread


def _invoke_worker(payload: bytes) -> subprocess.CompletedProcess[bytes]:
    if len(payload) > M7_LIMITS_V1.max_request_bytes:
        raise WorkerResourceViolation(
            ResourceLimitFailureCode.REQUEST_SIZE_LIMIT,
            "execution request exceeds the canonical request limit",
        )
    if psutil is None:
        raise WorkerResourceViolation(
            ResourceLimitFailureCode.MEMORY_MONITOR_UNAVAILABLE,
            "RSS monitor is unavailable",
        )

    project_root = Path(__file__).resolve().parents[2]
    module_name = _worker_module_name()
    command = _worker_command(module_name)
    violation: list[WorkerResourceViolation] = []
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="m7-worker-") as isolated_cwd:
        os.chmod(isolated_cwd, 0o500)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=isolated_cwd,
                env=expected_worker_environment(project_root),
                start_new_session=True,
            )
            monitor = _start_memory_monitor(process, violation, stop)
            try:
                stdout, _ = process.communicate(
                    input=payload,
                    timeout=M7_LIMITS_V1.wall_clock_timeout_ms / 1000,
                )
            except subprocess.TimeoutExpired:
                _kill_worker_group(process)
                process.communicate()
                raise WorkerResourceViolation(
                    ResourceLimitFailureCode.WALL_CLOCK_TIMEOUT,
                    "worker exceeded the wall-clock limit",
                )
            finally:
                stop.set()
                monitor.join(timeout=0.2)
        finally:
            os.chmod(isolated_cwd, 0o700)

    if violation:
        raise violation[0]
    if len(stdout) > M7_LIMITS_V1.max_output_bytes:
        raise WorkerResourceViolation(
            ResourceLimitFailureCode.OUTPUT_SIZE_LIMIT,
            "worker exceeded the canonical output limit",
        )
    return subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=stdout,
        stderr=b"",
    )


def execute_sandboxed(
    value: SandboxExecutionRequest | Mapping[str, Any],
) -> ExecutionResult:
    """Policy-check, dispatch, and validate one isolated worker result."""
    start_ns = monotonic_ns()
    try:
        request = validate_execution_request(value)
    except ExecutionPolicyError as error:
        program_id = "unavailable"
        if isinstance(value, SandboxExecutionRequest):
            program_id = value.program.program_id
        return failure_result(
            program_id,
            error.failure,
            execution_ms=_elapsed_ms(start_ns),
        )

    try:
        payload = encode_canonical_json(
            request.to_dict(), max_bytes=MAX_PROTOCOL_REQUEST_BYTES
        )
    except ProtocolError:
        return _infrastructure_failure(
            request,
            ExecutionInfrastructureFailureCode.MALFORMED_WORKER_PROTOCOL,
            "execution request exceeds the canonical protocol boundary",
            start_ns,
        )

    try:
        completed = _invoke_worker(payload)
    except WorkerResourceViolation as error:
        return failure_result(
            request.program.program_id,
            error.failure,
            execution_ms=_elapsed_ms(start_ns),
        )
    except WorkerSecurityViolation as error:
        return failure_result(
            request.program.program_id,
            error.failure,
            execution_ms=_elapsed_ms(start_ns),
        )
    except OSError:
        return _infrastructure_failure(
            request,
            ExecutionInfrastructureFailureCode.WORKER_START_FAILED,
            "isolated worker could not be started",
            start_ns,
        )
    if completed.returncode != 0:
        if completed.returncode == -signal.SIGXCPU:
            return failure_result(
                request.program.program_id,
                resource_failure(
                    ResourceLimitFailureCode.CPU_TIME_LIMIT,
                    "worker exceeded the CPU-time limit",
                ),
                execution_ms=_elapsed_ms(start_ns),
            )
        if completed.returncode == -signal.SIGXFSZ:
            return failure_result(
                request.program.program_id,
                resource_failure(
                    ResourceLimitFailureCode.FILE_SIZE_LIMIT,
                    "worker exceeded the file-size limit",
                ),
                execution_ms=_elapsed_ms(start_ns),
            )
        return _infrastructure_failure(
            request,
            ExecutionInfrastructureFailureCode.WORKER_CRASHED,
            f"isolated worker exited with code {completed.returncode}",
            start_ns,
        )

    try:
        result_payload = decode_canonical_json(
            completed.stdout, max_bytes=MAX_PROTOCOL_RESPONSE_BYTES
        )
    except ProtocolError:
        return _infrastructure_failure(
            request,
            ExecutionInfrastructureFailureCode.MALFORMED_WORKER_PROTOCOL,
            "isolated worker returned malformed protocol data",
            start_ns,
        )
    try:
        result = ExecutionResult.from_dict(result_payload)
    except SchemaValidationError:
        return _infrastructure_failure(
            request,
            ExecutionInfrastructureFailureCode.INVALID_WORKER_RESULT,
            "isolated worker returned an invalid ExecutionResult",
            start_ns,
        )
    if result.program_id != request.program.program_id or (
        result.success
        and result.output is not None
        and result.output.kind is not request.program.output_kind
    ):
        return _infrastructure_failure(
            request,
            ExecutionInfrastructureFailureCode.INVALID_WORKER_RESULT,
            "isolated worker result does not match the dispatched Program",
            start_ns,
        )
    return result
