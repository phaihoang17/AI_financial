"""ADR-072: one sandbox configuration for Linux, macOS and Windows hosts."""

import ctypes
import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
from unittest.mock import patch

import pytest

from src.sandbox import host, windows_job
from src.sandbox.limits import M7_LIMITS_V1, T2P_PANDAS_LIMITS_V1, WorkerResourceViolation, apply_worker_resource_limits
from src.sandbox.security import WorkerSecurityViolation, expected_worker_environment

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    for name in ("M7_SANDBOX_ISOLATION", "M7_MAX_CONCURRENT_WORKERS", "T2P_MAX_CONCURRENT_WORKERS",
                 "M7_ADMISSION_TIMEOUT_MS", "T2P_ADMISSION_TIMEOUT_MS", "SANDBOX_CONFIG",
                 "SANDBOX_CONTAINER_IMAGE", "SANDBOX_CONTAINER_RUNTIME"):
        monkeypatch.delenv(name, raising=False)
    host.reset_for_tests()
    yield
    host.reset_for_tests()


@pytest.mark.parametrize("system,backend,options", [
    ("Linux", "posix-rlimit", {"start_new_session": True}),
    ("Darwin", "darwin-sandbox-exec", {"start_new_session": True}),
    ("Windows", "windows-job-object", {"creationflags": 0x08000200}),
])
def test_each_host_os_has_a_local_boundary(system, backend, options):
    assert host.local_backend(system) == backend
    assert host.popen_isolation_options(system) == options


def test_unknown_host_os_fails_closed():
    with pytest.raises(WorkerSecurityViolation) as raised:
        host.local_backend("Plan9")
    assert raised.value.failure.code == "ISOLATION_SETUP_FAILED"


def test_windows_worker_command_is_the_plain_worker():
    from src.sandbox.executor import _worker_command

    with patch("src.sandbox.executor.platform.system", return_value="Windows"):
        assert _worker_command([sys.executable, "-m", "src.sandbox.worker"]) == [
            sys.executable, "-m", "src.sandbox.worker"]


def test_missing_posix_signals_have_no_exit_code(monkeypatch):
    monkeypatch.delattr("signal.SIGXCPU", raising=False)
    assert host.exit_signal("SIGXCPU") is None
    assert host.exit_signal("SIGTERM") == -15


def test_windows_environment_keeps_the_system_root():
    environment = expected_worker_environment(ROOT, "win32")
    assert environment["SYSTEMROOT"] and "__CF_USER_TEXT_ENCODING" not in environment
    assert "SYSTEMROOT" not in expected_worker_environment(ROOT, "linux")


@pytest.mark.parametrize("profile", [M7_LIMITS_V1, T2P_PANDAS_LIMITS_V1])
def test_job_object_limits_mirror_the_posix_profile(profile):
    info = windows_job.job_limit_information(profile)
    basic = info.BasicLimitInformation
    assert info.ProcessMemoryLimit == profile.memory_bytes
    assert basic.PerProcessUserTimeLimit == profile.cpu_hard_seconds * 10_000_000
    assert basic.ActiveProcessLimit == profile.max_processes == 1
    for flag in (windows_job.JOB_OBJECT_LIMIT_PROCESS_TIME, windows_job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
                 windows_job.JOB_OBJECT_LIMIT_PROCESS_MEMORY, windows_job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE):
        assert basic.LimitFlags & flag
    if ctypes.sizeof(ctypes.c_size_t) == 8:  # documented x64 Win32 layout
        assert ctypes.sizeof(windows_job.JOBOBJECT_BASIC_LIMIT_INFORMATION) == 64
        assert ctypes.sizeof(windows_job.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144


class FakeKernel32:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __getattr__(self, name):
        def call(*args):
            self.calls.append(name)
            return 0 if name == self.fail else (-1 if name == "GetCurrentProcess" else 7)
        return call


def test_worker_assigns_itself_to_a_limited_job():
    kernel32 = FakeKernel32()
    assert windows_job.apply_job_limits(M7_LIMITS_V1, kernel32) == 7
    assert kernel32.calls == ["SetErrorMode", "CreateJobObjectW", "SetInformationJobObject",
                              "SetInformationJobObject", "GetCurrentProcess", "AssignProcessToJobObject"]


@pytest.mark.parametrize("failing", ["CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"])
def test_job_setup_failures_raise(failing):
    with pytest.raises(OSError):
        windows_job.apply_job_limits(M7_LIMITS_V1, FakeKernel32(fail=failing))


def test_windows_limit_setup_without_kernel32_fails_closed():
    # Off Windows there is no kernel32; the worker must refuse to run untrusted code.
    with pytest.raises(WorkerResourceViolation) as raised:
        apply_worker_resource_limits(M7_LIMITS_V1, system_name="Windows")
    assert raised.value.failure.code == "LIMIT_SETUP_FAILED"


def test_capacity_follows_isolation_mode_and_overrides(monkeypatch):
    config = host.settings()
    assert config.worker_capacity("m7", "linux-nsjail-v1") == 32
    assert config.worker_capacity("m7", "local") == 64
    assert config.worker_capacity("t2p", "container-v1") == 8
    monkeypatch.setenv("M7_MAX_CONCURRENT_WORKERS", "3")
    assert config.worker_capacity("m7", "linux-nsjail-v1") == 3
    monkeypatch.setenv("M7_MAX_CONCURRENT_WORKERS", "0")
    with pytest.raises(ValueError):
        config.worker_capacity("m7", "local")


def test_admission_queues_then_fails_closed(monkeypatch):
    monkeypatch.setenv("M7_MAX_CONCURRENT_WORKERS", "1")
    monkeypatch.setenv("M7_ADMISSION_TIMEOUT_MS", "50")
    entered, release = threading.Event(), threading.Event()

    def hold():
        with host.admitted("m7", "local"):
            entered.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    entered.wait(5)
    started = time.monotonic()
    with pytest.raises(WorkerResourceViolation) as raised:
        with host.admitted("m7", "local"):
            pass
    assert raised.value.failure.code == "CAPACITY_EXCEEDED" and time.monotonic() - started >= 0.04
    release.set()
    holder.join(5)
    with host.admitted("m7", "local"):  # the slot is free again
        pass


def _m7_request(index=0):
    from src.evaluation.e2e_fixtures import _oracle_artifacts, fixture_pairs, fixture_understanding

    oracle = _oracle_artifacts(fixture_understanding(f"host-{index}"), fixture_pairs(("2015",), (f"{100 + index}.5",)))
    return oracle.request, oracle.execution.output


def test_full_capacity_returns_a_typed_resource_failure(monkeypatch):
    from src.sandbox.executor import execute_sandboxed

    monkeypatch.setenv("M7_MAX_CONCURRENT_WORKERS", "1")
    monkeypatch.setenv("M7_ADMISSION_TIMEOUT_MS", "0")
    request, _ = _m7_request()
    with host.admitted("m7", "local"):
        result = execute_sandboxed(request)
    assert not result.success
    assert (result.failure.stage.value, result.failure.code) == ("RESOURCE", "CAPACITY_EXCEEDED")


# ---- container-v1 ---------------------------------------------------------

DIGEST = "registry.example/ai-financial-sandbox@sha256:" + "a" * 64


def _fake_runtime(tmp_path, sleep_seconds=0):
    """A docker-compatible client that runs the in-image command on this host."""
    log = tmp_path / "runtime.log"
    script = tmp_path / "bin" / "docker"
    script.parent.mkdir()
    script.write_text(f"""#!{sys.executable}
import json, os, sys, time
args = sys.argv[1:]
with open({str(log)!r}, "a") as handle:
    handle.write(json.dumps(args) + "\\n")
if args[0] == "rm":
    sys.exit(0)
image = args.index({DIGEST!r})
if {sleep_seconds}:
    time.sleep({sleep_seconds})
os.execv(args[image + 1], args[image + 1:])
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    config = tmp_path / "sandbox.toml"
    config.write_text((ROOT / "deploy" / "sandbox" / "sandbox.toml").read_text().replace(
        'project_root = "/opt/ai-financial"', f'project_root = "{ROOT}"').replace(
        'python = "/usr/local/bin/python"', f'python = "{sys.executable}"').replace(
        'user = "65534:65534"', 'user = "0:0"'))
    return script, log, config


@pytest.fixture
def container_mode(tmp_path, monkeypatch):
    if sys.platform == "win32" or not Path("/usr/bin/env").exists():
        pytest.skip("the fake runtime execs /usr/bin/env like the Linux image")
    script, log, config = _fake_runtime(tmp_path)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SANDBOX_CONFIG", str(config))
    monkeypatch.setenv("SANDBOX_CONTAINER_IMAGE", DIGEST)
    monkeypatch.setenv("M7_SANDBOX_ISOLATION", "container-v1")
    host.reset_for_tests()
    return log


def test_container_command_pins_image_and_drops_privileges(container_mode):
    from src.sandbox.executor import _worker_command

    command = _worker_command([sys.executable, "-m", "src.sandbox.worker"])
    joined = " ".join(command)
    for flag in ("run --rm --interactive", "--network none", "--read-only", "--cap-drop ALL",
                 "--security-opt no-new-privileges", "--ipc none", f"--memory {M7_LIMITS_V1.memory_bytes}",
                 f"--memory-swap {M7_LIMITS_V1.memory_bytes}", "--pids-limit 8"):
        assert flag in joined
    image = command.index(DIGEST)
    assert command[image + 1:image + 3] == ["/usr/bin/env", "-i"]
    assert command[-2:] == ["-m", "src.sandbox.worker"]
    assert host.container_name(type("P", (), {"args": command})) == command[command.index("--name") + 1]


def test_container_mode_rejects_unpinned_images(container_mode, monkeypatch):
    from src.sandbox.executor import _worker_command

    monkeypatch.setenv("SANDBOX_CONTAINER_IMAGE", "ai-financial-sandbox:latest")
    with pytest.raises(WorkerSecurityViolation) as raised:
        _worker_command([sys.executable, "-m", "src.sandbox.worker"])
    assert raised.value.failure.code == "ISOLATION_SETUP_FAILED"


def test_container_client_gets_host_env_and_worker_gets_exact_env(container_mode, monkeypatch):
    from src.sandbox.production import launcher_environment

    monkeypatch.setenv("DOCKER_HOST", "unix:///run/docker.sock")
    monkeypatch.setenv("SECRET_TOKEN", "x")
    launched = launcher_environment({"PYTHONPATH": "/opt/ai-financial"})
    assert launched["DOCKER_HOST"] == "unix:///run/docker.sock" and "SECRET_TOKEN" not in launched
    assert "PYTHONPATH" not in launched


def test_m7_and_t2p_workers_run_through_the_container_boundary(container_mode):
    from src.evaluation.run_sandbox_load_eval import BENIGN_CODE, _t2p_payload
    from src.sandbox.executor import execute_sandboxed
    from src.sandbox.pandas_executor import execute_pandas_sandboxed

    request, expected = _m7_request(3)
    result = execute_sandboxed(request)
    assert result.success, result.failure
    assert result.output == expected
    datasets, total = _t2p_payload(5, 40)
    pandas_result = execute_pandas_sandboxed(BENIGN_CODE, datasets)
    assert pandas_result.success and pandas_result.result["value"] == total
    runs = [json.loads(line) for line in container_mode.read_text().splitlines()]
    assert [args[0] for args in runs] == ["run", "run"]


def test_container_timeout_removes_the_container(tmp_path, monkeypatch):
    if sys.platform == "win32" or not Path("/usr/bin/env").exists():
        pytest.skip("the fake runtime execs /usr/bin/env like the Linux image")
    from src.sandbox.executor import execute_sandboxed

    script, log, config = _fake_runtime(tmp_path, sleep_seconds=5)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SANDBOX_CONFIG", str(config))
    monkeypatch.setenv("SANDBOX_CONTAINER_IMAGE", DIGEST)
    monkeypatch.setenv("M7_SANDBOX_ISOLATION", "container-v1")
    request, _ = _m7_request(4)
    result = execute_sandboxed(request)
    assert (result.failure.stage.value, result.failure.code) == ("RESOURCE", "WALL_CLOCK_TIMEOUT")
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    name = calls[0][calls[0].index("--name") + 1]
    assert ["rm", "--force", name] in calls
