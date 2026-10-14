import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import tempfile
import unittest
from unittest.mock import patch

from src.evidence.schemas import Scale
from src.sandbox.executor import _start_memory_monitor, _worker_command
from src.sandbox.executor import execute_sandboxed
from src.sandbox.limits import ResourceLimitFailureCode
from src.sandbox.production import (
    NSJAIL_BINARY_ENV,
    NSJAIL_CONFIG_ENV,
    PRODUCTION_NSJAIL_CONFIG_SHA256,
    PRODUCTION_PROJECT_ROOT,
    PRODUCTION_SECCOMP_POLICY_SHA256,
    SANDBOX_ISOLATION_MODE_ENV,
    T2P_NSJAIL_CONFIG_SHA256,
    SandboxIsolationMode,
    production_worker_command,
    resolve_isolation_mode,
)
from src.sandbox.schemas import ExecutionFailureStage
from src.sandbox.security import SecurityFailureCode, WorkerSecurityViolation
from tests.programmer.helpers import growth_programmer_input
from tests.sandbox.execution_helpers import execution_request_for


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NSJAIL_CONFIG = REPOSITORY_ROOT / "deploy" / "sandbox" / "m7-nsjail.cfg"
SECCOMP_POLICY = REPOSITORY_ROOT / "deploy" / "sandbox" / "m7-seccomp.policy"
T2P_NSJAIL_CONFIG = REPOSITORY_ROOT / "deploy" / "sandbox" / "t2p-nsjail.cfg"
INSTALLED_NSJAIL = os.environ.get("NSJAIL_TEST_BINARY") or shutil.which("nsjail") or (
    "/usr/local/bin/nsjail" if Path("/usr/local/bin/nsjail").is_file() else None
)


def _namespace_free_profile(config: str) -> str:
    """The approved profile minus the controls that need a privileged host.

    Drops user/mount/cgroup setup so the real NsJail binary can parse the
    config, compile the pinned seccomp policy and apply rlimits/no_new_privs
    on any Linux host, including an unprivileged container.
    """
    config = re.sub(r"(?ms)^(uidmap|gidmap|mount) \{.*?^\}\n", "", config)
    config = re.sub(r"(?m)^(clone_new\w+): true$", r"\1: false", config)
    config = re.sub(r"(?m)^(detect_cgroupv2|cgroup_\w+|iface_no_lo):.*\n", "", config)
    config = re.sub(r"(?m)^cwd: .*$", 'cwd: "/"', config)
    return re.sub(r"(?m)^seccomp_policy_file: .*$", f'seccomp_policy_file: "{SECCOMP_POLICY}"', config)


class ProductionSandboxBoundaryTests(unittest.TestCase):
    def production_environment(self, config: Path = NSJAIL_CONFIG) -> dict[str, str]:
        return {
            SANDBOX_ISOLATION_MODE_ENV: SandboxIsolationMode.LINUX_NSJAIL_V1.value,
            NSJAIL_BINARY_ENV: "/usr/local/bin/nsjail",
            NSJAIL_CONFIG_ENV: str(config),
        }

    def test_local_is_default_and_production_mode_is_exact(self):
        self.assertIs(resolve_isolation_mode({}), SandboxIsolationMode.LOCAL)
        self.assertIs(
            resolve_isolation_mode(self.production_environment()),
            SandboxIsolationMode.LINUX_NSJAIL_V1,
        )
        with self.assertRaises(WorkerSecurityViolation) as raised:
            resolve_isolation_mode({SANDBOX_ISOLATION_MODE_ENV: "best-effort"})
        self.assertEqual(
            raised.exception.failure.code,
            SecurityFailureCode.ISOLATION_SETUP_FAILED.value,
        )

    def test_linux_production_command_wraps_fixed_worker_without_shell(self):
        worker = ["/usr/bin/python3", "-m", "src.sandbox.worker"]
        with (
            patch(
                "src.sandbox.production.PRODUCTION_PROJECT_ROOT",
                REPOSITORY_ROOT,
            ),
            patch("src.sandbox.production._validate_deployment_file"),
        ):
            command = production_worker_command(
                worker,
                project_root=REPOSITORY_ROOT,
                environment=self.production_environment(),
                system_name="Linux",
            )
        self.assertEqual(
            command,
            [
                "/usr/local/bin/nsjail",
                "--config",
                str(NSJAIL_CONFIG),
                "--",
                *worker,
            ],
        )
        self.assertNotIn("-c", command)

    def test_production_mode_never_falls_back_off_linux(self):
        with self.assertRaises(WorkerSecurityViolation) as raised:
            production_worker_command(
                ["python3", "-m", "src.sandbox.worker"],
                project_root=PRODUCTION_PROJECT_ROOT,
                environment=self.production_environment(),
                system_name="Darwin",
            )
        self.assertIs(raised.exception.failure.stage, ExecutionFailureStage.SECURITY)
        self.assertEqual(
            raised.exception.failure.code,
            SecurityFailureCode.ISOLATION_SETUP_FAILED.value,
        )

    def test_missing_linux_launcher_fails_closed(self):
        environment = self.production_environment()
        environment[NSJAIL_BINARY_ENV] = "/missing/nsjail"
        with patch(
            "src.sandbox.production.PRODUCTION_PROJECT_ROOT",
            REPOSITORY_ROOT,
        ):
            with self.assertRaises(WorkerSecurityViolation) as raised:
                production_worker_command(
                    ["python3", "-m", "src.sandbox.worker"],
                    project_root=REPOSITORY_ROOT,
                    environment=environment,
                    system_name="Linux",
                )
        self.assertEqual(
            raised.exception.failure.code,
            SecurityFailureCode.ISOLATION_SETUP_FAILED.value,
        )

    def test_setup_failure_maps_to_failure_only_execution_result(self):
        request = execution_request_for(
            growth_programmer_input(),
            ["10", "15"],
            [Scale.RAW, Scale.RAW],
        )
        with patch.dict(
            "os.environ",
            {SANDBOX_ISOLATION_MODE_ENV: "best-effort"},
        ):
            result = execute_sandboxed(request)
        self.assertFalse(result.success)
        self.assertIsNone(result.output)
        self.assertIs(result.failure.stage, ExecutionFailureStage.SECURITY)
        self.assertEqual(
            result.failure.code,
            SecurityFailureCode.ISOLATION_SETUP_FAILED.value,
        )

    def test_policy_fingerprint_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "m7-nsjail.cfg"
            changed.write_bytes(NSJAIL_CONFIG.read_bytes() + b"\n# drift\n")
            with (
                patch(
                    "src.sandbox.production.PRODUCTION_PROJECT_ROOT",
                    REPOSITORY_ROOT,
                ),
                patch("src.sandbox.production._validate_deployment_file"),
            ):
                with self.assertRaises(WorkerSecurityViolation) as raised:
                    production_worker_command(
                        ["python3", "-m", "src.sandbox.worker"],
                        project_root=REPOSITORY_ROOT,
                        environment=self.production_environment(changed),
                        system_name="Linux",
                    )
        self.assertEqual(
            raised.exception.failure.code,
            SecurityFailureCode.ISOLATION_SETUP_FAILED.value,
        )

    def test_executor_uses_production_wrapper_before_platform_fallback(self):
        wrapped = ["nsjail", "--", "python3", "-m", "src.sandbox.worker"]
        with (
            patch(
                "src.sandbox.executor.production_worker_command",
                return_value=wrapped,
            ),
            patch("src.sandbox.executor.platform.system", return_value="Linux"),
        ):
            self.assertEqual(
                _worker_command(["python3", "-m", "src.sandbox.worker"]),
                wrapped,
            )

    def test_parent_rss_monitor_includes_jail_supervisor_children(self):
        class FakeObserved:
            def children(self, recursive: bool):
                self.recursive = recursive
                return [self.child]

            def memory_info(self):
                return type("Memory", (), {"rss": 32 * 1024 * 1024})()

        class FakeChild:
            def memory_info(self):
                return type("Memory", (), {"rss": 240 * 1024 * 1024})()

        observed = FakeObserved()
        observed.child = FakeChild()
        process = type("Process", (), {"pid": 1234})()
        violation = []
        stop = threading.Event()
        with (
            patch("src.sandbox.executor.psutil.Process", return_value=observed),
            patch("src.sandbox.executor._kill_worker_group") as kill,
        ):
            monitor = _start_memory_monitor(process, violation, stop)
            monitor.join(timeout=0.2)
        self.assertFalse(monitor.is_alive())
        self.assertTrue(observed.recursive)
        self.assertEqual(
            violation[0].failure.code,
            ResourceLimitFailureCode.MEMORY_LIMIT.value,
        )
        kill.assert_called_once_with(process)

    def test_approved_profile_pins_native_linux_controls_and_exact_limits(self):
        config = NSJAIL_CONFIG.read_text(encoding="utf-8")
        policy = SECCOMP_POLICY.read_text(encoding="utf-8")

        self.assertEqual(
            hashlib.sha256(NSJAIL_CONFIG.read_bytes()).hexdigest(),
            PRODUCTION_NSJAIL_CONFIG_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(SECCOMP_POLICY.read_bytes()).hexdigest(),
            PRODUCTION_SECCOMP_POLICY_SHA256,
        )
        for setting in (
            "mode: ONCE",
            "keep_caps: false",
            "disable_no_new_privs: false",
            "rlimit_as: 256",
            "rlimit_cpu: 2",
            "rlimit_fsize: 0",
            "rlimit_nofile: 32",
            "rlimit_nproc: 1",
            "clone_newnet: true",
            "clone_newuser: true",
            "clone_newns: true",
            "clone_newpid: true",
            "clone_newipc: true",
            "clone_newuts: true",
            "mount_proc: false",
            "cgroup_mem_max: 268435456",
            "cgroup_pids_max: 1",
        ):
            self.assertIn(setting, config)
        self.assertNotIn('src: "/"', config)
        self.assertNotIn("rw: true", config)
        self.assertIn('src: "/opt/ai-financial/src"', config)
        for syscall in ("socket", "connect", "clone", "execveat", "ptrace"):
            self.assertIn(syscall, policy)

    def test_profiles_use_syntax_the_pinned_nsjail_accepts(self):
        self.assertEqual(
            hashlib.sha256(T2P_NSJAIL_CONFIG.read_bytes()).hexdigest(),
            T2P_NSJAIL_CONFIG_SHA256,
        )
        for path in (NSJAIL_CONFIG, T2P_NSJAIL_CONFIG):
            config = path.read_text(encoding="utf-8")
            # NsJail 3.4 aborts when both are set; swap_max alone is the v2 form.
            self.assertNotRegex(config, r"(?m)^cgroup_mem_memsw_max:")
            self.assertIn("cgroup_mem_swap_max: 0", config)
            # Root runs NsJail (cgroup v2), so the jail must map to nobody, not to root.
            self.assertEqual(config.count('outside_id: "65534"'), 2)
        policy = SECCOMP_POLICY.read_text(encoding="utf-8")
        # kafel reads '#' as a preprocessor directive and has no per-arch clause.
        self.assertFalse(re.search(r"(?m)^\s*#", policy))
        self.assertNotIn(" ON ", policy)

    @unittest.skipUnless(INSTALLED_NSJAIL, "NsJail binary is not installed")
    def test_installed_nsjail_compiles_policy_and_enforces_it(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, path in (("m7", NSJAIL_CONFIG), ("t2p", T2P_NSJAIL_CONFIG)):
                profile = Path(directory) / f"{name}.cfg"
                profile.write_text(_namespace_free_profile(path.read_text(encoding="utf-8")), encoding="utf-8")
                ok = subprocess.run([INSTALLED_NSJAIL, "--config", str(profile), "-q", "--", "/bin/true"],
                                    capture_output=True, timeout=30, check=False)
                self.assertEqual(ok.returncode, 0, ok.stderr.decode(errors="replace")[-500:])
                denied = subprocess.run(
                    [INSTALLED_NSJAIL, "--config", str(profile), "-q", "--", "/bin/sh", "-c", "/bin/true"],
                    capture_output=True, timeout=30, check=False)
                # The shell cannot fork/clone its child under the policy.
                self.assertNotEqual(denied.returncode, 0)


if __name__ == "__main__":
    unittest.main()
