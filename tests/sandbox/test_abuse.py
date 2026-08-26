from dataclasses import replace
import os
import sys
import unittest
from unittest.mock import patch

from src.evidence.schemas import Scale
from src.programmer.schemas import Program, ProgramOperation, ProgramOutputKind, ProgramStep
from src.programmer.validator import ProgramValidationFailureCode
from src.sandbox.executor import (
    ExecutionInfrastructureFailureCode,
    _invoke_worker,
    execute_sandboxed,
)
from src.sandbox.limits import ResourceLimitFailureCode
from src.sandbox.protocol import (
    MAX_PROTOCOL_REQUEST_BYTES,
    MAX_PROTOCOL_RESPONSE_BYTES,
    decode_canonical_json,
)
from src.sandbox.schemas import ExecutionFailureStage, ExecutionResult
from src.sandbox.security import SecurityFailureCode
from tests.programmer.helpers import (
    compare_programmer_input,
    growth_programmer_input,
    lookup_programmer_input,
)
from tests.sandbox.execution_helpers import execution_request_for


def _probe_command(mode: str) -> list[str]:
    # The memory probe uses a lean module so the worker cold-starts fast and
    # crosses the RSS limit with wide wall-clock margin (deterministic
    # MEMORY_LIMIT classification); enforcement and limits are unchanged.
    module = "tests.sandbox.rss_probe" if mode == "memory" else "tests.sandbox.abuse_probe"
    return [sys.executable, "-m", module, mode]


class SandboxAbuseTests(unittest.TestCase):
    def request(self):
        return execution_request_for(
            growth_programmer_input(),
            ["10", "15"],
            [Scale.RAW, Scale.RAW],
        )

    def execute_probe(self, mode: str):
        with patch(
            "src.sandbox.executor._worker_python_command",
            return_value=_probe_command(mode),
        ):
            return execute_sandboxed(self.request())

    def assert_failure(self, result, stage, code):
        self.assertFalse(result.success)
        self.assertIsNone(result.output)
        self.assertIsNotNone(result.failure)
        self.assertIs(result.failure.stage, stage)
        self.assertEqual(result.failure.code, code)

    def test_timeout_and_infinite_work_fail_closed(self):
        self.assert_failure(
            self.execute_probe("wall"),
            ExecutionFailureStage.RESOURCE,
            ResourceLimitFailureCode.WALL_CLOCK_TIMEOUT.value,
        )

    def test_memory_pressure_fails_closed(self):
        self.assert_failure(
            self.execute_probe("memory"),
            ExecutionFailureStage.RESOURCE,
            ResourceLimitFailureCode.MEMORY_LIMIT.value,
        )

    def test_oversized_request_is_rejected_before_dispatch(self):
        request = execution_request_for(
            lookup_programmer_input(),
            ["1"],
            [Scale.RAW],
            units=["U" * (MAX_PROTOCOL_REQUEST_BYTES + 1)],
        )
        self.assert_failure(
            execute_sandboxed(request),
            ExecutionFailureStage.RESOURCE,
            ResourceLimitFailureCode.REQUEST_SIZE_LIMIT.value,
        )

    def test_oversized_output_returns_only_typed_failure(self):
        request = execution_request_for(
            compare_programmer_input(),
            ["1" + "0" * 6_000, "2"],
            [Scale.RAW, Scale.RAW],
        )
        refs = [item.input_id for item in request.program.inputs]
        step = ProgramStep(
            step_id="step_oversized_collect",
            operation=ProgramOperation.COLLECT,
            input_refs=refs + [refs[0]] * 254,
            formula_id=None,
        )
        program = Program.create(
            formula_registry_fingerprint=request.program.formula_registry_fingerprint,
            question_type=request.program.question_type,
            formula_id=None,
            inputs=request.program.inputs,
            steps=[step],
            output_ref=step.step_id,
            output_kind=ProgramOutputKind.ORDERED_VALUES,
        )
        result = execute_sandboxed(replace(request, program=program))
        self.assert_failure(
            result,
            ExecutionFailureStage.RESOURCE,
            ResourceLimitFailureCode.OUTPUT_SIZE_LIMIT.value,
        )

    def test_malformed_protocol_and_protocol_injection_are_rejected(self):
        for payload in (b"not-json", b'{"valid":true}{}'):
            with self.subTest(payload=payload):
                completed = _invoke_worker(payload)
                parsed = ExecutionResult.from_dict(
                    decode_canonical_json(
                        completed.stdout,
                        max_bytes=MAX_PROTOCOL_RESPONSE_BYTES,
                    )
                )
                self.assert_failure(
                    parsed,
                    ExecutionFailureStage.INFRASTRUCTURE,
                    "MALFORMED_WORKER_PROTOCOL",
                )

    def test_worker_crash_is_not_accepted_as_partial_success(self):
        self.assert_failure(
            self.execute_probe("crash"),
            ExecutionFailureStage.INFRASTRUCTURE,
            ExecutionInfrastructureFailureCode.WORKER_CRASHED.value,
        )

    def test_filesystem_read_and_write_are_denied(self):
        expected = {
            "filesystem_read": SecurityFailureCode.FILESYSTEM_READ_DENIED.value,
            "filesystem_write": SecurityFailureCode.FILESYSTEM_WRITE_DENIED.value,
        }
        for mode, code in expected.items():
            with self.subTest(mode=mode):
                self.assert_failure(
                    self.execute_probe(mode),
                    ExecutionFailureStage.SECURITY,
                    code,
                )

    def test_network_attempt_is_denied(self):
        self.assert_failure(
            self.execute_probe("network"),
            ExecutionFailureStage.SECURITY,
            SecurityFailureCode.NETWORK_ACCESS_DENIED.value,
        )

    def test_process_spawn_attempt_is_denied(self):
        self.assert_failure(
            self.execute_probe("process_spawn"),
            ExecutionFailureStage.SECURITY,
            SecurityFailureCode.PROCESS_SPAWN_DENIED.value,
        )

    def test_parent_secret_is_not_inherited(self):
        with patch.dict(os.environ, {"M7_ABUSE_SECRET": "never-forward"}):
            result = self.execute_probe("environment")
        self.assert_failure(
            result,
            ExecutionFailureStage.SECURITY,
            SecurityFailureCode.ENVIRONMENT_INHERITANCE_DENIED.value,
        )
        self.assertNotIn("never-forward", result.failure.message)

    def test_invalid_program_shape_is_rejected_before_worker(self):
        payload = self.request().to_dict()
        payload["program"]["steps"][0]["operation"] = "ARBITRARY_CALL"
        self.assert_failure(
            execute_sandboxed(payload),
            ExecutionFailureStage.VALIDATION,
            ProgramValidationFailureCode.FORBIDDEN_OPERATION.value,
        )

    def test_unknown_formula_is_rejected_before_worker(self):
        payload = self.request().to_dict()
        payload["program"]["formula_id"] = "UNKNOWN_FORMULA"
        self.assert_failure(
            execute_sandboxed(payload),
            ExecutionFailureStage.VALIDATION,
            ProgramValidationFailureCode.FORMULA_NOT_REGISTERED.value,
        )

    def test_corrupted_execution_result_is_rejected(self):
        self.assert_failure(
            self.execute_probe("corrupt_result"),
            ExecutionFailureStage.INFRASTRUCTURE,
            ExecutionInfrastructureFailureCode.INVALID_WORKER_RESULT.value,
        )


if __name__ == "__main__":
    unittest.main()
