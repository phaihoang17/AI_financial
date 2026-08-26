"""Fail-closed worker security guards for the trusted M7 interpreter."""

from __future__ import annotations

from enum import Enum
import os
from pathlib import Path
import sys
from typing import Mapping

from src.sandbox.schemas import ExecutionFailure, ExecutionFailureStage


class SecurityFailureCode(str, Enum):
    FILESYSTEM_READ_DENIED = "FILESYSTEM_READ_DENIED"
    FILESYSTEM_WRITE_DENIED = "FILESYSTEM_WRITE_DENIED"
    NETWORK_ACCESS_DENIED = "NETWORK_ACCESS_DENIED"
    PROCESS_SPAWN_DENIED = "PROCESS_SPAWN_DENIED"
    ENVIRONMENT_INHERITANCE_DENIED = "ENVIRONMENT_INHERITANCE_DENIED"
    ISOLATION_SETUP_FAILED = "ISOLATION_SETUP_FAILED"


class WorkerSecurityViolation(RuntimeError):
    def __init__(self, code: SecurityFailureCode, message: str) -> None:
        self.failure = ExecutionFailure(
            stage=ExecutionFailureStage.SECURITY,
            code=code.value,
            message=message,
        )
        super().__init__(f"{code.value}: {message}")


def security_failure(code: SecurityFailureCode, message: str) -> ExecutionFailure:
    return ExecutionFailure(
        stage=ExecutionFailureStage.SECURITY,
        code=code.value,
        message=message,
    )


def expected_worker_environment(project_root: Path) -> dict[str, str]:
    return {
        "LC_CTYPE": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(project_root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def validate_worker_environment(
    environment: Mapping[str, str], project_root: Path
) -> None:
    if dict(environment) != expected_worker_environment(project_root):
        raise WorkerSecurityViolation(
            SecurityFailureCode.ENVIRONMENT_INHERITANCE_DENIED,
            "worker environment differs from the approved minimal environment",
        )


_PROCESS_EVENTS = frozenset(
    {
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
        "os.system",
        "pty.spawn",
        "subprocess.Popen",
    }
)


def _open_is_write(args: tuple[object, ...]) -> bool:
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else None
    if isinstance(mode, str) and any(marker in mode for marker in "wax+"):
        return True
    if isinstance(flags, int):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        return bool(flags & write_flags)
    return False


def _security_audit_hook(event: str, args: tuple[object, ...]) -> None:
    if event == "open":
        if _open_is_write(args):
            raise WorkerSecurityViolation(
                SecurityFailureCode.FILESYSTEM_WRITE_DENIED,
                "worker filesystem writes are denied",
            )
        raise WorkerSecurityViolation(
            SecurityFailureCode.FILESYSTEM_READ_DENIED,
            "worker filesystem reads are denied after startup",
        )
    if event.startswith("socket."):
        raise WorkerSecurityViolation(
            SecurityFailureCode.NETWORK_ACCESS_DENIED,
            "worker network access is denied",
        )
    if event in _PROCESS_EVENTS or event.startswith("subprocess."):
        raise WorkerSecurityViolation(
            SecurityFailureCode.PROCESS_SPAWN_DENIED,
            "worker process creation and execution are denied",
        )


def install_worker_security_guards() -> None:
    """Remove the minimal startup environment and deny runtime capabilities."""
    os.environ.clear()
    sys.addaudithook(_security_audit_hook)


def darwin_sandbox_profile() -> str:
    """Return the additive Darwin policy; TASK-075 remains undecided."""
    return """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(deny process-fork)
"""
