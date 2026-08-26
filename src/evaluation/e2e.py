"""Deterministic retrieved-evidence M9 end-to-end evaluation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from src.evaluation.harness import EvaluationFailureStage
from src.orchestration.graph import OrchestrationGraph
from src.orchestration.schemas import (
    FailureAttribution,
    FinalAnswer,
    FinalResponseStatus,
)
from src.orchestration.state import M9State
from src.programmer.schemas import Program
from src.retrieval.evidence import EvidenceCompletenessResult
from src.sandbox.schemas import ExecutionFailureStage, ExecutionOutput
from src.supervisor.schemas import Plan
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError
from src.verification.schemas import VerificationFailureCategory


E2E_EVALUATION_SCHEMA_VERSION = "m9-retrieved-evidence-e2e-v1"
PRODUCTION_TABLE_E2E_STATUS = "BLOCKED"
PRODUCTION_TABLE_E2E_BLOCKER = "PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING"


@dataclass(frozen=True)
class ExecutionExpectation:
    success: bool
    output: Optional[ExecutionOutput]
    failure_stage: Optional[ExecutionFailureStage]
    failure_code: Optional[str]

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise SchemaValidationError("ExecutionExpectation.success must be boolean")
        if self.success:
            if (
                self.output is None
                or self.failure_stage is not None
                or self.failure_code is not None
            ):
                raise SchemaValidationError(
                    "successful execution expectation requires only output"
                )
        elif (
            self.output is not None
            or self.failure_stage is None
            or not isinstance(self.failure_code, str)
            or not self.failure_code
        ):
            raise SchemaValidationError(
                "failed execution expectation requires native stage/code"
            )


@dataclass(frozen=True)
class VerificationExpectation:
    passed: bool
    failure_category: Optional[VerificationFailureCategory]
    failure_code: Optional[str]

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise SchemaValidationError("VerificationExpectation.passed must be boolean")
        if self.passed != (
            self.failure_category is None and self.failure_code is None
        ):
            raise SchemaValidationError(
                "verification expectation failure fields must match passed"
            )


@dataclass(frozen=True)
class RetrievedEvidenceE2ECase:
    case_id: str
    raw_question: str
    graph_factory: Callable[[], OrchestrationGraph]
    expected_nlu: Optional[QueryUnderstanding]
    expected_plan: Optional[Plan]
    expected_evidence: Optional[EvidenceCompletenessResult]
    expected_program: Optional[Program]
    expected_execution: Optional[ExecutionExpectation]
    expected_verification: Optional[VerificationExpectation]
    expected_status: FinalResponseStatus
    expected_answer: Optional[FinalAnswer]
    expected_retries_used: Optional[int]
    expected_strong_escalated: bool
    expected_failure_attribution: Optional[FailureAttribution]

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise SchemaValidationError("case_id must be non-empty")
        if not isinstance(self.raw_question, str):
            raise SchemaValidationError("raw_question must be a string")
        if not callable(self.graph_factory):
            raise SchemaValidationError("graph_factory must be callable")
        if not isinstance(self.expected_status, FinalResponseStatus):
            raise SchemaValidationError("expected_status must be FinalResponseStatus")
        if not isinstance(self.expected_strong_escalated, bool):
            raise SchemaValidationError("expected_strong_escalated must be boolean")
        if self.expected_status is FinalResponseStatus.PASS:
            if self.expected_answer is None or self.expected_failure_attribution is not None:
                raise SchemaValidationError("PASS expectation requires answer and no failure")
        elif self.expected_answer is not None or self.expected_failure_attribution is None:
            raise SchemaValidationError("non-PASS expectation requires failure and no answer")


@dataclass(frozen=True)
class RetrievedEvidenceE2ECaseResult:
    case_id: str
    nlu_correct: bool
    plan_correct: bool
    evidence_correct: bool
    trace_correct: bool
    execution_correct: bool
    verification_correct: bool
    status_correct: bool
    answer_correct: bool
    retry_correct: bool
    escalation_correct: bool
    failure_attribution_correct: bool
    final_status: FinalResponseStatus
    retries_used: Optional[int]
    strong_escalated: bool
    terminal_failure: Optional[FailureAttribution]

    @property
    def passed(self) -> bool:
        return all(
            (
                self.nlu_correct,
                self.plan_correct,
                self.evidence_correct,
                self.trace_correct,
                self.execution_correct,
                self.verification_correct,
                self.status_correct,
                self.answer_correct,
                self.retry_correct,
                self.escalation_correct,
                self.failure_attribution_correct,
            )
        )

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "nlu_correct": self.nlu_correct,
            "plan_correct": self.plan_correct,
            "evidence_correct": self.evidence_correct,
            "trace_correct": self.trace_correct,
            "execution_correct": self.execution_correct,
            "verification_correct": self.verification_correct,
            "status_correct": self.status_correct,
            "answer_correct": self.answer_correct,
            "retry_correct": self.retry_correct,
            "escalation_correct": self.escalation_correct,
            "failure_attribution_correct": self.failure_attribution_correct,
            "final_status": self.final_status.value,
            "retries_used": self.retries_used,
            "strong_escalated": self.strong_escalated,
            "terminal_failure": (
                None
                if self.terminal_failure is None
                else self.terminal_failure.to_dict()
            ),
        }


def _contract_equal(expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is actual
    return expected.to_dict() == actual.to_dict()


def _execution_correct(expected: Optional[ExecutionExpectation], actual) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if expected.success != actual.success:
        return False
    if expected.success:
        return (
            actual.failure is None
            and actual.output is not None
            and expected.output.to_dict() == actual.output.to_dict()
        )
    return (
        actual.output is None
        and actual.failure is not None
        and actual.failure.stage is expected.failure_stage
        and actual.failure.code == expected.failure_code
    )


def _verification_correct(
    expected: Optional[VerificationExpectation], actual
) -> bool:
    if expected is None or actual is None:
        return expected is actual
    return (
        expected.passed == actual.passed
        and expected.failure_category is actual.failure_category
        and expected.failure_code == actual.failure_reason
    )


def evaluate_e2e_case(case: RetrievedEvidenceE2ECase) -> RetrievedEvidenceE2ECaseResult:
    if not isinstance(case, RetrievedEvidenceE2ECase):
        raise SchemaValidationError("case must be RetrievedEvidenceE2ECase")
    state: M9State = case.graph_factory().run(
        request_id=f"e2e-{case.case_id}", raw_question=case.raw_question
    ).state
    attempt = None if not state.attempts else state.attempts[-1]
    actual_plan = (
        None if state.supervisor_result is None else state.supervisor_result.plan
    )
    actual_evidence = None if attempt is None else attempt.evidence_completeness
    actual_program = (
        None
        if attempt is None
        or attempt.programmer_result is None
        or attempt.programmer_result.program is None
        else attempt.programmer_result.program
    )
    actual_execution = None if attempt is None else attempt.execution_result
    actual_verification = None if attempt is None else attempt.verification_report
    actual_answer = None if state.final_response is None else state.final_response.answer
    retries_used = None if state.retry_state is None else state.retry_state.retries_used
    strong_escalated = (
        False if state.retry_state is None else state.retry_state.strong_escalated
    )
    return RetrievedEvidenceE2ECaseResult(
        case_id=case.case_id,
        nlu_correct=_contract_equal(case.expected_nlu, state.query_understanding),
        plan_correct=_contract_equal(case.expected_plan, actual_plan),
        evidence_correct=_contract_equal(case.expected_evidence, actual_evidence),
        trace_correct=_contract_equal(case.expected_program, actual_program),
        execution_correct=_execution_correct(case.expected_execution, actual_execution),
        verification_correct=_verification_correct(
            case.expected_verification, actual_verification
        ),
        status_correct=state.outcome is case.expected_status,
        answer_correct=_contract_equal(case.expected_answer, actual_answer),
        retry_correct=retries_used == case.expected_retries_used,
        escalation_correct=strong_escalated == case.expected_strong_escalated,
        failure_attribution_correct=_contract_equal(
            case.expected_failure_attribution, state.failure_attribution
        ),
        final_status=state.outcome,
        retries_used=retries_used,
        strong_escalated=strong_escalated,
        terminal_failure=state.failure_attribution,
    )


@dataclass(frozen=True)
class RetrievedEvidenceE2EReport:
    case_results: tuple[RetrievedEvidenceE2ECaseResult, ...]
    expected_statuses: tuple[FinalResponseStatus, ...]
    expected_retries_used: tuple[Optional[int], ...]

    @staticmethod
    def _rate(values: Sequence[bool]) -> float:
        return 0.0 if not values else sum(values) / len(values)

    def to_dict(self) -> dict:
        results = self.case_results
        status_accuracy = {}
        for status in FinalResponseStatus:
            selected = [
                item.status_correct
                for item, expected in zip(results, self.expected_statuses)
                if expected is status
            ]
            status_accuracy[status.value] = self._rate(selected)
        retry_cases = [
            item
            for item, expected_retries in zip(results, self.expected_retries_used)
            if expected_retries is not None and expected_retries > 0
        ]
        retry_success_rate = self._rate(
            [item.final_status is FinalResponseStatus.PASS for item in retry_cases]
        )
        retry_distribution: dict[str, int] = {}
        for item in results:
            key = "NONE" if item.retries_used is None else str(item.retries_used)
            retry_distribution[key] = retry_distribution.get(key, 0) + 1
        terminal_distribution: dict[str, int] = {}
        for item in results:
            if item.terminal_failure is None:
                continue
            key = f"{item.terminal_failure.stage.value}/{item.terminal_failure.code}"
            terminal_distribution[key] = terminal_distribution.get(key, 0) + 1
        fields = {
            "nlu_exactness": "nlu_correct",
            "plan_exactness": "plan_correct",
            "evidence_completeness": "evidence_correct",
            "trace_correctness": "trace_correct",
            "execution_correctness": "execution_correct",
            "verification_correctness": "verification_correct",
            "final_status_accuracy": "status_correct",
            "answer_correctness": "answer_correct",
        }
        return {
            "schema_version": E2E_EVALUATION_SCHEMA_VERSION,
            "mode": "FIXTURE",
            "production_table_e2e_status": PRODUCTION_TABLE_E2E_STATUS,
            "production_table_e2e_blocker": PRODUCTION_TABLE_E2E_BLOCKER,
            "total_cases": len(results),
            "passed_cases": sum(item.passed for item in results),
            "failed_cases": sum(not item.passed for item in results),
            "status_accuracy": status_accuracy,
            **{
                metric: self._rate([getattr(item, field) for item in results])
                for metric, field in fields.items()
            },
            "retry_success_rate": retry_success_rate,
            "retries_used_distribution": dict(sorted(retry_distribution.items())),
            "strong_escalation_count": sum(
                item.strong_escalated for item in results
            ),
            "terminal_failure_distribution": dict(
                sorted(terminal_distribution.items())
            ),
            "case_results": [item.to_dict() for item in results],
        }


def evaluate_e2e_cases(
    cases: Sequence[RetrievedEvidenceE2ECase],
) -> RetrievedEvidenceE2EReport:
    if not isinstance(cases, (list, tuple)) or not all(
        isinstance(item, RetrievedEvidenceE2ECase) for item in cases
    ):
        raise SchemaValidationError(
            "cases must contain RetrievedEvidenceE2ECase values"
        )
    case_ids = [item.case_id for item in cases]
    if len(case_ids) != len(set(case_ids)):
        raise SchemaValidationError("E2E fixture case IDs must be unique")
    return RetrievedEvidenceE2EReport(
        tuple(evaluate_e2e_case(item) for item in cases),
        tuple(item.expected_status for item in cases),
        tuple(item.expected_retries_used for item in cases),
    )
