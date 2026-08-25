from src.evidence.m5_schemas import (
    ScaleUnitResolution,
    ScaleUnitResolutionStatus,
)
from src.evidence.schemas import CanonicalDecimal, EvidenceItem, Scale
from src.indexing.schemas import (
    HeaderPathEntry,
    NumericParseResult,
    NumericParseStatus,
    ScaleHintSource,
    ScaleHintStatus,
    SourceSpan,
)
from src.retrieval.evidence import CellLocation, MatchBasis, ProvenanceRole
from src.retrieval.schemas import HintAssociation, RetrievedScaleUnitHint, make_candidate_id
from src.supervisor.schemas import EvidenceSource, Plan, RetrievalRequirement, TableClass
from src.understanding.schemas import (
    CompanyUnderstanding,
    MetricUnderstanding,
    Operation,
    PeriodKind,
    PeriodUnderstanding,
    QueryUnderstanding,
    StatementScope,
    StatementScopeUnderstanding,
)


def candidate_id(name="one"):
    return make_candidate_id(EvidenceSource.TABLE, f"representation-{name}", f"chunk-{name}")


def understanding(requested_scale=None, requested_unit=None):
    return QueryUnderstanding(
        raw_question="LNST 2015",
        company=CompanyUnderstanding("AAA", "Test Company", "AAA", 1.0),
        periods=[PeriodUnderstanding("2015", PeriodKind.NAM, "2015")],
        statement_scope=StatementScopeUnderstanding(
            StatementScope.HOP_NHAT, False, 1.0
        ),
        metrics=[MetricUnderstanding("LNST", "LNST", 1.0)],
        operation=Operation.NONE,
        requested_scale=requested_scale,
        requested_unit=requested_unit,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )


def plan(*, requires_scale_resolution=True):
    requirement = RetrievalRequirement.create(
        EvidenceSource.TABLE,
        TableClass.INCOME_STATEMENT,
        "LNST",
        "2015",
    )
    return Plan.from_dict(
        {
            "question_type": "LOOKUP",
            "company": {"name": "Test Company", "ticker": "AAA"},
            "periods": ["2015"],
            "period_kind": "NAM",
            "statement_scope": "HOP_NHAT",
            "target_metrics": ["LNST"],
            "derived_target": None,
            "formula_id": None,
            "tables_needed": ["INCOME_STATEMENT"],
            "retrieval_requirements": [requirement.to_dict()],
            "evidence_sources": ["TABLE"],
            "reasoning_mode": "DIRECT",
            "requires_scale_resolution": requires_scale_resolution,
            "model_tier": "CHEAP",
            "verify_profile": "LIGHT",
            "max_retries": 1,
            "confidence": 1.0,
            "abstain": False,
            "abstain_reason": None,
        }
    )


def multi_period_plan():
    requirements = [
        RetrievalRequirement.create(
            EvidenceSource.TABLE,
            TableClass.INCOME_STATEMENT,
            "LNST",
            period,
        )
        for period in ("2014", "2015")
    ]
    return Plan.from_dict(
        {
            "question_type": "MULTI_PERIOD",
            "company": {"name": "Test Company", "ticker": "AAA"},
            "periods": ["2014", "2015"],
            "period_kind": "NAM",
            "statement_scope": "HOP_NHAT",
            "target_metrics": ["LNST"],
            "derived_target": "GROWTH",
            "formula_id": "GROWTH_RATE",
            "tables_needed": ["INCOME_STATEMENT"],
            "retrieval_requirements": [item.to_dict() for item in requirements],
            "evidence_sources": ["TABLE"],
            "reasoning_mode": "PROGRAM",
            "requires_scale_resolution": True,
            "model_tier": "STRONG",
            "verify_profile": "STRICT",
            "max_retries": 2,
            "confidence": 1.0,
            "abstain": False,
            "abstain_reason": None,
        }
    )


def grounded_cell(
    name="one",
    *,
    row_labels=("Báo cáo kết quả", "LNST"),
    column_labels=("2015",),
    raw_value="12.50",
    decimal_value="12.50",
    percent_literal=False,
):
    row_path = [
        HeaderPathEntry(f"{name}-row-{index}", label)
        for index, label in enumerate(row_labels)
    ]
    column_path = [
        HeaderPathEntry(f"{name}-column-{index}", label)
        for index, label in enumerate(column_labels)
    ]
    numeric = NumericParseResult(
        NumericParseStatus.PARSED,
        raw_value,
        decimal_value,
        decimal_value,
        percent_literal,
    )
    item = EvidenceItem(
        evidence_id=f"evidence-{name}",
        source_type=EvidenceSource.TABLE,
        report_ref="report",
        page_ref="page",
        table_ref="table",
        associated_table_ref=None,
        statement_scope=StatementScope.HOP_NHAT,
        period=None,
        metric=None,
        row_label=row_labels[-1] if row_labels else None,
        column_label=column_labels[-1] if column_labels else None,
        row_path=list(row_labels),
        column_path=list(column_labels),
        text_span=None,
        raw_value=raw_value,
        normalized_value=CanonicalDecimal(decimal_value),
        unit=None,
        scale=None,
        scale_source=None,
        retrieval_score=0.5,
        rerank_score=0.6,
    )
    location = CellLocation(
        location_id=f"location-{name}",
        candidate_id=candidate_id(name),
        representation_id=f"representation-{name}",
        chunk_id=f"chunk-{name}",
        report_id="report",
        page_id="page",
        table_id="table",
        source_cell_id=f"cell-{name}",
        anchor_row=2,
        anchor_column=1,
        row_path=row_path,
        column_path=column_path,
        normalized_text=raw_value,
        numeric=numeric,
        provenance_role=ProvenanceRole.PRIMARY,
        matched_metric=None,
        matched_period=None,
        match_basis=MatchBasis.STRUCTURAL,
    )
    return item, location


def hint(
    name,
    location,
    source_kind,
    scale,
    *,
    association=HintAssociation.DIRECT,
    unit=None,
    start=0,
):
    return RetrievedScaleUnitHint(
        hint_id=f"hint-{name}",
        candidate_id=location.candidate_id,
        source_kind=source_kind,
        source_ref=f"source-{name}",
        source_span=SourceSpan(start, start + 1),
        raw_hint_text=name,
        scale_candidate=scale,
        unit_candidate=unit,
        status=ScaleHintStatus.EXTRACTED,
        association=association,
    )


def resolved_scale(
    item,
    *,
    source_scale=Scale.MILLION,
    source_unit=None,
    requested_output_scale=None,
    requested_output_unit=None,
):
    return ScaleUnitResolution(
        evidence_id=item.evidence_id,
        status=ScaleUnitResolutionStatus.RESOLVED,
        source_scale=source_scale,
        source_unit=source_unit,
        requested_output_scale=requested_output_scale,
        requested_output_unit=requested_output_unit,
        winning_hint_ids=[f"hint-{item.evidence_id}"],
        considered_hint_ids=[f"hint-{item.evidence_id}"],
    )
