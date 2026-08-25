"""Versioned TASK-072 runtime limits for the isolated M7 worker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import platform
import resource
import signal

from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    ExecutionFailure,
    ExecutionFailureStage,
)


@dataclass(frozen=True)
class SandboxLimitsProfile:
    profile_id: str
    wall_clock_timeout_ms: int
    cpu_soft_seconds: int
    cpu_hard_seconds: int
    memory_bytes: int
    max_processes: int
    max_open_files: int
    max_file_size_bytes: int
    max_request_bytes: int
    max_output_bytes: int


M7_LIMITS_V1 = SandboxLimitsProfile(
    profile_id=EXECUTION_LIMITS_PROFILE_ID,
    wall_clock_timeout_ms=1_500,
    cpu_soft_seconds=1,
    cpu_hard_seconds=2,
    memory_bytes=256 * 1024 * 1024,
    max_processes=1,
    max_open_files=32,
    max_file_size_bytes=0,
    max_request_bytes=1_048_576,
    max_output_bytes=1_048_576,
)


class ResourceLimitFailureCode(str, Enum):
    WALL_CLOCK_TIMEOUT = "WALL_CLOCK_TIMEOUT"
    CPU_TIME_LIMIT = "CPU_TIME_LIMIT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    PROCESS_LIMIT = "PROCESS_LIMIT"
    FILE_DESCRIPTOR_LIMIT = "FILE_DESCRIPTOR_LIMIT"
    FILE_SIZE_LIMIT = "FILE_SIZE_LIMIT"
    REQUEST_SIZE_LIMIT = "REQUEST_SIZE_LIMIT"
    OUTPUT_SIZE_LIMIT = "OUTPUT_SIZE_LIMIT"
    LIMIT_SETUP_FAILED = "LIMIT_SETUP_FAILED"
    MEMORY_MONITOR_UNAVAILABLE = "MEMORY_MONITOR_UNAVAILABLE"


class WorkerResourceViolation(RuntimeError):
    def __init__(self, code: ResourceLimitFailureCode, message: str) -> None:
        self.failure = ExecutionFailure(
            stage=ExecutionFailureStage.RESOURCE,
            code=code.value,
            message=message,
        )
        super().__init__(f"{code.value}: {message}")


def resource_failure(
    code: ResourceLimitFailureCode, message: str
) -> ExecutionFailure:
    return ExecutionFailure(
        stage=ExecutionFailureStage.RESOURCE,
        code=code.value,
        message=message,
    )


def _raise_cpu_limit(_signum: int, _frame: object) -> None:
    raise WorkerResourceViolation(
        ResourceLimitFailureCode.CPU_TIME_LIMIT,
        "worker exceeded the CPU-time limit",
    )


def _set_limit(kind: int, soft: int, hard: int, name: str) -> None:
    try:
        resource.setrlimit(kind, (soft, hard))
    except (OSError, ValueError) as error:
        raise WorkerResourceViolation(
            ResourceLimitFailureCode.LIMIT_SETUP_FAILED,
            f"could not enforce {name}",
        ) from error


def apply_worker_resource_limits(
    profile: SandboxLimitsProfile = M7_LIMITS_V1,
) -> None:
    """Install OS limits inside the already-isolated trusted worker."""
    try:
        signal.signal(signal.SIGXCPU, _raise_cpu_limit)
    except (OSError, ValueError) as error:
        raise WorkerResourceViolation(
            ResourceLimitFailureCode.LIMIT_SETUP_FAILED,
            "could not install the CPU-limit handler",
        ) from error

    _set_limit(
        resource.RLIMIT_CPU,
        profile.cpu_soft_seconds,
        profile.cpu_hard_seconds,
        "CPU time",
    )
    _set_limit(
        resource.RLIMIT_NPROC,
        profile.max_processes,
        profile.max_processes,
        "process count",
    )
    _set_limit(
        resource.RLIMIT_NOFILE,
        profile.max_open_files,
        profile.max_open_files,
        "file descriptors",
    )
    _set_limit(
        resource.RLIMIT_FSIZE,
        profile.max_file_size_bytes,
        profile.max_file_size_bytes,
        "file size",
    )
    _set_limit(resource.RLIMIT_CORE, 0, 0, "core-file size")

    # Darwin reserves a very large virtual address region for every process, so
    # RLIMIT_AS cannot express a useful RSS cap there. The parent enforces the
    # canonical RSS limit on every platform; RLIMIT_AS is an additive Linux
    # hard stop when the kernel supports it reliably.
    if platform.system() != "Darwin" and hasattr(resource, "RLIMIT_AS"):
        _set_limit(
            resource.RLIMIT_AS,
            profile.memory_bytes,
            profile.memory_bytes,
            "address space",
        )
