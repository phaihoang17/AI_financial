import unittest

from src.evaluation.program_trace import (
    compare_program_traces,
    evaluate_program_trace,
    normalized_program_equivalence,
)
from src.evaluation.reasoning import TraceComparison
from src.programmer.schemas import (
    Program,
    ProgramInput,
    ProgramOperation,
    ProgramOutputKind,
    ProgramStep,
)
from tests.programmer.helpers import rebuild_program, valid_growth_program


def renamed_graph(program):
    renamed_inputs = [
        ProgramInput(
            input_id=f"operand_{index}",
            placeholder=item.placeholder,
            evidence_id=item.evidence_id,
            requirement_id=item.requirement_id,
        )
        for index, item in enumerate(program.inputs)
    ]
    renamed_steps = [
        ProgramStep(
            step_id=f"operation_{index}",
            operation=step.operation,
            input_refs=[item.input_id for item in renamed_inputs],
            formula_id=step.formula_id,
        )
        for index, step in enumerate(program.steps)
    ]
    return Program.create(
        formula_registry_fingerprint=program.formula_registry_fingerprint,
        question_type=program.question_type,
        formula_id=program.formula_id,
        inputs=renamed_inputs,
        steps=renamed_steps,
        output_ref=renamed_steps[-1].step_id,
        output_kind=program.output_kind,
    )


class ProgramTraceEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.expected = valid_growth_program()

    def test_exact_program(self):
        self.assertIs(
            compare_program_traces(
                self.expected,
                Program.from_dict(self.expected.to_dict()),
                normalized_program_equivalence,
            ),
            TraceComparison.EXACT,
        )

    def test_graph_ids_are_normalized_by_optional_hook(self):
        actual = renamed_graph(self.expected)
        self.assertNotEqual(actual.program_id, self.expected.program_id)
        self.assertIs(
            compare_program_traces(
                self.expected, actual, normalized_program_equivalence
            ),
            TraceComparison.NORMALIZED_EQUIVALENT,
        )
        self.assertIs(
            compare_program_traces(self.expected, actual),
            TraceComparison.DIFFERENT,
        )

    def test_different_operation(self):
        step = ProgramStep(
            step_id=self.expected.steps[0].step_id,
            operation=ProgramOperation.COLLECT,
            input_refs=list(self.expected.steps[0].input_refs),
            formula_id=None,
        )
        actual = rebuild_program(self.expected, steps=[step])
        self.assertIs(
            compare_program_traces(
                self.expected, actual, normalized_program_equivalence
            ),
            TraceComparison.DIFFERENT,
        )

    def test_different_formula(self):
        step = ProgramStep(
            step_id=self.expected.steps[0].step_id,
            operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
            input_refs=list(self.expected.steps[0].input_refs),
            formula_id="AVERAGE",
        )
        actual = rebuild_program(
            self.expected, formula_id="AVERAGE", steps=[step]
        )
        self.assertIs(
            compare_program_traces(
                self.expected, actual, normalized_program_equivalence
            ),
            TraceComparison.DIFFERENT,
        )

    def test_different_input_order(self):
        reversed_inputs = list(reversed(self.expected.inputs))
        step = ProgramStep(
            step_id=self.expected.steps[0].step_id,
            operation=self.expected.steps[0].operation,
            input_refs=[item.input_id for item in reversed_inputs],
            formula_id=self.expected.steps[0].formula_id,
        )
        actual = rebuild_program(
            self.expected, inputs=reversed_inputs, steps=[step]
        )
        self.assertIs(
            compare_program_traces(
                self.expected, actual, normalized_program_equivalence
            ),
            TraceComparison.DIFFERENT,
        )

    def test_different_output_ref_and_output_kind(self):
        different_ref = rebuild_program(
            self.expected, output_ref=self.expected.inputs[0].input_id
        )
        different_kind = rebuild_program(
            self.expected, output_kind=ProgramOutputKind.ORDERED_VALUES
        )
        for actual in (different_ref, different_kind):
            with self.subTest(actual=actual.output_ref):
                self.assertIs(
                    compare_program_traces(
                        self.expected, actual, normalized_program_equivalence
                    ),
                    TraceComparison.DIFFERENT,
                )

    def test_reasoning_result_uses_caller_supplied_independent_scores(self):
        result = evaluate_program_trace(
            self.expected,
            renamed_graph(self.expected),
            execution_correct=False,
            answer_correct=True,
            equivalence_hook=normalized_program_equivalence,
        )
        self.assertFalse(result.execution_correct)
        self.assertTrue(result.answer_correct)
        self.assertIs(
            result.trace_comparison, TraceComparison.NORMALIZED_EQUIVALENT
        )


if __name__ == "__main__":
    unittest.main()
