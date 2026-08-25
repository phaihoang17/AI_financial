"""Program-specific structural trace evaluation without execution scoring."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.evaluation.reasoning import (
    ReasoningEvaluationResult,
    TraceComparison,
    TraceEquivalenceHook,
    compare_traces,
)
from src.programmer.schemas import Program
from src.understanding.schemas import SchemaValidationError


def _normalized_program(program: Program) -> Optional[Dict[str, Any]]:
    references: Dict[str, str] = {}
    ordered_inputs: List[Dict[str, str]] = []
    for index, item in enumerate(program.inputs):
        if item.input_id in references:
            return None
        references[item.input_id] = f"input:{index}"
        ordered_inputs.append(
            {
                "placeholder": item.placeholder,
                "evidence_id": item.evidence_id,
                "requirement_id": item.requirement_id,
            }
        )

    normalized_steps: List[Dict[str, Any]] = []
    for index, step in enumerate(program.steps):
        if step.step_id in references:
            return None
        normalized_refs: List[str] = []
        for reference in step.input_refs:
            if reference not in references:
                return None
            normalized_refs.append(references[reference])
        references[step.step_id] = f"step:{index}"
        normalized_steps.append(
            {
                "operation": step.operation.value,
                "input_refs": normalized_refs,
                "formula_id": step.formula_id,
            }
        )
    if program.output_ref not in references:
        return None

    return {
        "schema_version": program.schema_version,
        "formula_registry_fingerprint": program.formula_registry_fingerprint,
        "question_type": program.question_type.value,
        "formula_id": program.formula_id,
        "inputs": ordered_inputs,
        "steps": normalized_steps,
        "output_ref": references[program.output_ref],
        "output_kind": program.output_kind.value,
    }


def normalized_program_equivalence(
    expected_trace: Any, actual_trace: Any
) -> bool:
    """Ignore graph IDs only; preserve grounding, ordering, and all operations."""
    if not isinstance(expected_trace, Program) or not isinstance(actual_trace, Program):
        return False
    expected = _normalized_program(expected_trace)
    actual = _normalized_program(actual_trace)
    return expected is not None and expected == actual


def compare_program_traces(
    expected_program: Program,
    actual_program: Program,
    equivalence_hook: Optional[TraceEquivalenceHook] = None,
) -> TraceComparison:
    """Compare exact Programs, then optionally apply a deterministic hook."""
    if not isinstance(expected_program, Program) or not isinstance(
        actual_program, Program
    ):
        raise SchemaValidationError(
            "expected_program and actual_program must be Program values"
        )
    return compare_traces(expected_program, actual_program, equivalence_hook)


def evaluate_program_trace(
    expected_program: Program,
    actual_program: Program,
    *,
    execution_correct: bool,
    answer_correct: bool,
    equivalence_hook: Optional[TraceEquivalenceHook] = None,
) -> ReasoningEvaluationResult:
    """Package trace evaluation with caller-supplied, independently scored outcomes."""
    return ReasoningEvaluationResult(
        execution_correct=execution_correct,
        trace_comparison=compare_program_traces(
            expected_program, actual_program, equivalence_hook
        ),
        answer_correct=answer_correct,
    )
