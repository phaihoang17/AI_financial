import json
import sys
import unittest
from unittest.mock import patch

from src.evidence.schemas import Scale
from src.sandbox.executor import _invoke_worker, _worker_command, execute_sandboxed
from src.sandbox.limits import M7_LIMITS_V1, ResourceLimitFailureCode
from src.sandbox.protocol import MAX_PROTOCOL_REQUEST_BYTES, encode_canonical_json
from src.sandbox.schemas import ExecutionFailureStage
from src.sandbox.security import SecurityFailureCode, WorkerSecurityViolation
from tests.programmer.helpers import growth_programmer_input
from tests.sandbox.execution_helpers import execution_request_for


def _probe_command(mode: str) -> list[str]:
    # The memory probe uses a lean module so the worker cold-starts fast and
    # crosses the RSS limit with wide wall-clock margin (deterministic
    # MEMORY_LIMIT classification); enforcement and limits are unchanged.
    module = "tests.sandbox.rss_probe" if mode == "memory" else "tests.sandbox.abuse_probe"
    return [sys.executable, "-m", module, mode]


class RuntimeLimitsTests(unittest.TestCase):
    def request(self):
        return execution_request_for(
            growth_programmer_input(),
            ["10", "15"],
            [Scale.RAW, Scale.RAW],
        )

    def test_m7_limits_v1_profile_is_exact_and_protocol_bound(self):
        self.assertEqual(M7_LIMITS_V1.profile_id, "m7-limits-v1")
        self.assertEqual(M7_LIMITS_V1.wall_clock_timeout_ms, 1_500)
        self.assertEqual(
            (M7_LIMITS_V1.cpu_soft_seconds, M7_LIMITS_V1.cpu_hard_seconds),
            (1, 2),
        )
        self.assertEqual(M7_LIMITS_V1.memory_bytes, 256 * 1024 * 1024)
        self.assertEqual(M7_LIMITS_V1.max_processes, 1)
        self.assertEqual(M7_LIMITS_V1.max_open_files, 32)
        self.assertEqual(M7_LIMITS_V1.max_file_size_bytes, 0)
        self.assertEqual(M7_LIMITS_V1.max_request_bytes, 1_048_576)
        self.assertEqual(M7_LIMITS_V1.max_output_bytes, 1_048_576)

    def test_worker_applies_cpu_process_fd_and_file_size_rlimits(self):
        payload = encode_canonical_json(
            self.request().to_dict(), max_bytes=MAX_PROTOCOL_REQUEST_BYTES
        )
        with patch(
            "src.sandbox.executor._worker_python_command",
            return_value=_probe_command("limits"),
        ):
            completed = _invoke_worker(payload)
        self.assertEqual(completed.returncode, 0)
        limits = json.loads(completed.stdout)
        self.assertEqual(limits["cpu"], [1, 2])
        self.assertEqual(limits["processes"], [1, 1])
        self.assertEqual(limits["file_descriptors"], [32, 32])
        self.assertEqual(limits["file_size"], [0, 0])

    def test_parent_terminates_wall_clock_overrun_without_partial_success(self):
        with patch(
            "src.sandbox.executor._worker_python_command",
            return_value=_probe_command("wall"),
        ):
            result = execute_sandboxed(self.request())
        self.assertFalse(result.success)
        self.assertIsNone(result.output)
        self.assertIs(result.failure.stage, ExecutionFailureStage.RESOURCE)
        self.assertEqual(
            result.failure.code,
            ResourceLimitFailureCode.WALL_CLOCK_TIMEOUT.value,
        )

    def test_cpu_time_limit_is_typed(self):
        with patch(
            "src.sandbox.executor._worker_python_command",
            return_value=_probe_command("cpu"),
        ):
            result = execute_sandboxed(self.request())
        self.assertFalse(result.success)
        self.assertIsNone(result.output)
        self.assertIs(result.failure.stage, ExecutionFailureStage.RESOURCE)
        self.assertEqual(
            result.failure.code,
            ResourceLimitFailureCode.CPU_TIME_LIMIT.value,
        )

    def test_rss_memory_limit_is_typed(self):
        with patch(
            "src.sandbox.executor._worker_python_command",
            return_value=_probe_command("memory"),
        ):
            result = execute_sandboxed(self.request())
        self.assertFalse(result.success)
        self.assertIsNone(result.output)
        self.assertIs(result.failure.stage, ExecutionFailureStage.RESOURCE)
        self.assertEqual(
            result.failure.code,
            ResourceLimitFailureCode.MEMORY_LIMIT.value,
        )

    def test_missing_rss_monitor_fails_closed(self):
        with patch("src.sandbox.executor.psutil", None):
            result = execute_sandboxed(self.request())
        self.assertIs(result.failure.stage, ExecutionFailureStage.RESOURCE)
        self.assertEqual(
            result.failure.code,
            ResourceLimitFailureCode.MEMORY_MONITOR_UNAVAILABLE.value,
        )

    def test_missing_required_darwin_sandbox_fails_closed(self):
        with patch("src.sandbox.executor.platform.system", return_value="Darwin"), patch(
            "src.sandbox.executor.Path.is_file", return_value=False
        ):
            with self.assertRaises(WorkerSecurityViolation) as raised:
                _worker_command([sys.executable, "-m", "src.sandbox.worker"])
        self.assertIs(raised.exception.failure.stage, ExecutionFailureStage.SECURITY)
        self.assertEqual(
            raised.exception.failure.code,
            SecurityFailureCode.ISOLATION_SETUP_FAILED.value,
        )


if __name__ == "__main__":
    unittest.main()
