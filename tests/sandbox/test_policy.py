from dataclasses import replace
import unittest

from src.evidence.m5_schemas import (
    BindingMap,
    ValueBinding,
    make_value_placeholder,
)
from src.evidence.schemas import CanonicalDecimal, Scale
from src.programmer.schemas import Program, ProgramOperation, ProgramStep
from src.programmer.validator import ProgramValidationFailureCode
from src.sandbox.policy import (
    ALLOWED_OPERATIONS,
    MAX_PROGRAM_DEPTH,
    MAX_PROGRAM_INPUTS,
    ExecutionBindingFailureCode,
    ExecutionPolicyError,
    ExecutionPolicyFailureCode,
    validate_execution_request,
)
from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    ExecutionFailureStage,
    SandboxExecutionRequest,
)
from tests.programmer.helpers import growth_programmer_input, valid_growth_program


def value_binding(program_input, value="1"):
    return ValueBinding(
        placeholder=program_input.placeholder,
        evidence_id=program_input.evidence_id,
        value=CanonicalDecimal(value),
        source_scale=Scale.MILLION,
        source_unit=None,
        requested_output_scale=None,
        requested_output_unit=None,
    )


def request():
    programmer_input = growth_programmer_input()
    program = valid_growth_program(programmer_input)
    bindings = sorted(
        [
            value_binding(item, str(index + 1))
            for index, item in enumerate(program.inputs)
        ],
        key=lambda item: item.placeholder,
    )
    return SandboxExecutionRequest(
        schema_version=EXECUTION_REQUEST_SCHEMA_VERSION,
        program=program,
        programmer_input=programmer_input,
        binding_map=BindingMap(bindings),
        limits_profile_id=EXECUTION_LIMITS_PROFILE_ID,
    )


def extra_binding():
    evidence_id = "extra-evidence"
    return ValueBinding(
        placeholder=make_value_placeholder(evidence_id),
        evidence_id=evidence_id,
        value=CanonicalDecimal("3"),
        source_scale=Scale.MILLION,
        source_unit=None,
        requested_output_scale=None,
        requested_output_unit=None,
    )


class StaticExecutionPolicyTests(unittest.TestCase):
    def assert_failure(self, value, stage, code):
        with self.assertRaises(ExecutionPolicyError) as caught:
            validate_execution_request(value)
        self.assertIs(caught.exception.failure.stage, stage)
        self.assertEqual(caught.exception.failure.code, code.value)
        return caught.exception.failure

    def test_valid_object_and_mapping_are_deterministic(self):
        current = request()
        first = validate_execution_request(current)
        second = validate_execution_request(current.to_dict())
        self.assertEqual(first.to_dict(), current.to_dict())
        self.assertEqual(second.to_dict(), current.to_dict())
        self.assertEqual(
            ALLOWED_OPERATIONS,
            frozenset(
                {
                    ProgramOperation.IDENTITY,
                    ProgramOperation.COLLECT,
                    ProgramOperation.APPLY_REGISTERED_FORMULA,
                }
            ),
        )

    def test_missing_extra_and_duplicate_bindings_are_typed(self):
        current = request()
        missing = replace(
            current,
            binding_map=BindingMap(current.binding_map.bindings[1:]),
        )
        self.assert_failure(
            missing,
            ExecutionFailureStage.BINDING,
            ExecutionBindingFailureCode.MISSING_BINDING,
        )

        extra = replace(
            current,
            binding_map=BindingMap(
                sorted(
                    [*current.binding_map.bindings, extra_binding()],
                    key=lambda item: item.placeholder,
                )
            ),
        )
        self.assert_failure(
            extra,
            ExecutionFailureStage.BINDING,
            ExecutionBindingFailureCode.EXTRA_BINDING,
        )

        duplicate = replace(
            current,
            binding_map=BindingMap(list(current.binding_map.bindings)),
        )
        duplicate.binding_map.bindings.append(duplicate.binding_map.bindings[0])
        self.assert_failure(
            duplicate,
            ExecutionFailureStage.BINDING,
            ExecutionBindingFailureCode.DUPLICATE_PLACEHOLDER,
        )

    def test_evidence_mismatch_is_typed(self):
        current = request()
        current.binding_map.bindings[0].evidence_id = "wrong-evidence"
        self.assert_failure(
            current,
            ExecutionFailureStage.BINDING,
            ExecutionBindingFailureCode.EVIDENCE_ID_MISMATCH,
        )

    def test_unknown_program_placeholder_preserves_m6_failure_code(self):
        payload = request().to_dict()
        payload["program"]["inputs"][0]["placeholder"] = "val_" + "f" * 64
        self.assert_failure(
            payload,
            ExecutionFailureStage.VALIDATION,
            ProgramValidationFailureCode.UNKNOWN_PLACEHOLDER,
        )

    def test_forbidden_operation_code_and_literal_preserve_m6_codes(self):
        cases = (
            (
                "operation",
                "ADD",
                ProgramValidationFailureCode.FORBIDDEN_OPERATION,
            ),
            ("literal", 100, ProgramValidationFailureCode.FORBIDDEN_LITERAL),
            (
                "source_code",
                "import os",
                ProgramValidationFailureCode.FORBIDDEN_CODE,
            ),
        )
        for field, value, code in cases:
            with self.subTest(field=field):
                payload = request().to_dict()
                if field == "operation":
                    payload["program"]["steps"][0][field] = value
                else:
                    payload["program"][field] = value
                self.assert_failure(payload, ExecutionFailureStage.VALIDATION, code)

    def test_m6_validation_is_rechecked_before_dispatch(self):
        payload = request().to_dict()
        payload["program"]["program_id"] = "0" * 64
        self.assert_failure(
            payload,
            ExecutionFailureStage.VALIDATION,
            ProgramValidationFailureCode.PROGRAM_ID_MISMATCH,
        )

    def test_caller_supplied_formula_fields_are_rejected(self):
        payload = request().to_dict()
        payload["formula_registry"] = {"CUSTOM": "code"}
        self.assert_failure(
            payload,
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.INVALID_REQUEST,
        )

    def test_structural_input_and_depth_limits_are_typed(self):
        payload = request().to_dict()
        payload["program"]["inputs"] = payload["program"]["inputs"] * (
            MAX_PROGRAM_INPUTS + 1
        )
        self.assert_failure(
            payload,
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.TOO_MANY_INPUTS,
        )

        current = request()
        steps = []
        prior_ref = current.program.inputs[0].input_id
        for index in range(MAX_PROGRAM_DEPTH + 1):
            step = ProgramStep(
                step_id=f"deep_{index}",
                operation=ProgramOperation.IDENTITY,
                input_refs=[prior_ref],
                formula_id=None,
            )
            steps.append(step)
            prior_ref = step.step_id
        steps.append(
            replace(
                current.program.steps[0],
                input_refs=[prior_ref, current.program.inputs[1].input_id],
            )
        )
        deep_program = Program.create(
            formula_registry_fingerprint=(
                current.program.formula_registry_fingerprint
            ),
            question_type=current.program.question_type,
            formula_id=current.program.formula_id,
            inputs=current.program.inputs,
            steps=steps,
            output_ref=current.program.output_ref,
            output_kind=current.program.output_kind,
        )
        self.assert_failure(
            replace(current, program=deep_program),
            ExecutionFailureStage.POLICY,
            ExecutionPolicyFailureCode.PROGRAM_TOO_DEEP,
        )

    def test_failure_is_deterministic(self):
        payload = request().to_dict()
        payload["program"]["program_id"] = "0" * 64
        first = self.assert_failure(
            payload,
            ExecutionFailureStage.VALIDATION,
            ProgramValidationFailureCode.PROGRAM_ID_MISMATCH,
        )
        second = self.assert_failure(
            payload,
            ExecutionFailureStage.VALIDATION,
            ProgramValidationFailureCode.PROGRAM_ID_MISMATCH,
        )
        self.assertEqual(first.to_dict(), second.to_dict())


if __name__ == "__main__":
    unittest.main()
