"""Deterministic TASK-083/TASK-084 verification-profile policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.supervisor.schemas import QuestionType, ReasoningMode, VerifyProfile
from src.verification.schemas import (
    VerificationCheckResult,
    VerificationFailureCategory,
    VerificationRequest,
)


@dataclass(frozen=True)
class VerificationProfileDecision:
    effective_profile: VerifyProfile
    checks: List[VerificationCheckResult]


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


def select_verification_profile(
    request: VerificationRequest,
) -> VerificationProfileDecision:
    """Validate declared profiles and select a non-downgrading check set."""
    plan = request.plan
    program = request.program
    required_count = sum(
        1 for requirement in plan.retrieval_requirements if requirement.required
    )
    profile_matches = request.verify_profile is plan.verify_profile
    checks = [
        _check(
            "profile:request_plan_match",
            profile_matches,
            "VERIFY_PROFILE_MISMATCH",
            request.verify_profile.value,
            plan.verify_profile.value,
        )
    ]

    light_declared = (
        request.verify_profile is VerifyProfile.LIGHT
        or plan.verify_profile is VerifyProfile.LIGHT
    )
    light_question_type = (
        plan.question_type is QuestionType.LOOKUP
        and program.question_type is QuestionType.LOOKUP
    )
    light_reasoning = plan.reasoning_mode is ReasoningMode.DIRECT
    light_formula = plan.formula_id is None and program.formula_id is None
    light_requirement_count = (
        required_count == 1 and len(plan.retrieval_requirements) == 1
    )
    if light_declared:
        checks.extend(
            [
                _check(
                    "profile:light_question_type",
                    light_question_type,
                    "LIGHT_QUESTION_TYPE_INVALID",
                    plan.question_type.value,
                    program.question_type.value,
                ),
                _check(
                    "profile:light_reasoning_mode",
                    light_reasoning,
                    "LIGHT_REASONING_MODE_INVALID",
                    plan.reasoning_mode.value,
                ),
                _check(
                    "profile:light_formula",
                    light_formula,
                    "LIGHT_FORMULA_INVALID",
                    plan.formula_id,
                    program.formula_id,
                ),
                _check(
                    "profile:light_required_count",
                    light_requirement_count,
                    "LIGHT_REQUIRED_REQUIREMENT_COUNT_INVALID",
                    *[
                        requirement.requirement_id
                        for requirement in plan.retrieval_requirements
                        if requirement.required
                    ],
                ),
            ]
        )

    strict_required = (
        plan.reasoning_mode is ReasoningMode.PROGRAM
        or plan.formula_id is not None
        or program.formula_id is not None
        or plan.question_type
        in {
            QuestionType.DERIVED_RATIO,
            QuestionType.MULTI_PERIOD,
            QuestionType.AGGREGATE,
        }
        or program.question_type
        in {
            QuestionType.DERIVED_RATIO,
            QuestionType.MULTI_PERIOD,
            QuestionType.AGGREGATE,
        }
        or len(plan.retrieval_requirements) != 1
    )
    effective_profile = (
        VerifyProfile.STRICT
        if strict_required
        or request.verify_profile is VerifyProfile.STRICT
        or plan.verify_profile is VerifyProfile.STRICT
        else VerifyProfile.LIGHT
    )
    return VerificationProfileDecision(effective_profile, checks)
