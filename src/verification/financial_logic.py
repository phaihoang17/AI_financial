"""Deterministic TASK-082 Plan/Program financial-logic verification."""

from __future__ import annotations

from typing import List, Optional

from src.programmer.schemas import ProgramOperation, ProgramOutputKind
from src.supervisor.formula_registry import (
    FORMULA_REGISTRY_FINGERPRINT,
    get_formula,
    get_formula_implementation,
)
from src.supervisor.schemas import FormulaPeriodRule, QuestionType, ReasoningMode
from src.understanding.schemas import Operation
from src.verification.schemas import (
    VerificationCheckResult,
    VerificationFailureCategory,
    VerificationRequest,
)


def _check(
    check_id: str,
    passed: bool,
    reason_code: str,
    *subject_ids: Optional[str],
) -> VerificationCheckResult:
    subjects = []
    for item in subject_ids:
        if item and item not in subjects:
            subjects.append(item)
    return VerificationCheckResult(
        check_id=check_id,
        category=VerificationFailureCategory.FINANCIAL_LOGIC,
        passed=passed,
        reason_code=None if passed else reason_code,
        subject_ids=subjects,
    )


def _output_step(request: VerificationRequest):
    return next(
        (step for step in request.program.steps if step.step_id == request.program.output_ref),
        None,
    )


def _expected_shape(request: VerificationRequest):
    plan = request.plan
    if plan.question_type is QuestionType.LOOKUP:
        return ProgramOperation.IDENTITY, None, ProgramOutputKind.SCALAR
    if plan.question_type is QuestionType.MULTI_PERIOD and plan.formula_id is None:
        return ProgramOperation.COLLECT, None, ProgramOutputKind.ORDERED_VALUES
    if plan.formula_id in {"GROWTH_RATE", "AVERAGE"}:
        return (
            ProgramOperation.APPLY_REGISTERED_FORMULA,
            plan.formula_id,
            ProgramOutputKind.SCALAR,
        )
    return None


def verify_financial_logic(
    request: VerificationRequest,
) -> List[VerificationCheckResult]:
    """Validate symbolic financial intent; never calculate a formula."""
    plan = request.plan
    program = request.program
    step = _output_step(request)
    checks = [
        _check(
            "financial_logic:question_type",
            program.question_type is plan.question_type,
            "QUESTION_TYPE_MISMATCH",
            program.program_id,
        ),
        _check(
            "financial_logic:formula_id",
            program.formula_id == plan.formula_id,
            "FORMULA_ID_MISMATCH",
            program.formula_id,
            plan.formula_id,
        ),
        _check(
            "financial_logic:registry_fingerprint",
            program.formula_registry_fingerprint == FORMULA_REGISTRY_FINGERPRINT,
            "FORMULA_REGISTRY_FINGERPRINT_MISMATCH",
            program.program_id,
        ),
        _check(
            "financial_logic:output_step",
            step is not None,
            "OUTPUT_STEP_MISSING",
            program.output_ref,
        ),
        _check(
            "financial_logic:unsupported_ratio",
            plan.question_type is not QuestionType.DERIVED_RATIO,
            "UNSUPPORTED_RATIO",
            program.program_id,
        ),
    ]

    expected_reasoning = (
        ReasoningMode.PROGRAM if plan.formula_id is not None else ReasoningMode.DIRECT
    )
    checks.append(
        _check(
            "financial_logic:reasoning_mode",
            plan.reasoning_mode is expected_reasoning,
            "REASONING_MODE_MISMATCH",
            program.program_id,
        )
    )

    required = [item for item in plan.retrieval_requirements if item.required]
    expected_requirement_ids = [item.requirement_id for item in required]
    actual_requirement_ids = [item.requirement_id for item in program.inputs]
    checks.extend(
        [
            _check(
                "financial_logic:input_count_order",
                actual_requirement_ids == expected_requirement_ids,
                "INPUT_COUNT_ORDER_MISMATCH",
                *expected_requirement_ids,
            ),
            _check(
                "financial_logic:input_metric_period",
                len(program.inputs) == len(required)
                and all(
                    program_input.requirement_id == requirement.requirement_id
                    and requirement.metric in plan.target_metrics
                    and requirement.period in plan.periods
                    for program_input, requirement in zip(program.inputs, required)
                ),
                "REQUIRED_METRIC_PERIOD_MISMATCH",
                *expected_requirement_ids,
            ),
        ]
    )

    shape = _expected_shape(request)
    checks.append(
        _check(
            "financial_logic:supported_shape",
            shape is not None,
            "UNSUPPORTED_FINANCIAL_LOGIC",
            program.program_id,
        )
    )
    if shape is not None and step is not None:
        expected_operation, expected_formula_id, expected_output_kind = shape
        expected_input_refs = [item.input_id for item in program.inputs]
        if expected_operation is ProgramOperation.IDENTITY:
            expected_input_refs = expected_input_refs[:1]
        checks.extend(
            [
                _check(
                    "financial_logic:operation",
                    step.operation is expected_operation,
                    "PROGRAM_OPERATION_MISMATCH",
                    step.step_id,
                ),
                _check(
                    "financial_logic:step_formula",
                    step.formula_id == expected_formula_id,
                    "STEP_FORMULA_ID_MISMATCH",
                    step.step_id,
                ),
                _check(
                    "financial_logic:output_kind",
                    program.output_kind is expected_output_kind,
                    "PROGRAM_OUTPUT_KIND_MISMATCH",
                    program.output_ref,
                ),
                _check(
                    "financial_logic:input_refs",
                    step.input_refs == expected_input_refs,
                    "FORMULA_INPUT_ORDER_MISMATCH",
                    step.step_id,
                ),
            ]
        )

    if plan.formula_id is not None:
        definition = get_formula(plan.formula_id)
        implementation = get_formula_implementation(plan.formula_id)
        registered = definition is not None and implementation is not None
        checks.append(
            _check(
                "financial_logic:registered_formula",
                registered,
                "FORMULA_NOT_REGISTERED",
                plan.formula_id,
            )
        )
        if registered:
            arity = len(program.inputs)
            period_count = len(plan.periods)
            requirement_metrics = [item.metric for item in required]
            requirement_periods = [item.period for item in required]
            if definition.required_metrics:
                metrics_valid = requirement_metrics == definition.required_metrics
            else:
                metrics_valid = (
                    len(plan.target_metrics) == 1
                    and all(
                        metric == plan.target_metrics[0]
                        for metric in requirement_metrics
                    )
                )
            expected_registry_operation = {
                "GROWTH_RATE": Operation.GROWTH,
                "AVERAGE": Operation.AGGREGATE,
            }.get(plan.formula_id)
            period_valid = (
                definition.period_rule is FormulaPeriodRule.EXACT_TWO
                and period_count == 2
                or definition.period_rule is FormulaPeriodRule.AT_LEAST_TWO
                and period_count >= 2
                or definition.period_rule is FormulaPeriodRule.SINGLE
                and period_count == 1
            )
            arity_valid = arity >= implementation.min_arity and (
                implementation.max_arity is None or arity <= implementation.max_arity
            )
            checks.extend(
                [
                    _check(
                        "financial_logic:formula_contract",
                        definition.question_type is plan.question_type
                        and definition.reasoning_mode is plan.reasoning_mode
                        and definition.derived_target == plan.derived_target,
                        "FORMULA_CONTRACT_MISMATCH",
                        plan.formula_id,
                    ),
                    _check(
                        "financial_logic:formula_operation",
                        definition.operation is expected_registry_operation,
                        "FORMULA_OPERATION_MISMATCH",
                        plan.formula_id,
                    ),
                    _check(
                        "financial_logic:formula_metrics",
                        metrics_valid,
                        "FORMULA_METRIC_ORDER_MISMATCH",
                        plan.formula_id,
                    ),
                    _check(
                        "financial_logic:formula_period_order",
                        requirement_periods == plan.periods,
                        "FORMULA_PERIOD_ORDER_MISMATCH",
                        plan.formula_id,
                    ),
                    _check(
                        "financial_logic:formula_periods",
                        period_valid,
                        "FORMULA_PERIOD_COUNT_MISMATCH",
                        plan.formula_id,
                    ),
                    _check(
                        "financial_logic:formula_arity",
                        arity_valid,
                        "FORMULA_INPUT_COUNT_MISMATCH",
                        plan.formula_id,
                    ),
                ]
            )
    return checks
