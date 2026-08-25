from dataclasses import replace
import inspect
import unittest

from src.evidence.m5_schemas import (
    BindingMap,
    MaskedEvidenceBundle,
    ValueBinding,
)
from src.evidence.schemas import CanonicalDecimal, Scale
from src.programmer.generator import ProgrammerFailureCode, generate_program
from src.programmer.schemas import ProgrammerInput, ProgrammerStatus
from src.programmer.validator import (
    ProgramValidationError,
    ProgramValidationFailureCode,
    validate_program,
)
from src.supervisor.formula_registry import (
    FORMULA_IMPLEMENTATIONS,
    FORMULA_REGISTRY,
    get_formula,
)
from src.understanding.schemas import SchemaValidationError
from tests.programmer.helpers import growth_programmer_input, rebuild_program


def _binding_map(programmer_input, values):
    bindings = [
        ValueBinding(
            placeholder=item.placeholder,
            evidence_id=item.evidence_id,
            value=CanonicalDecimal(value),
            source_scale=Scale.MILLION,
            source_unit=None,
            requested_output_scale=None,
            requested_output_unit=None,
        )
        for item, value in zip(programmer_input.masked_evidence.items, values)
    ]
    return BindingMap(bindings=sorted(bindings, key=lambda item: item.placeholder))


class M5AntiHardcodeGeneratedProgramTests(unittest.TestCase):
    def generated(self, programmer_input=None):
        programmer_input = (
            growth_programmer_input()
            if programmer_input is None
            else programmer_input
        )
        result = generate_program(programmer_input)
        self.assertIs(result.status, ProgrammerStatus.GENERATED)
        self.assertIsNotNone(result.program)
        return programmer_input, result.program

    def test_binding_values_cannot_enter_or_change_generated_program(self):
        programmer_input = growth_programmer_input()
        low_values = _binding_map(
            programmer_input, ["123456789.25", "223456789.25"]
        )
        high_values = _binding_map(
            programmer_input, ["999999999.75", "899999999.75"]
        )
        self.assertNotEqual(low_values.to_dict(), high_values.to_dict())
        self.assertEqual(
            list(inspect.signature(generate_program).parameters), ["value"]
        )

        first = generate_program(programmer_input).program
        second = generate_program(programmer_input).program
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.program_id, second.program_id)
        self.assertEqual(first.canonical_json(), second.canonical_json())

    def test_financial_values_never_appear_in_generated_serialization(self):
        programmer_input, program = self.generated()
        financial_values = (
            "123456789.25",
            "223456789.25",
            "999999999.75",
            "899999999.75",
        )
        serialized = program.canonical_json()
        for value in financial_values:
            self.assertNotIn(value, serialized)
        self.assertNotIn("raw_value", serialized)
        self.assertNotIn("normalized_value", serialized)
        self.assertIs(validate_program(program, programmer_input), program)

    def test_unknown_or_generated_placeholders_are_rejected(self):
        programmer_input = growth_programmer_input()
        programmer_input.masked_evidence.items[0].placeholder = "val_" + "f" * 64
        result = generate_program(programmer_input)
        self.assertIs(result.status, ProgrammerStatus.REJECTED)
        self.assertEqual(
            result.failure_code,
            ProgrammerFailureCode.INVALID_PROGRAMMER_INPUT.value,
        )

        clean_input, program = self.generated()
        unknown = replace(program.inputs[0], placeholder="val_" + "f" * 64)
        mutated = rebuild_program(
            program, inputs=[unknown, program.inputs[1]]
        )
        with self.assertRaises(ProgramValidationError) as caught:
            validate_program(mutated, clean_input)
        self.assertIs(
            caught.exception.code,
            ProgramValidationFailureCode.UNKNOWN_PLACEHOLDER,
        )

    def test_missing_and_unused_required_evidence_are_rejected(self):
        programmer_input = growth_programmer_input()
        missing = replace(
            programmer_input,
            masked_evidence=MaskedEvidenceBundle(
                items=[programmer_input.masked_evidence.items[0]]
            ),
        )
        result = generate_program(missing)
        self.assertIs(result.status, ProgrammerStatus.REJECTED)
        self.assertEqual(
            result.failure_code,
            ProgrammerFailureCode.MISSING_REQUIRED_EVIDENCE.value,
        )

        clean_input, program = self.generated()
        unused = replace(
            program.steps[0], input_refs=[program.inputs[0].input_id] * 2
        )
        mutated = rebuild_program(program, steps=[unused])
        with self.assertRaises(ProgramValidationError) as caught:
            validate_program(mutated, clean_input)
        self.assertIs(
            caught.exception.code,
            ProgramValidationFailureCode.MISSING_REQUIRED_EVIDENCE,
        )

    def test_formulas_can_only_come_from_formula_registry(self):
        programmer_input, program = self.generated()
        self.assertEqual(
            [item.formula_id for item in FORMULA_REGISTRY],
            ["GROWTH_RATE", "AVERAGE"],
        )
        self.assertIsNotNone(get_formula(program.formula_id))
        unknown_step = replace(program.steps[0], formula_id="NOT_REGISTERED")
        mutated = rebuild_program(
            program,
            formula_id="NOT_REGISTERED",
            steps=[unknown_step],
        )
        with self.assertRaises(ProgramValidationError) as caught:
            validate_program(mutated, programmer_input)
        self.assertIs(
            caught.exception.code,
            ProgramValidationFailureCode.FORMULA_NOT_REGISTERED,
        )

    def test_numeric_constants_cannot_be_generated(self):
        _, program = self.generated()
        payload = program.to_dict()

        def assert_symbolic(value):
            self.assertNotIsInstance(value, (int, float, bool))
            if isinstance(value, dict):
                forbidden = {
                    "constant",
                    "constants",
                    "literal",
                    "literals",
                    "value",
                }
                self.assertTrue(forbidden.isdisjoint(value))
                for item in value.values():
                    assert_symbolic(item)
            elif isinstance(value, list):
                for item in value:
                    assert_symbolic(item)

        assert_symbolic(payload)
        serialized = program.canonical_json()
        self.assertNotIn('"100"', serialized)
        growth = next(
            item
            for item in FORMULA_IMPLEMENTATIONS
            if item.formula_id == "GROWTH_RATE"
        )
        self.assertEqual(growth.constants, ["100"])

    def test_programmer_input_exact_schema_still_excludes_binding_map(self):
        payload = growth_programmer_input().to_dict()
        payload["binding_map"] = {"bindings": []}
        with self.assertRaises(SchemaValidationError):
            ProgrammerInput.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
