import copy
from dataclasses import fields
import unittest

from src.evidence.m5_schemas import BindingMap, ValueBinding
from src.evidence.schemas import CanonicalDecimal, Scale
from src.programmer.schemas import ProgramOutputKind
from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    EXECUTION_RESULT_SCHEMA_VERSION,
    ExecutionDatum,
    ExecutionFailure,
    ExecutionFailureStage,
    ExecutionOutput,
    ExecutionResult,
    SandboxExecutionRequest,
)
from src.understanding.schemas import SchemaValidationError
from tests.programmer.helpers import growth_programmer_input, valid_growth_program


def binding_map_for(program):
    bindings = [
        ValueBinding(
            placeholder=item.placeholder,
            evidence_id=item.evidence_id,
            value=CanonicalDecimal(str(index + 1)),
            source_scale=Scale.MILLION,
            source_unit=None,
            requested_output_scale=None,
            requested_output_unit=None,
        )
        for index, item in enumerate(program.inputs)
    ]
    return BindingMap(bindings=sorted(bindings, key=lambda item: item.placeholder))


def execution_request():
    programmer_input = growth_programmer_input()
    program = valid_growth_program(programmer_input)
    return SandboxExecutionRequest(
        schema_version=EXECUTION_REQUEST_SCHEMA_VERSION,
        program=program,
        programmer_input=programmer_input,
        binding_map=binding_map_for(program),
        limits_profile_id=EXECUTION_LIMITS_PROFILE_ID,
    )


def datum(value="12.5", scale=Scale.MILLION, unit="VND"):
    return ExecutionDatum(
        value=CanonicalDecimal(value),
        scale=scale,
        unit=unit,
    )


class SandboxExecutionRequestTests(unittest.TestCase):
    def test_request_has_exact_contract_and_round_trips(self):
        request = execution_request()
        self.assertEqual(
            [field.name for field in fields(SandboxExecutionRequest)],
            [
                "schema_version",
                "program",
                "programmer_input",
                "binding_map",
                "limits_profile_id",
            ],
        )
        self.assertEqual(
            SandboxExecutionRequest.from_dict(request.to_dict()).to_dict(),
            request.to_dict(),
        )

    def test_request_rejects_wrong_versions_and_unknown_fields(self):
        payload = execution_request().to_dict()
        payload["schema_version"] = "wrong"
        with self.assertRaises(SchemaValidationError):
            SandboxExecutionRequest.from_dict(payload)

        payload = execution_request().to_dict()
        payload["extra"] = None
        with self.assertRaises(SchemaValidationError):
            SandboxExecutionRequest.from_dict(payload)


class ExecutionResultSchemaTests(unittest.TestCase):
    def test_failure_stage_enum_is_exact(self):
        self.assertEqual(
            [stage.value for stage in ExecutionFailureStage],
            [
                "POLICY",
                "VALIDATION",
                "BINDING",
                "CONVERSION",
                "ARITHMETIC",
                "RESOURCE",
                "SECURITY",
                "INFRASTRUCTURE",
            ],
        )

    def test_successful_scalar_result_round_trips(self):
        payload = {
            "schema_version": EXECUTION_RESULT_SCHEMA_VERSION,
            "program_id": "program-id",
            "success": True,
            "output": {
                "kind": "SCALAR",
                "values": [
                    {"value": "12.5", "scale": "MILLION", "unit": "VND"}
                ],
            },
            "failure": None,
            "execution_ms": 14,
        }
        result = ExecutionResult.from_dict(copy.deepcopy(payload))
        self.assertEqual(result.to_dict(), payload)
        self.assertIsInstance(result.output.values[0].value, CanonicalDecimal)
        self.assertNotIsInstance(result.output.values[0].value, float)

    def test_failed_result_round_trips_with_typed_stage(self):
        payload = {
            "schema_version": EXECUTION_RESULT_SCHEMA_VERSION,
            "program_id": "program-id",
            "success": False,
            "output": None,
            "failure": {
                "stage": "VALIDATION",
                "code": "FORBIDDEN_OPERATION",
                "message": "operation is not allowlisted",
            },
            "execution_ms": 3,
        }
        result = ExecutionResult.from_dict(payload)
        self.assertEqual(result.to_dict(), payload)
        self.assertIs(result.failure.stage, ExecutionFailureStage.VALIDATION)

    def test_success_and_failure_invariants(self):
        output = ExecutionOutput(ProgramOutputKind.SCALAR, [datum()])
        failure = ExecutionFailure(
            ExecutionFailureStage.POLICY, "INVALID_REQUEST", "invalid"
        )
        invalid = (
            (True, None, None),
            (True, output, failure),
            (False, output, failure),
            (False, None, None),
        )
        for success, current_output, current_failure in invalid:
            with self.subTest(success=success, output=current_output):
                with self.assertRaises(SchemaValidationError):
                    ExecutionResult(
                        EXECUTION_RESULT_SCHEMA_VERSION,
                        "program-id",
                        success,
                        current_output,
                        current_failure,
                        0,
                    )

    def test_scalar_and_ordered_output_invariants(self):
        with self.assertRaises(SchemaValidationError):
            ExecutionOutput(ProgramOutputKind.SCALAR, [])
        with self.assertRaises(SchemaValidationError):
            ExecutionOutput(ProgramOutputKind.SCALAR, [datum(), datum("2")])
        with self.assertRaises(SchemaValidationError):
            ExecutionOutput(ProgramOutputKind.ORDERED_VALUES, [])
        ordered = ExecutionOutput(
            ProgramOutputKind.ORDERED_VALUES,
            [datum("1"), datum("2")],
        )
        self.assertEqual([item.value for item in ordered.values], ["1", "2"])

    def test_float_values_are_rejected_everywhere_in_execution_output(self):
        with self.assertRaises(SchemaValidationError):
            ExecutionDatum(12.5, Scale.RAW, None)  # type: ignore[arg-type]
        with self.assertRaises(SchemaValidationError):
            ExecutionDatum.from_dict(
                {"value": 12.5, "scale": "RAW", "unit": None}
            )

    def test_execution_ms_and_exact_schema_are_enforced(self):
        for execution_ms in (-1, 1.5, True, "14"):
            with self.subTest(execution_ms=execution_ms):
                with self.assertRaises(SchemaValidationError):
                    ExecutionResult(
                        EXECUTION_RESULT_SCHEMA_VERSION,
                        "program-id",
                        True,
                        ExecutionOutput(ProgramOutputKind.SCALAR, [datum()]),
                        None,
                        execution_ms,  # type: ignore[arg-type]
                    )
        payload = {
            "schema_version": EXECUTION_RESULT_SCHEMA_VERSION,
            "program_id": "program-id",
            "success": True,
            "output": {
                "kind": "SCALAR",
                "values": [{"value": "1", "scale": None, "unit": None}],
            },
            "failure": None,
            "execution_ms": 0,
            "result": 1.0,
        }
        with self.assertRaises(SchemaValidationError):
            ExecutionResult.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
