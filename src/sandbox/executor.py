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
from src.sandbox.production import production_worker_command
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


def _worker_python_command() -> list[str]:
    return [sys.executable, "-m", "src.sandbox.worker"]


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


# Only the last slice of worker stderr is retained by the parent, and solely to
# classify how the worker died. 8 KiB comfortably covers a CPython traceback.
_WORKER_STDERR_TAIL_BYTES = 8192


def _worker_exited_on_memory_error(stderr: bytes) -> bool:
    """True only when the worker terminated on an unhandled ``MemoryError``.

    On Linux the worker also runs under an ``RLIMIT_AS`` hard cap
    (``src/sandbox/limits.py``). When an allocation crosses it the interpreter
    raises ``MemoryError``; if nothing in the worker catches it, CPython prints a
    traceback whose final line is the bare exception and exits non-zero. Matching
    exactly that signature keeps the mapping narrow — any other non-zero exit
    still falls through to ``WORKER_CRASHED``.
    """
    if not stderr:
        return False
    lines = [
        line.strip()
        for line in stderr.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]
    if not lines:
        return False
    last = lines[-1]
    return last == "MemoryError" or last.startswith("MemoryError:")


def _kill_worker_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _worker_command(python_command: list[str]) -> list[str]:
    command = list(python_command)
    project_root = Path(__file__).resolve().parents[2]
    production_command = production_worker_command(
        command,
        project_root=project_root,
    )
    if production_command is not None:
        return production_command
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
                    processes = [observed, *observed.children(recursive=True)]
                    rss = 0
                    for current in processes:
                        try:
                            rss += current.memory_info().rss
                        except (psutil.NoSuchProcess, psutil.ZombieProcess):
                            continue
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
    command = _worker_command(_worker_python_command())
    violation: list[WorkerResourceViolation] = []
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="m7-worker-") as isolated_cwd:
        os.chmod(isolated_cwd, 0o500)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=isolated_cwd,
                env=expected_worker_environment(project_root),
                start_new_session=True,
            )
            monitor = _start_memory_monitor(process, violation, stop)
            try:
                stdout, stderr = process.communicate(
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
        stderr=(stderr or b"")[-_WORKER_STDERR_TAIL_BYTES:],
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
    except ProtocolError as error:
        if error.code.value == "PAYLOAD_TOO_LARGE":
            return failure_result(
                request.program.program_id,
                resource_failure(
                    ResourceLimitFailureCode.REQUEST_SIZE_LIMIT,
                    "execution request exceeds the canonical request limit",
                ),
                execution_ms=_elapsed_ms(start_ns),
            )
        return _infrastructure_failure(
            request,
            ExecutionInfrastructureFailureCode.MALFORMED_WORKER_PROTOCOL,
            "execution request cannot cross the canonical protocol boundary",
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
        if _worker_exited_on_memory_error(completed.stderr):
            return failure_result(
                request.program.program_id,
                resource_failure(
                    ResourceLimitFailureCode.MEMORY_LIMIT,
                    "worker exhausted its memory budget",
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
