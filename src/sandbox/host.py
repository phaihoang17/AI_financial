"""Host-OS process boundary and worker admission for the sandboxes (ADR-072).

The two isolated workers (M7 DSL, Text-to-Pandas) run on Linux, macOS and
Windows. This module keeps every OS difference in one place:

- how a worker is started in its own process group/session and how its whole
  process tree is killed;
- which local boundary applies (``posix-rlimit`` on Linux, ``darwin-sandbox-exec``
  on macOS, ``windows-job-object`` on Windows) when no production isolation
  mode is selected;
- how many workers of each kind may run at once (``deploy/sandbox/sandbox.toml``).
  Above the cap a request waits for a slot up to a bounded queue timeout, then
  fails closed with ``CAPACITY_EXCEEDED``; the worker wall clock starts only
  after admission.

Production isolation (Linux NsJail, or a pinned container on any OS) stays in
``src/sandbox/production.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import signal
import subprocess
import threading
import tomllib
from typing import Any, Dict, Iterator, Mapping, Optional

LOCAL_BACKENDS = {"Linux": "posix-rlimit", "Darwin": "darwin-sandbox-exec", "Windows": "windows-job-object"}
WORKER_KINDS = ("m7", "t2p")
SANDBOX_CONFIG_ENV = "SANDBOX_CONFIG"
DEFAULT_SANDBOX_CONFIG = Path(__file__).resolve().parents[2] / "deploy" / "sandbox" / "sandbox.toml"
_CAPACITY_ENV = {"m7": "M7_MAX_CONCURRENT_WORKERS", "t2p": "T2P_MAX_CONCURRENT_WORKERS"}
_QUEUE_ENV = {"m7": "M7_ADMISSION_TIMEOUT_MS", "t2p": "T2P_ADMISSION_TIMEOUT_MS"}
# Windows: own process group, no console window.
_WINDOWS_CREATIONFLAGS = 0x00000200 | 0x08000000


def host_system(system_name: Optional[str] = None) -> str:
    return platform.system() if system_name is None else system_name


def local_backend(system_name: Optional[str] = None) -> str:
    system = host_system(system_name)
    if system not in LOCAL_BACKENDS:
        from src.sandbox.security import SecurityFailureCode, WorkerSecurityViolation

        raise WorkerSecurityViolation(SecurityFailureCode.ISOLATION_SETUP_FAILED,
                                      f"no sandbox boundary is defined for {system}")
    return LOCAL_BACKENDS[system]


def popen_isolation_options(system_name: Optional[str] = None) -> Dict[str, Any]:
    """Popen options that give the worker its own session or process group."""
    if host_system(system_name) == "Windows":
        return {"creationflags": _WINDOWS_CREATIONFLAGS}
    return {"start_new_session": True}


def kill_worker_tree(process: subprocess.Popen, system_name: Optional[str] = None) -> None:
    """Kill the worker and everything it started; never raises for a dead worker."""
    if host_system(system_name) == "Windows":
        # The worker's job object (KILL_ON_JOB_CLOSE, one active process) dies with it.
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    container = container_name(process)
    if container is not None:
        _kill_container(process.args[0], container)


def container_name(process: Any) -> Optional[str]:
    """The ``--name`` of a ``container-v1`` worker command, else None."""
    args = getattr(process, "args", None)
    if not isinstance(args, (list, tuple)) or "--name" not in args:
        return None
    index = list(args).index("--name")
    return args[index + 1] if index + 1 < len(args) else None


def _kill_container(runtime: str, name: str) -> None:
    # Killing the client does not stop a running container; remove it by name.
    try:
        subprocess.run([runtime, "rm", "--force", name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def exit_signal(name: str) -> Optional[int]:
    """Negative Popen return code of a POSIX signal, or None where it does not exist."""
    number = getattr(signal, name, None)
    return None if number is None else -int(number)


@dataclass(frozen=True)
class SandboxSettings:
    capacity: Mapping[str, Mapping[str, int]]
    admission_timeout_ms: Mapping[str, int]
    container: Mapping[str, Any]

    def worker_capacity(self, kind: str, mode: str, environment: Optional[Mapping[str, str]] = None) -> int:
        source = os.environ if environment is None else environment
        raw = source.get(_CAPACITY_ENV[kind])
        value = int(raw) if raw else int(self.capacity[kind].get(mode, self.capacity[kind]["default"]))
        if value < 1:
            raise ValueError(f"{_CAPACITY_ENV[kind]} must be at least 1")
        return value

    def queue_timeout_s(self, kind: str, environment: Optional[Mapping[str, str]] = None) -> float:
        source = os.environ if environment is None else environment
        raw = source.get(_QUEUE_ENV[kind])
        value = int(raw) if raw else int(self.admission_timeout_ms[kind])
        if value < 0:
            raise ValueError(f"{_QUEUE_ENV[kind]} must not be negative")
        return value / 1000


def load_sandbox_settings(path: Optional[str | Path] = None) -> SandboxSettings:
    document = tomllib.loads(Path(path or os.environ.get(SANDBOX_CONFIG_ENV) or DEFAULT_SANDBOX_CONFIG)
                             .read_text(encoding="utf-8"))
    capacity = document.get("capacity", {})
    timeouts = document.get("admission_timeout_ms", {})
    for kind in WORKER_KINDS:
        if not isinstance(capacity.get(kind), Mapping) or "default" not in capacity[kind]:
            raise ValueError(f"sandbox config needs [capacity.{kind}] with a default")
        if not isinstance(timeouts.get(kind), int):
            raise ValueError(f"sandbox config needs admission_timeout_ms.{kind}")
    return SandboxSettings(capacity, timeouts, document.get("container", {}))


_settings: Optional[SandboxSettings] = None
_semaphores: Dict[tuple, threading.BoundedSemaphore] = {}
_lock = threading.Lock()


def settings() -> SandboxSettings:
    global _settings
    with _lock:
        if _settings is None:
            _settings = load_sandbox_settings()
        return _settings


def _semaphore(kind: str, mode: str) -> threading.BoundedSemaphore:
    capacity = settings().worker_capacity(kind, mode)
    with _lock:
        key = (kind, mode, capacity)
        if key not in _semaphores:
            _semaphores[key] = threading.BoundedSemaphore(capacity)
        return _semaphores[key]


@contextmanager
def admitted(kind: str, mode: str) -> Iterator[None]:
    """Hold one of ``kind``'s worker slots for ``mode``; fail closed when none frees in time."""
    from src.sandbox.limits import ResourceLimitFailureCode, WorkerResourceViolation

    gate = _semaphore(kind, mode)
    if not gate.acquire(timeout=settings().queue_timeout_s(kind)):
        raise WorkerResourceViolation(ResourceLimitFailureCode.CAPACITY_EXCEEDED,
                                      "all sandbox worker slots stayed busy for the admission timeout")
    try:
        yield
    finally:
        gate.release()


def reset_for_tests() -> None:
    global _settings
    with _lock:
        _settings = None
        _semaphores.clear()
