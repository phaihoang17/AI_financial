import subprocess
import unittest
from unittest.mock import patch

from src.evidence.schemas import Scale
from src.sandbox.executor import (
    ExecutionInfrastructureFailureCode,
    _invoke_worker,
    execute_sandboxed,
)
from src.sandbox.protocol import (
    MAX_PROTOCOL_REQUEST_BYTES,
    MAX_PROTOCOL_RESPONSE_BYTES,
    decode_canonical_json,
    encode_canonical_json,
)
from src.sandbox.schemas import ExecutionFailureStage, ExecutionResult
from src.understanding.requested_scale_unit_parser import RequestedScale
from tests.programmer.helpers import (
    average_programmer_input,
    compare_programmer_input,
    growth_programmer_input,
    lookup_programmer_input,
)
from tests.sandbox.execution_helpers import execution_request_for


class IsolatedExecutorTests(unittest.TestCase):
    def current_request(self):
        return execution_request_for(
            growth_programmer_input(),
            ["10", "15"],
            [Scale.RAW, Scale.RAW],
        )

    def test_all_approved_shapes_execute_in_isolated_worker(self):
        cases = (
            (
                execution_request_for(
                    lookup_programmer_input(),
                    ["2500"],
                    [Scale.MILLION],
                    requested_scales=[RequestedScale.BILLION],
                ),
                ["2.5"],
                [Scale.BILLION],
            ),
            (
                execution_request_for(
                    compare_programmer_input(),
                    ["1", "2"],
                    [Scale.RAW, Scale.RAW],
                ),
                ["1", "2"],
                [Scale.RAW, Scale.RAW],
            ),
            (self.current_request(), ["50"], [Scale.PERCENT]),
            (
                execution_request_for(
                    average_programmer_input(),
                    ["1", "2", "3"],
                    [Scale.MILLION, Scale.MILLION, Scale.MILLION],
                    requested_scales=[
                        RequestedScale.MILLION,
                        RequestedScale.MILLION,
                        RequestedScale.MILLION,
                    ],
                ),
                ["2"],
                [Scale.MILLION],
            ),
        )
        for request, expected_values, expected_scales in cases:
            with self.subTest(kind=request.program.output_kind):
                result = execute_sandboxed(request)
                self.assertTrue(result.success)
                self.assertEqual(
                    [item.value for item in result.output.values], expected_values
                )
                self.assertEqual(
                    [item.scale for item in result.output.values], expected_scales
                )

    def test_worker_revalidates_program_independently(self):
        payload = self.current_request().to_dict()
        payload["program"]["program_id"] = "0" * 64
        completed = _invoke_worker(
            encode_canonical_json(payload, max_bytes=MAX_PROTOCOL_REQUEST_BYTES)
        )
        self.assertEqual(completed.returncode, 0)
        result = ExecutionResult.from_dict(
            decode_canonical_json(
                completed.stdout, max_bytes=MAX_PROTOCOL_RESPONSE_BYTES
            )
        )
        self.assertFalse(result.success)
        self.assertIs(result.failure.stage, ExecutionFailureStage.VALIDATION)
        self.assertEqual(result.failure.code, "PROGRAM_ID_MISMATCH")

    def test_worker_crash_is_typed(self):
        completed = subprocess.CompletedProcess(
            args=["worker"], returncode=9, stdout=b"", stderr=b"secret"
        )
        with patch("src.sandbox.executor._invoke_worker", return_value=completed):
            result = execute_sandboxed(self.current_request())
        self.assertFalse(result.success)
        self.assertIs(result.failure.stage, ExecutionFailureStage.INFRASTRUCTURE)
        self.assertEqual(
            result.failure.code,
            ExecutionInfrastructureFailureCode.WORKER_CRASHED.value,
        )
        self.assertNotIn("secret", result.failure.message)

    def test_protocol_corruption_is_typed(self):
        completed = subprocess.CompletedProcess(
            args=["worker"], returncode=0, stdout=b"not-json", stderr=b""
        )
        with patch("src.sandbox.executor._invoke_worker", return_value=completed):
            result = execute_sandboxed(self.current_request())
        self.assertEqual(
            result.failure.code,
            ExecutionInfrastructureFailureCode.MALFORMED_WORKER_PROTOCOL.value,
        )

    def test_invalid_worker_result_is_typed(self):
        stdout = encode_canonical_json(
            {"not": "an ExecutionResult"},
            max_bytes=MAX_PROTOCOL_RESPONSE_BYTES,
        )
        completed = subprocess.CompletedProcess(
            args=["worker"], returncode=0, stdout=stdout, stderr=b""
        )
        with patch("src.sandbox.executor._invoke_worker", return_value=completed):
            result = execute_sandboxed(self.current_request())
        self.assertEqual(
            result.failure.code,
            ExecutionInfrastructureFailureCode.INVALID_WORKER_RESULT.value,
        )

    def test_worker_uses_fixed_module_no_shell_and_minimal_environment(self):
        completed = subprocess.CompletedProcess(
            args=["worker"], returncode=1, stdout=b"", stderr=b""
        )
        with patch("src.sandbox.executor.subprocess.run", return_value=completed) as run:
            _invoke_worker(b"{}")
        args, kwargs = run.call_args
        self.assertEqual(args[0][1:], ["-m", "src.sandbox.worker"])
        self.assertNotIn("shell", kwargs)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(
            kwargs["env"],
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )


if __name__ == "__main__":
    unittest.main()
