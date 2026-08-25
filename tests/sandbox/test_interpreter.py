from dataclasses import replace
import unittest

from src.evidence.m5_schemas import BindingMap
from src.evidence.schemas import CanonicalDecimal, Scale
from src.programmer.schemas import (
    Program,
    ProgramOperation,
    ProgramOutputKind,
    ProgramStep,
)
from src.programmer.validator import ProgramValidationFailureCode
from src.sandbox.interpreter import (
    InterpreterFailureCode,
    interpret_execution_request,
)
from src.sandbox.policy import ExecutionBindingFailureCode
from src.sandbox.schemas import ExecutionFailureStage
from src.understanding.requested_scale_unit_parser import RequestedScale
from tests.programmer.helpers import (
    average_programmer_input,
    compare_programmer_input,
    growth_programmer_input,
    lookup_programmer_input,
)
from tests.sandbox.execution_helpers import execution_request_for


class TrustedDslInterpreterTests(unittest.TestCase):
    def test_identity_applies_requested_conversion(self):
        request = execution_request_for(
            lookup_programmer_input(),
            ["2500"],
            [Scale.MILLION],
            units=["VND"],
            requested_scales=[RequestedScale.BILLION],
            requested_units=["VND"],
        )
        result = interpret_execution_request(request)
        self.assertTrue(result.success)
        self.assertIs(result.output.kind, ProgramOutputKind.SCALAR)
        self.assertEqual(result.output.values[0].value, "2.5")
        self.assertIs(result.output.values[0].scale, Scale.BILLION)
        self.assertEqual(result.output.values[0].unit, "VND")

    def test_collect_preserves_order_and_converts_independently(self):
        request = execution_request_for(
            compare_programmer_input(),
            ["1", "0.002"],
            [Scale.MILLION, Scale.BILLION],
            requested_scales=[RequestedScale.MILLION, RequestedScale.MILLION],
        )
        result = interpret_execution_request(request)
        self.assertTrue(result.success)
        self.assertIs(result.output.kind, ProgramOutputKind.ORDERED_VALUES)
        self.assertEqual(
            [item.value for item in result.output.values],
            ["1", "2"],
        )
        self.assertEqual(
            [item.scale for item in result.output.values],
            [Scale.MILLION, Scale.MILLION],
        )

    def test_growth_normalizes_magnitude_and_emits_percent(self):
        request = execution_request_for(
            growth_programmer_input(),
            ["100", "0.2"],
            [Scale.MILLION, Scale.BILLION],
            units=["VND", "VND"],
            requested_scales=[RequestedScale.PERCENT, RequestedScale.PERCENT],
        )
        result = interpret_execution_request(request)
        self.assertTrue(result.success)
        self.assertEqual(result.output.values[0].value, "100")
        self.assertIs(result.output.values[0].scale, Scale.PERCENT)
        self.assertIsNone(result.output.values[0].unit)

    def test_average_uses_common_requested_scale(self):
        request = execution_request_for(
            average_programmer_input(),
            ["1", "0.002", "3"],
            [Scale.MILLION, Scale.BILLION, Scale.MILLION],
            units=["VND", "VND", "VND"],
            requested_scales=[
                RequestedScale.MILLION,
                RequestedScale.MILLION,
                RequestedScale.MILLION,
            ],
        )
        result = interpret_execution_request(request)
        self.assertTrue(result.success)
        self.assertEqual(result.output.values[0].value, "2")
        self.assertIs(result.output.values[0].scale, Scale.MILLION)
        self.assertEqual(result.output.values[0].unit, "VND")

    def test_average_falls_back_to_raw_scale(self):
        request = execution_request_for(
            average_programmer_input(),
            ["1", "0.002", "3"],
            [Scale.MILLION, Scale.BILLION, Scale.MILLION],
        )
        result = interpret_execution_request(request)
        self.assertTrue(result.success)
        self.assertEqual(result.output.values[0].value, "2000000")
        self.assertIs(result.output.values[0].scale, Scale.RAW)

    def test_repeating_average_uses_decimal_policy_and_never_float(self):
        request = execution_request_for(
            average_programmer_input(),
            ["1", "2", "2"],
            [Scale.RAW, Scale.RAW, Scale.RAW],
        )
        result = interpret_execution_request(request)
        value = result.output.values[0].value
        self.assertEqual(value, "1." + "6" * 48 + "7")
        self.assertIsInstance(value, CanonicalDecimal)
        self.assertNotIsInstance(value, float)

    def test_incompatible_units_and_scales_are_typed(self):
        incompatible_units = execution_request_for(
            growth_programmer_input(),
            ["1", "2"],
            [Scale.MILLION, Scale.MILLION],
            units=["VND", "USD"],
        )
        result = interpret_execution_request(incompatible_units)
        self.assertFalse(result.success)
        self.assertIs(result.failure.stage, ExecutionFailureStage.CONVERSION)
        self.assertEqual(result.failure.code, "UNSUPPORTED_UNIT_CONVERSION")

        mixed_null_unit = execution_request_for(
            growth_programmer_input(),
            ["1", "2"],
            [Scale.MILLION, Scale.MILLION],
            units=[None, "VND"],
        )
        result = interpret_execution_request(mixed_null_unit)
        self.assertEqual(result.failure.code, "UNSUPPORTED_UNIT_CONVERSION")

        incompatible_scales = execution_request_for(
            growth_programmer_input(),
            ["1", "2"],
            [Scale.PERCENT, Scale.MILLION],
        )
        result = interpret_execution_request(incompatible_scales)
        self.assertIs(result.failure.stage, ExecutionFailureStage.CONVERSION)
        self.assertEqual(
            result.failure.code,
            InterpreterFailureCode.INCOMPATIBLE_SCALES.value,
        )

    def test_growth_division_by_zero_is_typed(self):
        request = execution_request_for(
            growth_programmer_input(),
            ["0", "2"],
            [Scale.MILLION, Scale.MILLION],
        )
        result = interpret_execution_request(request)
        self.assertFalse(result.success)
        self.assertIs(result.failure.stage, ExecutionFailureStage.ARITHMETIC)
        self.assertEqual(
            result.failure.code,
            InterpreterFailureCode.DIVISION_BY_ZERO.value,
        )

    def test_invalid_formula_and_binding_are_revalidated(self):
        invalid_formula = execution_request_for(
            growth_programmer_input(),
            ["1", "2"],
            [Scale.RAW, Scale.RAW],
        ).to_dict()
        invalid_formula["program"]["formula_id"] = "CUSTOM"
        result = interpret_execution_request(invalid_formula)
        self.assertFalse(result.success)
        self.assertIs(result.failure.stage, ExecutionFailureStage.VALIDATION)
        self.assertEqual(
            result.failure.code,
            ProgramValidationFailureCode.FORMULA_NOT_REGISTERED.value,
        )

        missing_binding = execution_request_for(
            growth_programmer_input(),
            ["1", "2"],
            [Scale.RAW, Scale.RAW],
        )
        missing_binding = replace(
            missing_binding,
            binding_map=BindingMap(missing_binding.binding_map.bindings[1:]),
        )
        result = interpret_execution_request(missing_binding)
        self.assertIs(result.failure.stage, ExecutionFailureStage.BINDING)
        self.assertEqual(
            result.failure.code,
            ExecutionBindingFailureCode.MISSING_BINDING.value,
        )

    def test_runtime_output_kind_mismatch_is_typed(self):
        programmer_input = lookup_programmer_input()
        generated_request = execution_request_for(
            programmer_input,
            ["1"],
            [Scale.RAW],
        )
        collect_step = ProgramStep(
            step_id="step_output",
            operation=ProgramOperation.COLLECT,
            input_refs=[generated_request.program.inputs[0].input_id],
            formula_id=None,
        )
        program = Program.create(
            formula_registry_fingerprint=(
                generated_request.program.formula_registry_fingerprint
            ),
            question_type=generated_request.program.question_type,
            formula_id=None,
            inputs=generated_request.program.inputs,
            steps=[collect_step],
            output_ref="step_output",
            output_kind=ProgramOutputKind.SCALAR,
        )
        request = replace(generated_request, program=program)
        result = interpret_execution_request(request)
        self.assertFalse(result.success)
        self.assertIs(result.failure.stage, ExecutionFailureStage.ARITHMETIC)
        self.assertEqual(
            result.failure.code,
            InterpreterFailureCode.OUTPUT_KIND_MISMATCH.value,
        )

    def test_execution_is_deterministic_except_elapsed_metadata(self):
        request = execution_request_for(
            growth_programmer_input(),
            ["10", "15"],
            [Scale.RAW, Scale.RAW],
        )
        first = interpret_execution_request(request)
        second = interpret_execution_request(request)
        self.assertEqual(first.output.to_dict(), second.output.to_dict())
        self.assertEqual(first.failure, second.failure)


if __name__ == "__main__":
    unittest.main()
