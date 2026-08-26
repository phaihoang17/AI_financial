"""Deterministic TASK-067 oracle-evidence reasoning evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Sequence

from src.evaluation.program_trace import (
    compare_program_traces,
    normalized_program_equivalence,
)
from src.evaluation.reasoning import ReasoningEvaluationResult, TraceComparison
from src.evidence.m5_schemas import ScaleUnitResolution, SchemaLinkResult
from src.evidence.numeric_masking import NumericMaskingError, mask_numeric_evidence
from src.evidence.schemas import EvidenceItem
from src.evidence.value_binder import ValueBindingError, build_binding_map
from src.programmer.generator import ProgrammerFailureCode, generate_program
from src.programmer.schemas import Program, ProgrammerInput, ProgrammerStatus
from src.programmer.validator import ProgramValidationFailureCode
from src.retrieval.evidence import CellLocation
from src.sandbox.executor import execute_sandboxed
from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    ExecutionFailureStage,
    ExecutionOutput,
    SandboxExecutionRequest,
)
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from src.supervisor.schemas import Plan
from src.understanding.schemas import SchemaValidationError


ORACLE_REASONING_EVALUATION_SCHEMA_VERSION = "m6-oracle-reasoning-eval-v1"


class OracleReasoningFailureStage(str, Enum):
    PROGRAM_GENERATION = "PROGRAM_GENERATION"
    VALIDATION = "VALIDATION"
    BINDING = "BINDING"
    CONVERSION = "CONVERSION"
    ARITHMETIC = "ARITHMETIC"
    SANDBOX_INFRASTRUCTURE = "SANDBOX_INFRASTRUCTURE"


@dataclass(frozen=True)
class OracleEvidence:
    """One directly supplied, fully grounded oracle evidence chain."""

    item: EvidenceItem
    location: CellLocation
    schema_link: SchemaLinkResult
    scale_resolution: ScaleUnitResolution

    def __post_init__(self) -> None:
        if not isinstance(self.item, EvidenceItem):
            raise SchemaValidationError("OracleEvidence.item must be EvidenceItem")
        if not isinstance(self.location, CellLocation):
            raise SchemaValidationError(
                "OracleEvidence.location must be CellLocation"
            )
        if not isinstance(self.schema_link, SchemaLinkResult):
            raise SchemaValidationError(
                "OracleEvidence.schema_link must be SchemaLinkResult"
            )
        if not isinstance(self.scale_resolution, ScaleUnitResolution):
            raise SchemaValidationError(
                "OracleEvidence.scale_resolution must be ScaleUnitResolution"
            )


@dataclass(frozen=True)
class OracleReasoningCase:
    case_id: str
    plan: Plan
    oracle_evidence: Sequence[OracleEvidence]
    expected_program: Optional[Program]
    expected_output: Optional[ExecutionOutput]
    expected_failure_stage: Optional[OracleReasoningFailureStage]
    expected_failure_code: Optional[str]

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise SchemaValidationError("case_id must be a non-empty string")
        if not isinstance(self.plan, Plan):
            raise SchemaValidationError("plan must be a Plan")
        if not isinstance(self.oracle_evidence, (list, tuple)) or not all(
            isinstance(item, OracleEvidence) for item in self.oracle_evidence
        ):
            raise SchemaValidationError(
                "oracle_evidence must contain OracleEvidence values"
            )
        if not self.oracle_evidence:
            raise SchemaValidationError("oracle_evidence must be non-empty")
        if self.expected_program is not None and not isinstance(
            self.expected_program, Program
        ):
            raise SchemaValidationError("expected_program must be Program or null")
        if self.expected_output is not None and not isinstance(
            self.expected_output, ExecutionOutput
        ):
            raise SchemaValidationError(
                "expected_output must be ExecutionOutput or null"
            )
        if self.expected_failure_stage is not None and not isinstance(
            self.expected_failure_stage, OracleReasoningFailureStage
        ):
            raise SchemaValidationError(
                "expected_failure_stage must be OracleReasoningFailureStage or null"
            )
        if self.expected_failure_code is not None and (
            not isinstance(self.expected_failure_code, str)
            or not self.expected_failure_code
        ):
            raise SchemaValidationError(
                "expected_failure_code must be a non-empty string or null"
            )
        expects_failure = self.expected_failure_stage is not None
        if expects_failure != (self.expected_failure_code is not None):
            raise SchemaValidationError(
                "expected failure stage and code must either both be set or both be null"
            )
        if expects_failure == (self.expected_output is not None):
            raise SchemaValidationError(
                "a case must expect exactly one output or typed failure"
            )
        if not expects_failure and self.expected_program is None:
            raise SchemaValidationError(
                "successful cases require an expected Program trace"
            )


@dataclass(frozen=True)
class OracleReasoningCaseResult:
    case_id: str
    reasoning_result: ReasoningEvaluationResult
    actual_program_id: Optional[str]
    execution_success: bool
    actual_output: Optional[ExecutionOutput]
    failure_stage: Optional[OracleReasoningFailureStage]
    failure_code: Optional[str]
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "reasoning_result": self.reasoning_result.to_dict(),
            "actual_program_id": self.actual_program_id,
            "execution_success": self.execution_success,
            "actual_output": (
                None if self.actual_output is None else self.actual_output.to_dict()
            ),
            "failure_stage": (
                None if self.failure_stage is None else self.failure_stage.value
            ),
            "failure_code": self.failure_code,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class OracleReasoningReport:
    case_results: Sequence[OracleReasoningCaseResult]

    @property
    def case_count(self) -> int:
        return len(self.case_results)

    @property
    def passed_count(self) -> int:
        return sum(item.passed for item in self.case_results)

    @property
    def failed_count(self) -> int:
        return self.case_count - self.passed_count

    @staticmethod
    def _rate(correct: int, total: int) -> float:
        return 0.0 if total == 0 else correct / total

    @property
    def execution_accuracy(self) -> float:
        return self._rate(
            sum(item.reasoning_result.execution_correct for item in self.case_results),
            self.case_count,
        )

    @property
    def trace_accuracy(self) -> float:
        return self._rate(
            sum(
                item.reasoning_result.trace_comparison
                is not TraceComparison.DIFFERENT
                for item in self.case_results
            ),
            self.case_count,
        )

    @property
    def answer_accuracy(self) -> float:
        return self._rate(
            sum(item.reasoning_result.answer_correct for item in self.case_results),
            self.case_count,
        )

    def to_dict(self) -> Dict[str, Any]:
        trace_distribution = {
            comparison.value: sum(
                item.reasoning_result.trace_comparison is comparison
                for item in self.case_results
            )
            for comparison in TraceComparison
        }
        failure_distribution = {
            "NONE": sum(item.failure_stage is None for item in self.case_results),
            **{
                stage.value: sum(
                    item.failure_stage is stage for item in self.case_results
                )
                for stage in OracleReasoningFailureStage
            },
        }
        return {
            "schema_version": ORACLE_REASONING_EVALUATION_SCHEMA_VERSION,
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "execution_accuracy": self.execution_accuracy,
            "trace_accuracy": self.trace_accuracy,
            "answer_accuracy": self.answer_accuracy,
            "trace_distribution": trace_distribution,
            "failure_distribution": failure_distribution,
            "case_results": [item.to_dict() for item in self.case_results],
        }


def outputs_equal(
    expected: Optional[ExecutionOutput], actual: Optional[ExecutionOutput]
) -> bool:
    """Compare output kind, order, canonical decimal, scale, and unit exactly."""
    if expected is None or actual is None:
        return expected is actual
    return expected.to_dict() == actual.to_dict()


def _trace_comparison(
    expected: Optional[Program], actual: Optional[Program]
) -> TraceComparison:
    if expected is None or actual is None:
        return (
            TraceComparison.EXACT
            if expected is actual
            else TraceComparison.DIFFERENT
        )
    return compare_program_traces(
        expected, actual, normalized_program_equivalence
    )


def _execution_failure_stage(
    stage: ExecutionFailureStage,
) -> OracleReasoningFailureStage:
    if stage in {ExecutionFailureStage.POLICY, ExecutionFailureStage.VALIDATION}:
        return OracleReasoningFailureStage.VALIDATION
    if stage is ExecutionFailureStage.BINDING:
        return OracleReasoningFailureStage.BINDING
    if stage is ExecutionFailureStage.CONVERSION:
        return OracleReasoningFailureStage.CONVERSION
    if stage is ExecutionFailureStage.ARITHMETIC:
        return OracleReasoningFailureStage.ARITHMETIC
    return OracleReasoningFailureStage.SANDBOX_INFRASTRUCTURE


def _generation_failure_stage(code: str) -> OracleReasoningFailureStage:
    validation_codes = {
        *(item.value for item in ProgramValidationFailureCode),
        ProgrammerFailureCode.INVALID_PROGRAMMER_INPUT.value,
        ProgrammerFailureCode.UNKNOWN_REQUIREMENT.value,
        ProgrammerFailureCode.MISSING_REQUIRED_EVIDENCE.value,
        ProgrammerFailureCode.AMBIGUOUS_REQUIRED_EVIDENCE.value,
    }
    if code in validation_codes:
        return OracleReasoningFailureStage.VALIDATION
    return OracleReasoningFailureStage.PROGRAM_GENERATION


def evaluate_oracle_reasoning_case(
    case: OracleReasoningCase,
) -> OracleReasoningCaseResult:
    """Evaluate one supplied oracle chain without invoking retrieval."""
    evidence = [item.item for item in case.oracle_evidence]
    locations = {
        item.item.evidence_id: item.location for item in case.oracle_evidence
    }
    links = [item.schema_link for item in case.oracle_evidence]
    resolutions = [item.scale_resolution for item in case.oracle_evidence]

    actual_program: Optional[Program] = None
    actual_output: Optional[ExecutionOutput] = None
    failure_stage: Optional[OracleReasoningFailureStage] = None
    failure_code: Optional[str] = None
    execution_success = False

    try:
        masked = mask_numeric_evidence(
            case.plan, evidence, locations, links, resolutions
        )
    except NumericMaskingError as error:
        failure_stage = OracleReasoningFailureStage.VALIDATION
        failure_code = error.code.value
    except SchemaValidationError:
        failure_stage = OracleReasoningFailureStage.VALIDATION
        failure_code = "INVALID_ORACLE_EVIDENCE"
    else:
        try:
            binding_map = build_binding_map(
                masked, evidence, locations, resolutions
            )
        except ValueBindingError as error:
            failure_stage = OracleReasoningFailureStage.BINDING
            failure_code = error.code.value
        except SchemaValidationError:
            failure_stage = OracleReasoningFailureStage.BINDING
            failure_code = "INVALID_BINDING_INPUT"
        else:
            programmer_input = ProgrammerInput(
                plan=case.plan,
                masked_evidence=masked,
                formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
            )
            programmer_result = generate_program(programmer_input)
            if programmer_result.status is ProgrammerStatus.REJECTED:
                failure_code = programmer_result.failure_code
                assert failure_code is not None
                failure_stage = _generation_failure_stage(failure_code)
            else:
                actual_program = programmer_result.program
                assert actual_program is not None
                execution = execute_sandboxed(
                    SandboxExecutionRequest(
                        schema_version=EXECUTION_REQUEST_SCHEMA_VERSION,
                        program=actual_program,
                        programmer_input=programmer_input,
                        binding_map=binding_map,
                        limits_profile_id=EXECUTION_LIMITS_PROFILE_ID,
                    )
                )
                execution_success = execution.success
                actual_output = execution.output
                if execution.failure is not None:
                    failure_stage = _execution_failure_stage(
                        execution.failure.stage
                    )
                    failure_code = execution.failure.code

    expected_failure = case.expected_failure_stage is not None
    attribution_correct = (
        failure_stage is case.expected_failure_stage
        and failure_code == case.expected_failure_code
    )
    exact_output = outputs_equal(case.expected_output, actual_output)
    execution_correct = (
        attribution_correct
        if expected_failure
        else execution_success and exact_output and failure_stage is None
    )
    answer_correct = (
        actual_output is None
        if expected_failure
        else execution_success and exact_output
    )
    trace_comparison = _trace_comparison(case.expected_program, actual_program)
    reasoning_result = ReasoningEvaluationResult(
        execution_correct=execution_correct,
        trace_comparison=trace_comparison,
        answer_correct=answer_correct,
    )
    return OracleReasoningCaseResult(
        case_id=case.case_id,
        reasoning_result=reasoning_result,
        actual_program_id=(
            None if actual_program is None else actual_program.program_id
        ),
        execution_success=execution_success,
        actual_output=actual_output,
        failure_stage=failure_stage,
        failure_code=failure_code,
        passed=(
            reasoning_result.execution_correct
            and reasoning_result.trace_comparison is not TraceComparison.DIFFERENT
            and reasoning_result.answer_correct
            and attribution_correct
        ),
    )


def evaluate_oracle_reasoning_cases(
    cases: Sequence[OracleReasoningCase],
) -> OracleReasoningReport:
    if not isinstance(cases, (list, tuple)) or not all(
        isinstance(case, OracleReasoningCase) for case in cases
    ):
        raise SchemaValidationError(
            "cases must contain OracleReasoningCase values"
        )
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise SchemaValidationError("case IDs must be unique")
    return OracleReasoningReport(
        case_results=[evaluate_oracle_reasoning_case(case) for case in cases]
    )
