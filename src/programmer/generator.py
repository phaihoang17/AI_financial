"""Deterministic M6 Batch 2 symbolic Program generation."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List

from src.evidence.m5_schemas import MaskedEvidence
from src.programmer.schemas import (
    Program,
    ProgramInput,
    ProgramOperation,
    ProgramOutputKind,
    ProgrammerInput,
    ProgrammerResult,
    ProgrammerStatus,
    ProgramStep,
)
from src.programmer.validator import (
    ProgramValidationError,
    ProgramValidationFailureCode,
    validate_program,
)
from src.supervisor.formula_registry import get_formula, get_formula_implementation
from src.supervisor.schemas import QuestionType
from src.understanding.schemas import SchemaValidationError


class ProgrammerFailureCode(str, Enum):
    INVALID_PROGRAMMER_INPUT = "INVALID_PROGRAMMER_INPUT"
    UNKNOWN_REQUIREMENT = "UNKNOWN_REQUIREMENT"
    MISSING_REQUIRED_EVIDENCE = "MISSING_REQUIRED_EVIDENCE"
    AMBIGUOUS_REQUIRED_EVIDENCE = "AMBIGUOUS_REQUIRED_EVIDENCE"
    UNSUPPORTED_DERIVED_RATIO = "UNSUPPORTED_DERIVED_RATIO"
    UNSUPPORTED_QUESTION_TYPE = "UNSUPPORTED_QUESTION_TYPE"
    UNSUPPORTED_FORMULA = "UNSUPPORTED_FORMULA"
    PROGRAM_CONSTRUCTION_FAILED = "PROGRAM_CONSTRUCTION_FAILED"


def _rejected(code: str | Enum, message: str) -> ProgrammerResult:
    normalized_code = code.value if isinstance(code, Enum) else code
    return ProgrammerResult(
        status=ProgrammerStatus.REJECTED,
        program=None,
        failure_code=normalized_code,
        failure_message=message,
    )


def _ordered_required_evidence(
    programmer_input: ProgrammerInput,
) -> List[MaskedEvidence] | ProgrammerResult:
    plan = programmer_input.plan
    requirements_by_id = {
        requirement.requirement_id: requirement
        for requirement in plan.retrieval_requirements
    }
    items_by_requirement: Dict[str, List[MaskedEvidence]] = {}
    for item in programmer_input.masked_evidence.items:
        if item.requirement_id not in requirements_by_id:
            return _rejected(
                ProgrammerFailureCode.UNKNOWN_REQUIREMENT,
                f"masked evidence uses unknown requirement {item.requirement_id}",
            )
        items_by_requirement.setdefault(item.requirement_id, []).append(item)

    ordered: List[MaskedEvidence] = []
    for requirement in plan.retrieval_requirements:
        if not requirement.required:
            continue
        candidates = items_by_requirement.get(requirement.requirement_id, [])
        if not candidates:
            return _rejected(
                ProgrammerFailureCode.MISSING_REQUIRED_EVIDENCE,
                requirement.requirement_id,
            )
        if len(candidates) != 1:
            return _rejected(
                ProgrammerFailureCode.AMBIGUOUS_REQUIRED_EVIDENCE,
                requirement.requirement_id,
            )
        ordered.append(candidates[0])
    if not ordered:
        return _rejected(
            ProgrammerFailureCode.MISSING_REQUIRED_EVIDENCE,
            "Program requires at least one required evidence input",
        )
    return ordered


def _program_inputs(items: List[MaskedEvidence]) -> List[ProgramInput]:
    return [
        ProgramInput(
            input_id=f"input_{index}",
            placeholder=item.placeholder,
            evidence_id=item.evidence_id,
            requirement_id=item.requirement_id,
        )
        for index, item in enumerate(items)
    ]


def _program_shape(
    programmer_input: ProgrammerInput,
    inputs: List[ProgramInput],
) -> tuple[List[ProgramStep], ProgramOutputKind] | ProgrammerResult:
    plan = programmer_input.plan
    input_refs = [item.input_id for item in inputs]

    if plan.question_type is QuestionType.LOOKUP:
        return (
            [
                ProgramStep(
                    step_id="step_output",
                    operation=ProgramOperation.IDENTITY,
                    input_refs=[input_refs[0]],
                    formula_id=None,
                )
            ],
            ProgramOutputKind.SCALAR,
        )

    if plan.question_type is QuestionType.MULTI_PERIOD:
        if plan.formula_id is None:
            return (
                [
                    ProgramStep(
                        step_id="step_output",
                        operation=ProgramOperation.COLLECT,
                        input_refs=input_refs,
                        formula_id=None,
                    )
                ],
                ProgramOutputKind.ORDERED_VALUES,
            )
        if plan.formula_id != "GROWTH_RATE":
            code = (
                ProgramValidationFailureCode.FORMULA_NOT_REGISTERED
                if get_formula(plan.formula_id) is None
                or get_formula_implementation(plan.formula_id) is None
                else ProgrammerFailureCode.UNSUPPORTED_FORMULA
            )
            return _rejected(code, f"unsupported MULTI_PERIOD formula {plan.formula_id}")
        return (
            [
                ProgramStep(
                    step_id="step_output",
                    operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
                    input_refs=input_refs,
                    formula_id="GROWTH_RATE",
                )
            ],
            ProgramOutputKind.SCALAR,
        )

    if plan.question_type is QuestionType.AGGREGATE:
        if plan.formula_id != "AVERAGE":
            code = (
                ProgramValidationFailureCode.FORMULA_NOT_REGISTERED
                if plan.formula_id is None
                or get_formula(plan.formula_id) is None
                or get_formula_implementation(plan.formula_id) is None
                else ProgrammerFailureCode.UNSUPPORTED_FORMULA
            )
            return _rejected(code, f"unsupported AGGREGATE formula {plan.formula_id}")
        return (
            [
                ProgramStep(
                    step_id="step_output",
                    operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
                    input_refs=input_refs,
                    formula_id="AVERAGE",
                )
            ],
            ProgramOutputKind.SCALAR,
        )

    return _rejected(
        ProgrammerFailureCode.UNSUPPORTED_QUESTION_TYPE,
        plan.question_type.value,
    )


def generate_program(value: ProgrammerInput) -> ProgrammerResult:
    """Generate one validated symbolic Program without values, code, or execution."""
    if not isinstance(value, ProgrammerInput):
        return _rejected(
            ProgrammerFailureCode.INVALID_PROGRAMMER_INPUT,
            "value must be a ProgrammerInput",
        )

    # No canonical ratio Plan exists until a ratio formula is registered. Keep
    # this rejection before deep round-trip validation so even a stale ratio
    # Plan cannot cause formula invention.
    if value.plan.question_type is QuestionType.DERIVED_RATIO:
        return _rejected(
            ProgrammerFailureCode.UNSUPPORTED_DERIVED_RATIO,
            "no DERIVED_RATIO formula is registered",
        )

    try:
        programmer_input = ProgrammerInput.from_dict(value.to_dict())
    except SchemaValidationError as error:
        return _rejected(
            ProgrammerFailureCode.INVALID_PROGRAMMER_INPUT,
            str(error),
        )

    ordered = _ordered_required_evidence(programmer_input)
    if isinstance(ordered, ProgrammerResult):
        return ordered
    inputs = _program_inputs(ordered)
    shape = _program_shape(programmer_input, inputs)
    if isinstance(shape, ProgrammerResult):
        return shape
    steps, output_kind = shape

    try:
        program = Program.create(
            formula_registry_fingerprint=(
                programmer_input.formula_registry_fingerprint
            ),
            question_type=programmer_input.plan.question_type,
            formula_id=programmer_input.plan.formula_id,
            inputs=inputs,
            steps=steps,
            output_ref="step_output",
            output_kind=output_kind,
        )
    except SchemaValidationError as error:
        return _rejected(
            ProgrammerFailureCode.PROGRAM_CONSTRUCTION_FAILED,
            str(error),
        )

    try:
        validated = validate_program(program, programmer_input)
    except ProgramValidationError as error:
        return _rejected(error.code, str(error))
    return ProgrammerResult(
        status=ProgrammerStatus.GENERATED,
        program=validated,
        failure_code=None,
        failure_message=None,
    )
