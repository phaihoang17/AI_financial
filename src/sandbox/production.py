"""Fail-closed Linux production launcher for the M7 one-shot worker."""

from __future__ import annotations

from enum import Enum
import hashlib
import os
from pathlib import Path
import platform
import stat
from typing import Mapping, Optional, Sequence

from src.sandbox.security import SecurityFailureCode, WorkerSecurityViolation


class SandboxIsolationMode(str, Enum):
    LOCAL = "local"
    LINUX_NSJAIL_V1 = "linux-nsjail-v1"


SANDBOX_ISOLATION_MODE_ENV = "M7_SANDBOX_ISOLATION"
NSJAIL_BINARY_ENV = "M7_NSJAIL_BINARY"
NSJAIL_CONFIG_ENV = "M7_NSJAIL_CONFIG"

DEFAULT_NSJAIL_BINARY = Path("/usr/local/bin/nsjail")
DEFAULT_NSJAIL_CONFIG = Path("/etc/ai-financial/m7-nsjail.cfg")
PRODUCTION_PROJECT_ROOT = Path("/opt/ai-financial")
PRODUCTION_NSJAIL_CONFIG_SHA256 = (
    "8c543f48dc3bb3c84775e538f55c33179c341eddcd01139fba80ca528e0254ae"
)
PRODUCTION_SECCOMP_POLICY_SHA256 = (
    "e39e38298392222acbaa4454a410f98fcad749a6fce10f25de4ac5a081009bc9"
)


def _setup_failure(message: str) -> WorkerSecurityViolation:
    return WorkerSecurityViolation(
        SecurityFailureCode.ISOLATION_SETUP_FAILED,
        message,
    )


def resolve_isolation_mode(
    environment: Optional[Mapping[str, str]] = None,
) -> SandboxIsolationMode:
    source = os.environ if environment is None else environment
    raw = source.get(SANDBOX_ISOLATION_MODE_ENV, SandboxIsolationMode.LOCAL.value)
    try:
        return SandboxIsolationMode(raw)
    except ValueError as error:
        raise _setup_failure("M7 sandbox isolation mode is unsupported") from error


def _validate_deployment_file(
    path: Path,
    *,
    label: str,
    executable: bool = False,
) -> None:
    if not path.is_absolute():
        raise _setup_failure(f"{label} path must be absolute")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise _setup_failure(f"{label} is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise _setup_failure(f"{label} must be a regular non-symlink file")
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise _setup_failure(f"{label} must be root-owned and not group/world writable")
    if executable and not metadata.st_mode & 0o111:
        raise _setup_failure(f"{label} is not executable")


def _validate_sha256(path: Path, expected: str, *, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise _setup_failure(f"{label} cannot be read") from error
    if actual != expected:
        raise _setup_failure(
            f"{label} fingerprint does not match the approved v1 policy"
        )


def production_worker_command(
    worker_command: Sequence[str],
    *,
    project_root: Path,
    environment: Optional[Mapping[str, str]] = None,
    system_name: Optional[str] = None,
) -> Optional[list[str]]:
    """Wrap the fixed worker with the approved Linux NsJail boundary.

    ``None`` means local/development execution should keep using its existing
    platform boundary. Production mode never falls back to an unisolated worker.
    """
    mode = resolve_isolation_mode(environment)
    if mode is SandboxIsolationMode.LOCAL:
        return None
    current_system = platform.system() if system_name is None else system_name
    if current_system != "Linux":
        raise _setup_failure("linux-nsjail-v1 requires a Linux production host")
    if project_root.resolve() != PRODUCTION_PROJECT_ROOT:
        raise _setup_failure(
            "linux-nsjail-v1 requires the application at /opt/ai-financial"
        )
    if not worker_command or not all(
        isinstance(item, str) and item for item in worker_command
    ):
        raise _setup_failure("production worker command is invalid")

    source = os.environ if environment is None else environment
    binary = Path(source.get(NSJAIL_BINARY_ENV, str(DEFAULT_NSJAIL_BINARY)))
    config = Path(source.get(NSJAIL_CONFIG_ENV, str(DEFAULT_NSJAIL_CONFIG)))
    policy = project_root / "deploy" / "sandbox" / "m7-seccomp.policy"

    _validate_deployment_file(binary, label="NsJail binary", executable=True)
    _validate_deployment_file(config, label="NsJail config")
    _validate_deployment_file(policy, label="NsJail seccomp policy")
    _validate_sha256(
        config,
        PRODUCTION_NSJAIL_CONFIG_SHA256,
        label="NsJail config",
    )
    _validate_sha256(
        policy,
        PRODUCTION_SECCOMP_POLICY_SHA256,
        label="NsJail seccomp policy",
    )
    return [str(binary), "--config", str(config), "--", *worker_command]
