"""Deterministic TASK-097 final-answer construction."""

from __future__ import annotations

from typing import Sequence

from src.evidence.m5_schemas import SchemaLinkResult, SchemaLinkStatus
from src.evidence.schemas import EvidenceItem
from src.orchestration.schemas import (
    AnswerEvidenceReference,
    FinalAnswer,
    FinalResponse,
    FinalResponseStatus,
)
from src.programmer.schemas import Program
from src.sandbox.schemas import ExecutionResult
from src.supervisor.schemas import EvidenceSource, Plan
from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import RetryAction, RetryDirective, VerificationReport


def _used_input_ids(program: Program) -> set[str]:
    inputs = {item.input_id for item in program.inputs}
    steps = {item.step_id: item for item in program.steps}
    used: set[str] = set()
    visiting: set[str] = set()

    def visit(reference: str) -> None:
        if reference in inputs:
            used.add(reference)
            return
        if reference in visiting or reference not in steps:
            raise SchemaValidationError("Program output dependency closure is invalid")
        visiting.add(reference)
        for child in steps[reference].input_refs:
            visit(child)
        visiting.remove(reference)

    visit(program.output_ref)
    return used


def build_final_answer(
    plan: Plan,
    program: Program,
    execution_result: ExecutionResult,
    verification_report: VerificationReport,
    evidence_items: Sequence[EvidenceItem],
    schema_links: Sequence[SchemaLinkResult],
) -> FinalAnswer:
    """Copy verified output and cite only evidence in the Program dependency closure."""
    if not isinstance(plan, Plan) or not isinstance(program, Program):
        raise SchemaValidationError("answer builder requires Plan and Program")
    if not isinstance(execution_result, ExecutionResult) or not execution_result.success:
        raise SchemaValidationError("FinalAnswer requires successful execution")
    if execution_result.output is None:
        raise SchemaValidationError("FinalAnswer requires ExecutionOutput")
    if not isinstance(verification_report, VerificationReport) or not verification_report.passed:
        raise SchemaValidationError("FinalAnswer requires passed VerificationReport")
    if not all(isinstance(item, EvidenceItem) for item in evidence_items):
        raise SchemaValidationError("evidence_items must contain EvidenceItem values")
    if not all(isinstance(item, SchemaLinkResult) for item in schema_links):
        raise SchemaValidationError("schema_links must contain SchemaLinkResult values")

    evidence_by_id = {item.evidence_id: item for item in evidence_items}
    if len(evidence_by_id) != len(evidence_items):
        raise SchemaValidationError("evidence_items must have unique evidence_id values")
    used = _used_input_ids(program)
    requirement_order = {
        item.requirement_id: index
        for index, item in enumerate(plan.retrieval_requirements)
    }
    citations = []
    for program_input in sorted(
        (item for item in program.inputs if item.input_id in used),
        key=lambda item: (requirement_order[item.requirement_id], item.evidence_id),
    ):
        evidence = evidence_by_id.get(program_input.evidence_id)
        if evidence is None:
            raise SchemaValidationError("used Program evidence is unavailable")
        metric = evidence.metric
        period = evidence.period
        row_path = tuple(evidence.row_path)
        column_path = tuple(evidence.column_path)
        if evidence.source_type is EvidenceSource.TABLE:
            matches = [
                item for item in schema_links
                if item.requirement_id == program_input.requirement_id
                and item.evidence_id == program_input.evidence_id
                and item.status is SchemaLinkStatus.RESOLVED
            ]
            if len(matches) != 1:
                raise SchemaValidationError(
                    "used TABLE evidence requires one resolved SchemaLinkResult"
                )
            link = matches[0]
            metric = link.metric
            period = link.period
            row_path = tuple(item.label for item in link.row_path)
            column_path = tuple(item.label for item in link.column_path)
        citations.append(
            AnswerEvidenceReference(
                evidence_id=evidence.evidence_id,
                requirement_id=program_input.requirement_id,
                source_type=evidence.source_type,
                report_ref=evidence.report_ref,
                page_ref=evidence.page_ref,
                table_ref=evidence.table_ref,
                paragraph_ref=evidence.paragraph_ref,
                metric=metric,
                period=period,
                row_path=row_path,
                column_path=column_path,
            )
        )
    if not citations:
        raise SchemaValidationError("FinalAnswer requires used evidence citations")
    return FinalAnswer(
        question_type=plan.question_type,
        target_metrics=tuple(plan.target_metrics),
        derived_target=plan.derived_target,
        formula_id=plan.formula_id,
        output=execution_result.output,
        evidence=tuple(citations),
    )


def build_pass_response(
    *,
    plan: Plan,
    program: Program,
    execution_result: ExecutionResult,
    verification_report: VerificationReport,
    retry_directive: RetryDirective,
    evidence_items: Sequence[EvidenceItem],
    schema_links: Sequence[SchemaLinkResult],
) -> FinalResponse:
    if retry_directive.action is not RetryAction.PASS:
        raise SchemaValidationError("PASS response requires M8 PASS directive")
    answer = build_final_answer(
        plan,
        program,
        execution_result,
        verification_report,
        evidence_items,
        schema_links,
    )
    return FinalResponse(
        status=FinalResponseStatus.PASS,
        answer=answer,
        reason_code=None,
        message="Verified answer.",
        retry_state=retry_directive.next_state,
        failure_attribution=None,
    )
