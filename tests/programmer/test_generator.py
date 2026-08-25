from dataclasses import replace
import unittest
from unittest.mock import patch

from src.evidence.m5_schemas import MaskedEvidenceBundle
from src.programmer.generator import (
    ProgrammerFailureCode,
    generate_program,
)
from src.programmer.schemas import (
    ProgramOperation,
    ProgramOutputKind,
    ProgrammerStatus,
)
from src.programmer.validator import (
    ProgramValidationError,
    ProgramValidationFailureCode,
    validate_program,
)
from tests.programmer.helpers import (
    average_programmer_input,
    compare_programmer_input,
    growth_programmer_input,
    lookup_programmer_input,
    ratio_programmer_input,
)


class ProgramGenerationTests(unittest.TestCase):
    def assert_generated(self, programmer_input):
        result = generate_program(programmer_input)
        self.assertIs(result.status, ProgrammerStatus.GENERATED)
        self.assertIsNone(result.failure_code)
        self.assertIsNone(result.failure_message)
        self.assertIsNotNone(result.program)
        self.assertIs(
            validate_program(result.program, programmer_input), result.program
        )
        return result.program

    def test_lookup_generates_identity(self):
        programmer_input = lookup_programmer_input()
        program = self.assert_generated(programmer_input)
        self.assertEqual(len(program.inputs), 1)
        self.assertEqual(len(program.steps), 1)
        self.assertIs(program.steps[0].operation, ProgramOperation.IDENTITY)
        self.assertEqual(program.steps[0].input_refs, ["input_0"])
        self.assertIsNone(program.formula_id)
        self.assertIs(program.output_kind, ProgramOutputKind.SCALAR)

    def test_formula_free_compare_collects_in_plan_order(self):
        programmer_input = compare_programmer_input(reverse_masked=True)
        program = self.assert_generated(programmer_input)
        self.assertIs(program.steps[0].operation, ProgramOperation.COLLECT)
        self.assertEqual(program.steps[0].input_refs, ["input_0", "input_1"])
        self.assertEqual(
            [item.evidence_id for item in program.inputs],
            ["evidence-2014", "evidence-2015"],
        )
        self.assertIsNone(program.formula_id)
        self.assertIs(program.output_kind, ProgramOutputKind.ORDERED_VALUES)

    def test_growth_applies_registered_formula_in_plan_order(self):
        programmer_input = growth_programmer_input()
        programmer_input.masked_evidence.items.reverse()
        program = self.assert_generated(programmer_input)
        step = program.steps[0]
        self.assertIs(
            step.operation, ProgramOperation.APPLY_REGISTERED_FORMULA
        )
        self.assertEqual(step.formula_id, "GROWTH_RATE")
        self.assertEqual(step.input_refs, ["input_0", "input_1"])
        self.assertEqual(
            [item.evidence_id for item in program.inputs],
            ["evidence-2014", "evidence-2015"],
        )
        self.assertIs(program.output_kind, ProgramOutputKind.SCALAR)

    def test_average_applies_registered_formula_in_plan_order(self):
        programmer_input = average_programmer_input(reverse_masked=True)
        program = self.assert_generated(programmer_input)
        step = program.steps[0]
        self.assertIs(
            step.operation, ProgramOperation.APPLY_REGISTERED_FORMULA
        )
        self.assertEqual(step.formula_id, "AVERAGE")
        self.assertEqual(
            [item.evidence_id for item in program.inputs],
            ["evidence-2013", "evidence-2014", "evidence-2015"],
        )
        self.assertEqual(step.input_refs, ["input_0", "input_1", "input_2"])
        self.assertIs(program.output_kind, ProgramOutputKind.SCALAR)

    def test_derived_ratio_is_rejected_without_formula_invention(self):
        result = generate_program(ratio_programmer_input())
        self.assertIs(result.status, ProgrammerStatus.REJECTED)
        self.assertEqual(
            result.failure_code,
            ProgrammerFailureCode.UNSUPPORTED_DERIVED_RATIO.value,
        )
        self.assertIsNone(result.program)

    def test_missing_evidence_and_wrong_placeholder_are_rejected(self):
        programmer_input = growth_programmer_input()
        missing = replace(
            programmer_input,
            masked_evidence=MaskedEvidenceBundle(
                items=[programmer_input.masked_evidence.items[0]]
            ),
        )
        missing_result = generate_program(missing)
        self.assertIs(missing_result.status, ProgrammerStatus.REJECTED)
        self.assertEqual(
            missing_result.failure_code,
            ProgrammerFailureCode.MISSING_REQUIRED_EVIDENCE.value,
        )

        wrong = growth_programmer_input()
        wrong.masked_evidence.items[0].placeholder = "val_" + "f" * 64
        wrong_result = generate_program(wrong)
        self.assertIs(wrong_result.status, ProgrammerStatus.REJECTED)
        self.assertEqual(
            wrong_result.failure_code,
            ProgrammerFailureCode.INVALID_PROGRAMMER_INPUT.value,
        )

    def test_generation_is_deterministic(self):
        programmer_input = average_programmer_input()
        first = self.assert_generated(programmer_input)
        second = self.assert_generated(programmer_input)
        self.assertEqual(first.program_id, second.program_id)
        self.assertEqual(first.canonical_json(), second.canonical_json())

    def test_validator_failure_is_returned_as_typed_rejection(self):
        error = ProgramValidationError(
            ProgramValidationFailureCode.MISSING_REQUIRED_EVIDENCE,
            "fixture",
        )
        with patch("src.programmer.generator.validate_program", side_effect=error):
            result = generate_program(growth_programmer_input())
        self.assertIs(result.status, ProgrammerStatus.REJECTED)
        self.assertEqual(
            result.failure_code,
            ProgramValidationFailureCode.MISSING_REQUIRED_EVIDENCE.value,
        )
        self.assertIsNone(result.program)


if __name__ == "__main__":
    unittest.main()
