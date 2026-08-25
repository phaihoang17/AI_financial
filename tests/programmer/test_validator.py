from dataclasses import replace
import unittest

from src.programmer.schemas import (
    ProgramInput,
    ProgramOperation,
    ProgramOutputKind,
    ProgramStep,
)
from src.evidence.m5_schemas import MaskedEvidenceBundle
from src.programmer.validator import (
    ProgramValidationError,
    ProgramValidationFailureCode,
    validate_program,
)
from src.supervisor.schemas import QuestionType
from tests.programmer.helpers import (
    growth_programmer_input,
    rebuild_program,
    valid_growth_program,
)


class ProgramValidatorTests(unittest.TestCase):
    def assert_failure(self, code, value, programmer_input=None):
        programmer_input = (
            growth_programmer_input()
            if programmer_input is None
            else programmer_input
        )
        with self.assertRaises(ProgramValidationError) as caught:
            validate_program(value, programmer_input)
        self.assertIs(caught.exception.code, code)

    def test_valid_program(self):
        programmer_input = growth_programmer_input()
        program = valid_growth_program(programmer_input)
        self.assertIs(validate_program(program, programmer_input), program)
        self.assertEqual(
            validate_program(program.to_dict(), programmer_input).to_dict(),
            program.to_dict(),
        )

    def test_unknown_placeholder_and_binding_mismatch(self):
        program = valid_growth_program()
        bad_input = replace(program.inputs[0], placeholder="val_" + "f" * 64)
        self.assert_failure(
            ProgramValidationFailureCode.UNKNOWN_PLACEHOLDER,
            rebuild_program(program, inputs=[bad_input, program.inputs[1]]),
        )
        bad_input = replace(program.inputs[0], evidence_id="evidence-wrong")
        self.assert_failure(
            ProgramValidationFailureCode.INPUT_BINDING_MISMATCH,
            rebuild_program(program, inputs=[bad_input, program.inputs[1]]),
        )

    def test_required_evidence_must_reach_output(self):
        programmer_input = growth_programmer_input()
        program = valid_growth_program(programmer_input)
        missing_bundle_item = replace(
            programmer_input,
            masked_evidence=MaskedEvidenceBundle(
                items=[programmer_input.masked_evidence.items[0]]
            ),
        )
        self.assert_failure(
            ProgramValidationFailureCode.MISSING_REQUIRED_EVIDENCE,
            program,
            missing_bundle_item,
        )
        repeated = replace(
            program.steps[0], input_refs=[program.inputs[0].input_id] * 2
        )
        self.assert_failure(
            ProgramValidationFailureCode.MISSING_REQUIRED_EVIDENCE,
            rebuild_program(program, steps=[repeated]),
        )

    def test_forward_and_cyclic_references_are_rejected(self):
        program = valid_growth_program()
        forward = ProgramStep(
            "step_first", ProgramOperation.IDENTITY, ["step_later"], None
        )
        later = ProgramStep(
            "step_later", ProgramOperation.IDENTITY, ["input_0"], None
        )
        apply_forward = replace(
            program.steps[0], input_refs=["step_first", "input_1"]
        )
        self.assert_failure(
            ProgramValidationFailureCode.NON_TOPOLOGICAL_REFERENCE,
            rebuild_program(
                program,
                steps=[forward, later, apply_forward],
            ),
        )
        cyclic = ProgramStep(
            "step_cycle", ProgramOperation.IDENTITY, ["step_cycle"], None
        )
        apply_cycle = replace(
            program.steps[0], input_refs=["step_cycle", "input_1"]
        )
        self.assert_failure(
            ProgramValidationFailureCode.NON_TOPOLOGICAL_REFERENCE,
            rebuild_program(program, steps=[cyclic, apply_cycle]),
        )

    def test_forbidden_operation_literal_and_code_are_typed(self):
        program = valid_growth_program()
        payload = program.to_dict()
        payload["steps"][0]["operation"] = "ADD"
        self.assert_failure(
            ProgramValidationFailureCode.FORBIDDEN_OPERATION, payload
        )

        payload = program.to_dict()
        payload["steps"][0]["literal"] = 100
        self.assert_failure(
            ProgramValidationFailureCode.FORBIDDEN_LITERAL, payload
        )

        payload = program.to_dict()
        payload["source_code"] = "import os"
        self.assert_failure(ProgramValidationFailureCode.FORBIDDEN_CODE, payload)

    def test_unregistered_formula_and_wrong_plan_type_are_rejected(self):
        program = valid_growth_program()
        invalid_formula = rebuild_program(program, formula_id="NOT_REGISTERED")
        self.assert_failure(
            ProgramValidationFailureCode.FORMULA_NOT_REGISTERED,
            invalid_formula,
        )
        invalid_step_formula = replace(
            program.steps[0], formula_id="NOT_REGISTERED"
        )
        self.assert_failure(
            ProgramValidationFailureCode.FORMULA_NOT_REGISTERED,
            rebuild_program(program, steps=[invalid_step_formula]),
        )
        wrong_type = rebuild_program(program, question_type=QuestionType.AGGREGATE)
        self.assert_failure(
            ProgramValidationFailureCode.PLAN_QUESTION_TYPE_MISMATCH,
            wrong_type,
        )

    def test_formula_arity_and_order_are_validated(self):
        program = valid_growth_program()
        one_ref = replace(program.steps[0], input_refs=["input_0"])
        self.assert_failure(
            ProgramValidationFailureCode.FORMULA_ARITY_MISMATCH,
            rebuild_program(program, steps=[one_ref]),
        )
        reversed_refs = replace(
            program.steps[0], input_refs=["input_1", "input_0"]
        )
        self.assert_failure(
            ProgramValidationFailureCode.FORMULA_INPUT_ORDER_MISMATCH,
            rebuild_program(program, steps=[reversed_refs]),
        )

    def test_invalid_output_and_output_kind_are_rejected(self):
        program = valid_growth_program()
        self.assert_failure(
            ProgramValidationFailureCode.INVALID_OUTPUT_REF,
            rebuild_program(program, output_ref="step_missing"),
        )
        self.assert_failure(
            ProgramValidationFailureCode.OUTPUT_KIND_MISMATCH,
            rebuild_program(
                program, output_kind=ProgramOutputKind.ORDERED_VALUES
            ),
        )

    def test_unique_ids_and_program_identity_are_enforced(self):
        program = valid_growth_program()
        duplicate = ProgramInput(
            input_id=program.inputs[0].input_id,
            placeholder=program.inputs[1].placeholder,
            evidence_id=program.inputs[1].evidence_id,
            requirement_id=program.inputs[1].requirement_id,
        )
        self.assert_failure(
            ProgramValidationFailureCode.DUPLICATE_ID,
            rebuild_program(program, inputs=[program.inputs[0], duplicate]),
        )
        payload = program.to_dict()
        payload["program_id"] = "0" * 64
        self.assert_failure(
            ProgramValidationFailureCode.PROGRAM_ID_MISMATCH, payload
        )

    def test_numeric_leak_in_reference_is_rejected(self):
        program = valid_growth_program()
        leaked = replace(program.steps[0], input_refs=["12500000", "input_1"])
        self.assert_failure(
            ProgramValidationFailureCode.FORBIDDEN_LITERAL,
            rebuild_program(program, steps=[leaked]),
        )


if __name__ == "__main__":
    unittest.main()
