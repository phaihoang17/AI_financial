"""Seven deterministic oracle-evidence fixtures for TASK-067."""

from __future__ import annotations

from typing import Optional, Sequence

from src.evaluation.oracle_reasoning import (
    OracleEvidence,
    OracleReasoningCase,
    OracleReasoningFailureStage,
)
from src.evidence.m5_schemas import (
    ScaleUnitResolution,
    ScaleUnitResolutionStatus,
    SchemaLinkMatchBasis,
    SchemaLinkResult,
    SchemaLinkStatus,
    make_value_placeholder,
)
from src.evidence.schemas import CanonicalDecimal, EvidenceItem, Scale
from src.indexing.schemas import (
    HeaderPathEntry,
    NumericParseResult,
    NumericParseStatus,
)
from src.programmer.generator import ProgrammerFailureCode
from src.programmer.schemas import (
    Program,
    ProgramInput,
    ProgramOperation,
    ProgramOutputKind,
    ProgramStep,
)
from src.retrieval.evidence import CellLocation, MatchBasis, ProvenanceRole
from src.sandbox.interpreter import InterpreterFailureCode
from src.sandbox.schemas import ExecutionDatum, ExecutionOutput
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from src.supervisor.schemas import (
    EvidenceSource,
    Plan,
    QuestionType,
    RetrievalRequirement,
    TableClass,
)
from src.understanding.requested_scale_unit_parser import RequestedScale
from src.understanding.schemas import StatementScope


def _plan(
    question_type: str,
    periods: Sequence[str],
    *,
    derived_target: Optional[str],
    formula_id: Optional[str],
    reasoning_mode: str,
    model_tier: str,
) -> Plan:
    requirements = [
        RetrievalRequirement.create(
            EvidenceSource.TABLE,
            TableClass.INCOME_STATEMENT,
            "LNST",
            period,
        )
        for period in periods
    ]
    return Plan.from_dict(
        {
            "question_type": question_type,
            "company": {"name": "Test Company", "ticker": "AAA"},
            "periods": list(periods),
            "period_kind": "NAM",
            "statement_scope": "HOP_NHAT",
            "target_metrics": ["LNST"],
            "derived_target": derived_target,
            "formula_id": formula_id,
            "tables_needed": ["INCOME_STATEMENT"],
            "retrieval_requirements": [item.to_dict() for item in requirements],
            "evidence_sources": ["TABLE"],
            "reasoning_mode": reasoning_mode,
            "requires_scale_resolution": True,
            "model_tier": model_tier,
            "verify_profile": "LIGHT" if question_type == "LOOKUP" else "STRICT",
            "max_retries": 1 if question_type == "LOOKUP" else 2,
            "confidence": 1.0,
            "abstain": False,
            "abstain_reason": None,
        }
    )


def _oracle_evidence(
    case_id: str,
    requirement: RetrievalRequirement,
    value: str,
    *,
    source_scale: Scale,
    source_unit: Optional[str],
    requested_scale: Optional[RequestedScale] = None,
    requested_unit: Optional[str] = None,
) -> OracleEvidence:
    period = requirement.period or "unknown"
    evidence_id = f"oracle-{case_id}-{period}"
    source_cell_id = f"cell-{case_id}-{period}"
    row_path = [HeaderPathEntry(f"row-{case_id}-{period}", "LNST")]
    column_path = [HeaderPathEntry(f"column-{case_id}-{period}", period)]
    item = EvidenceItem(
        evidence_id=evidence_id,
        source_type=EvidenceSource.TABLE,
        report_ref="oracle-report",
        page_ref="oracle-page",
        table_ref="oracle-income-statement",
        associated_table_ref=None,
        statement_scope=StatementScope.HOP_NHAT,
        period=period,
        metric="LNST",
        row_label="LNST",
        column_label=period,
        row_path=["LNST"],
        column_path=[period],
        text_span=None,
        raw_value=value,
        normalized_value=CanonicalDecimal(value),
        unit=None,
        scale=None,
        scale_source=None,
        retrieval_score=None,
        rerank_score=None,
    )
    location = CellLocation(
        location_id=f"location-{case_id}-{period}",
        candidate_id=f"candidate-{case_id}-{period}",
        representation_id=f"representation-{case_id}-{period}",
        chunk_id=f"chunk-{case_id}-{period}",
        report_id="oracle-report",
        page_id="oracle-page",
        table_id="oracle-income-statement",
        source_cell_id=source_cell_id,
        anchor_row=1,
        anchor_column=1,
        row_path=row_path,
        column_path=column_path,
        normalized_text=value,
        numeric=NumericParseResult(
            NumericParseStatus.PARSED,
            value,
            value,
            value,
            False,
        ),
        provenance_role=ProvenanceRole.PRIMARY,
        matched_metric="LNST",
        matched_period=period,
        match_basis=MatchBasis.EXACT_ROW_PATH,
    )
    schema_link = SchemaLinkResult(
        requirement_id=requirement.requirement_id,
        evidence_id=evidence_id,
        status=SchemaLinkStatus.RESOLVED,
        metric="LNST",
        period=period,
        row_path=row_path,
        column_path=column_path,
        matched_source_cell_id=source_cell_id,
        match_basis=SchemaLinkMatchBasis.EXACT_METRIC_AND_PERIOD,
    )
    hint_id = f"oracle-hint-{case_id}-{period}"
    scale_resolution = ScaleUnitResolution(
        evidence_id=evidence_id,
        status=ScaleUnitResolutionStatus.RESOLVED,
        source_scale=source_scale,
        source_unit=source_unit,
        requested_output_scale=requested_scale,
        requested_output_unit=requested_unit,
        winning_hint_ids=[hint_id],
        considered_hint_ids=[hint_id],
    )
    return OracleEvidence(item, location, schema_link, scale_resolution)


def _expected_program(
    plan: Plan,
    evidence: Sequence[OracleEvidence],
    *,
    operation: ProgramOperation,
    output_kind: ProgramOutputKind,
    formula_id: Optional[str],
    renamed_graph: bool = False,
) -> Program:
    inputs = [
        ProgramInput(
            input_id=(f"operand_{index}" if renamed_graph else f"input_{index}"),
            placeholder=make_value_placeholder(item.item.evidence_id),
            evidence_id=item.item.evidence_id,
            requirement_id=item.schema_link.requirement_id,
        )
        for index, item in enumerate(evidence)
    ]
    step_id = "oracle_output" if renamed_graph else "step_output"
    step = ProgramStep(
        step_id=step_id,
        operation=operation,
        input_refs=[item.input_id for item in inputs],
        formula_id=formula_id,
    )
    return Program.create(
        formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
        question_type=plan.question_type,
        formula_id=plan.formula_id,
        inputs=inputs,
        steps=[step],
        output_ref=step_id,
        output_kind=output_kind,
    )


def _output(
    kind: ProgramOutputKind,
    values: Sequence[tuple[str, Scale, Optional[str]]],
) -> ExecutionOutput:
    return ExecutionOutput(
        kind=kind,
        values=[
            ExecutionDatum(CanonicalDecimal(value), scale, unit)
            for value, scale, unit in values
        ],
    )


def oracle_reasoning_fixture_cases() -> list[OracleReasoningCase]:
    cases: list[OracleReasoningCase] = []

    lookup = _plan(
        "LOOKUP",
        ("2015",),
        derived_target=None,
        formula_id=None,
        reasoning_mode="DIRECT",
        model_tier="CHEAP",
    )
    lookup_evidence = [
        _oracle_evidence(
            "lookup",
            lookup.retrieval_requirements[0],
            "125",
            source_scale=Scale.MILLION,
            source_unit="VND",
        )
    ]
    cases.append(
        OracleReasoningCase(
            "LOOKUP",
            lookup,
            lookup_evidence,
            _expected_program(
                lookup,
                lookup_evidence,
                operation=ProgramOperation.IDENTITY,
                output_kind=ProgramOutputKind.SCALAR,
                formula_id=None,
            ),
            _output(
                ProgramOutputKind.SCALAR,
                (("125", Scale.MILLION, "VND"),),
            ),
            None,
            None,
        )
    )

    compare = _plan(
        "MULTI_PERIOD",
        ("2014", "2015"),
        derived_target=None,
        formula_id=None,
        reasoning_mode="DIRECT",
        model_tier="CHEAP",
    )
    compare_evidence = [
        _oracle_evidence(
            "compare",
            requirement,
            value,
            source_scale=Scale.MILLION,
            source_unit="VND",
        )
        for requirement, value in zip(
            compare.retrieval_requirements, ("10", "20")
        )
    ]
    cases.append(
        OracleReasoningCase(
            "MULTI_PERIOD_COMPARE",
            compare,
            compare_evidence,
            _expected_program(
                compare,
                compare_evidence,
                operation=ProgramOperation.COLLECT,
                output_kind=ProgramOutputKind.ORDERED_VALUES,
                formula_id=None,
                renamed_graph=True,
            ),
            _output(
                ProgramOutputKind.ORDERED_VALUES,
                (
                    ("10", Scale.MILLION, "VND"),
                    ("20", Scale.MILLION, "VND"),
                ),
            ),
            None,
            None,
        )
    )

    growth = _plan(
        "MULTI_PERIOD",
        ("2014", "2015"),
        derived_target="GROWTH",
        formula_id="GROWTH_RATE",
        reasoning_mode="PROGRAM",
        model_tier="STRONG",
    )
    growth_evidence = [
        _oracle_evidence(
            "growth",
            growth.retrieval_requirements[0],
            "100",
            source_scale=Scale.MILLION,
            source_unit="VND",
        ),
        _oracle_evidence(
            "growth",
            growth.retrieval_requirements[1],
            "0.2",
            source_scale=Scale.BILLION,
            source_unit="VND",
        ),
    ]
    cases.append(
        OracleReasoningCase(
            "GROWTH_RATE",
            growth,
            growth_evidence,
            _expected_program(
                growth,
                growth_evidence,
                operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
                output_kind=ProgramOutputKind.SCALAR,
                formula_id="GROWTH_RATE",
            ),
            _output(
                ProgramOutputKind.SCALAR,
                (("100", Scale.PERCENT, None),),
            ),
            None,
            None,
        )
    )

    average = _plan(
        "AGGREGATE",
        ("2013", "2014", "2015"),
        derived_target="AVERAGE",
        formula_id="AVERAGE",
        reasoning_mode="PROGRAM",
        model_tier="STRONG",
    )
    average_evidence = [
        _oracle_evidence(
            "average",
            requirement,
            value,
            source_scale=Scale.MILLION,
            source_unit="VND",
            requested_scale=RequestedScale.MILLION,
            requested_unit="VND",
        )
        for requirement, value in zip(
            average.retrieval_requirements, ("1", "2", "3")
        )
    ]
    cases.append(
        OracleReasoningCase(
            "AVERAGE",
            average,
            average_evidence,
            _expected_program(
                average,
                average_evidence,
                operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
                output_kind=ProgramOutputKind.SCALAR,
                formula_id="AVERAGE",
            ),
            _output(
                ProgramOutputKind.SCALAR,
                (("2", Scale.MILLION, "VND"),),
            ),
            None,
            None,
        )
    )

    unsupported_ratio = _plan(
        "LOOKUP",
        ("2015",),
        derived_target=None,
        formula_id=None,
        reasoning_mode="DIRECT",
        model_tier="CHEAP",
    )
    unsupported_ratio.question_type = QuestionType.DERIVED_RATIO
    ratio_evidence = [
        _oracle_evidence(
            "unsupported-ratio",
            unsupported_ratio.retrieval_requirements[0],
            "10",
            source_scale=Scale.RAW,
            source_unit="VND",
        )
    ]
    cases.append(
        OracleReasoningCase(
            "UNSUPPORTED_RATIO",
            unsupported_ratio,
            ratio_evidence,
            None,
            None,
            OracleReasoningFailureStage.PROGRAM_GENERATION,
            ProgrammerFailureCode.UNSUPPORTED_DERIVED_RATIO.value,
        )
    )

    zero_growth = _plan(
        "MULTI_PERIOD",
        ("2014", "2015"),
        derived_target="GROWTH",
        formula_id="GROWTH_RATE",
        reasoning_mode="PROGRAM",
        model_tier="STRONG",
    )
    zero_evidence = [
        _oracle_evidence(
            "division-zero",
            requirement,
            value,
            source_scale=Scale.RAW,
            source_unit="VND",
        )
        for requirement, value in zip(
            zero_growth.retrieval_requirements, ("0", "10")
        )
    ]
    cases.append(
        OracleReasoningCase(
            "DIVISION_BY_ZERO",
            zero_growth,
            zero_evidence,
            _expected_program(
                zero_growth,
                zero_evidence,
                operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
                output_kind=ProgramOutputKind.SCALAR,
                formula_id="GROWTH_RATE",
            ),
            None,
            OracleReasoningFailureStage.ARITHMETIC,
            InterpreterFailureCode.DIVISION_BY_ZERO.value,
        )
    )

    conversion = _plan(
        "LOOKUP",
        ("2015",),
        derived_target=None,
        formula_id=None,
        reasoning_mode="DIRECT",
        model_tier="CHEAP",
    )
    conversion_evidence = [
        _oracle_evidence(
            "scale-conversion",
            conversion.retrieval_requirements[0],
            "2500",
            source_scale=Scale.MILLION,
            source_unit="VND",
            requested_scale=RequestedScale.BILLION,
            requested_unit="VND",
        )
    ]
    cases.append(
        OracleReasoningCase(
            "SCALE_CONVERSION",
            conversion,
            conversion_evidence,
            _expected_program(
                conversion,
                conversion_evidence,
                operation=ProgramOperation.IDENTITY,
                output_kind=ProgramOutputKind.SCALAR,
                formula_id=None,
            ),
            _output(
                ProgramOutputKind.SCALAR,
                (("2.5", Scale.BILLION, "VND"),),
            ),
            None,
            None,
        )
    )
    return cases
