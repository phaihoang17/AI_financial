"""Fail-closed production launchers for the one-shot sandbox workers.

``linux-nsjail-v1`` wraps the worker in the pinned NsJail profile on a Linux
host. ``container-v1`` (ADR-072) runs the same Linux worker in a pinned,
digest-addressed OCI image through Docker or Podman, so a macOS, Windows or
Linux host gets the same namespaces, cgroup limits, seccomp default profile,
no network, a read-only root and an unprivileged user. ``local`` keeps the
host-OS development boundary in ``src/sandbox/host.py``. Production modes never
fall back to an unisolated worker.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import Enum
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import stat
from uuid import uuid4

from src.sandbox.security import SecurityFailureCode, WorkerSecurityViolation


class SandboxIsolationMode(str, Enum):
    LOCAL = "local"
    LINUX_NSJAIL_V1 = "linux-nsjail-v1"
    CONTAINER_V1 = "container-v1"


SANDBOX_ISOLATION_MODE_ENV = "M7_SANDBOX_ISOLATION"
NSJAIL_BINARY_ENV = "M7_NSJAIL_BINARY"
NSJAIL_CONFIG_ENV = "M7_NSJAIL_CONFIG"

DEFAULT_NSJAIL_BINARY = Path("/usr/local/bin/nsjail")
DEFAULT_NSJAIL_CONFIG = Path("/etc/ai-financial/m7-nsjail.cfg")
PRODUCTION_PROJECT_ROOT = Path("/opt/ai-financial")
PRODUCTION_NSJAIL_CONFIG_SHA256 = (
    "b9a0f9a6b1e1fede6200f5f75cba13b65df183815267a394e27e7b5fecd22623"
)
PRODUCTION_SECCOMP_POLICY_SHA256 = (
    "528709ce4db5ff3d87c14bd298784c422de0a3f4d1dc1b74945080d6a95604f3"
)


def _setup_failure(message: str) -> WorkerSecurityViolation:
    return WorkerSecurityViolation(
        SecurityFailureCode.ISOLATION_SETUP_FAILED,
        message,
    )


def resolve_isolation_mode(
    environment: Mapping[str, str] | None = None,
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


# ADR-069: the Text-to-Pandas worker uses the same binary, seccomp policy and
# isolation mode with its own pinned config (larger limits, Python site-packages).
T2P_NSJAIL_CONFIG_ENV = "T2P_NSJAIL_CONFIG"
DEFAULT_T2P_NSJAIL_CONFIG = Path("/etc/ai-financial/t2p-nsjail.cfg")
T2P_NSJAIL_CONFIG_SHA256 = (
    "17032b290270864f106d6f859027792573118cfb366d9456fe2d7a9e9f0691f0"
)


CONTAINER_RUNTIME_ENV = "SANDBOX_CONTAINER_RUNTIME"
CONTAINER_IMAGE_ENV = "SANDBOX_CONTAINER_IMAGE"
_PINNED_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
# Host variables a Docker/Podman client may need; the worker never sees them.
_CONTAINER_CLIENT_ENV = (
    "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "SYSTEMROOT", "TEMP", "TMP",
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
    "XDG_RUNTIME_DIR", "CONTAINER_HOST", "CONTAINER_CONNECTION", "CONTAINERS_CONF",
)


def launcher_environment(
    worker_environment: Mapping[str, str],
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment of the process the parent starts.

    Locally and under NsJail this is the exact worker environment. Under
    ``container-v1`` it is the container client's host environment; the worker's
    exact environment is set inside the container with ``env -i``.
    """
    source = os.environ if environment is None else environment
    if resolve_isolation_mode(source) is not SandboxIsolationMode.CONTAINER_V1:
        return dict(worker_environment)
    return {name: source[name] for name in _CONTAINER_CLIENT_ENV if name in source}


def container_worker_command(
    worker_command: Sequence[str],
    *,
    limits: object,
    worker_environment: Callable[[Path, str | None], Mapping[str, str]],
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """``docker|podman run`` of the pinned worker image with the profile's limits."""
    from src.sandbox.host import settings

    source = os.environ if environment is None else environment
    container = settings().container
    runtime_name = source.get(CONTAINER_RUNTIME_ENV) or container.get("runtime", "docker")
    runtime = shutil.which(runtime_name)
    if runtime is None:
        raise _setup_failure("container-v1 requires a Docker or Podman client on PATH")
    image = source.get(CONTAINER_IMAGE_ENV) or container.get("image", "")
    if not _PINNED_IMAGE.match(image):
        raise _setup_failure("container-v1 requires an image pinned by sha256 digest")
    root = Path(container.get("project_root", str(PRODUCTION_PROJECT_ROOT)))
    python = container.get("python", "/usr/local/bin/python")
    module = list(worker_command)[1:]
    if module[:1] != ["-m"] or len(module) != 2:
        raise _setup_failure("production worker command is invalid")
    exact = worker_environment(root, "linux")
    memory = str(limits.memory_bytes)
    return [
        runtime, "run", "--rm", "--interactive", "--name", f"ai-fin-sbx-{uuid4().hex}",
        "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--ipc", "none", "--pids-limit", str(container.get("pids_limit", 8)),
        "--memory", memory, "--memory-swap", memory, "--cpus", str(container.get("cpus", 1)),
        "--user", str(container.get("user", "65534:65534")), "--workdir", "/tmp",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=1m", "--log-driver", "none",
        image, "/usr/bin/env", "-i", *[f"{name}={value}" for name, value in sorted(exact.items())],
        python, *module,
    ]


def production_worker_command(
    worker_command: Sequence[str],
    *,
    project_root: Path,
    environment: Mapping[str, str] | None = None,
    system_name: str | None = None,
    config_env: str = NSJAIL_CONFIG_ENV,
    default_config: Path | None = None,
    config_sha256: str | None = None,
    limits: object | None = None,
    worker_environment: Callable[[Path, str | None], Mapping[str, str]] | None = None,
) -> list[str] | None:
    """Wrap the fixed worker with the approved production boundary.

    ``None`` means local/development execution should keep using its existing
    platform boundary. Production mode never falls back to an unisolated worker.
    The config options default to the M7 profile; the T2P worker passes its own.
    """
    mode = resolve_isolation_mode(environment)
    if mode is SandboxIsolationMode.LOCAL:
        return None
    if mode is SandboxIsolationMode.CONTAINER_V1:
        from src.sandbox.limits import M7_LIMITS_V1
        from src.sandbox.security import expected_worker_environment

        if not worker_command or not all(isinstance(item, str) and item for item in worker_command):
            raise _setup_failure("production worker command is invalid")
        return container_worker_command(worker_command, limits=limits or M7_LIMITS_V1,
                                        worker_environment=worker_environment or expected_worker_environment,
                                        environment=environment)
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
    config = Path(source.get(config_env, str(default_config or DEFAULT_NSJAIL_CONFIG)))
    policy = project_root / "deploy" / "sandbox" / "m7-seccomp.policy"

    _validate_deployment_file(binary, label="NsJail binary", executable=True)
    _validate_deployment_file(config, label="NsJail config")
    _validate_deployment_file(policy, label="NsJail seccomp policy")
    _validate_sha256(
        config,
        config_sha256 or PRODUCTION_NSJAIL_CONFIG_SHA256,
        label="NsJail config",
    )
    _validate_sha256(
        policy,
        PRODUCTION_SECCOMP_POLICY_SHA256,
        label="NsJail seccomp policy",
    )
    return [str(binary), "--config", str(config), "--", *worker_command]
