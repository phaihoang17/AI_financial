from dataclasses import replace
from typing import Iterable, Sequence

from src.evidence.m5_schemas import (
    BindingMap,
    ScaleUnitResolution,
    ScaleUnitResolutionStatus,
    SchemaLinkResult,
    ValueBinding,
    make_value_placeholder,
)
from src.evidence.schema_linker import link_schema
from src.evidence.schemas import CanonicalDecimal, EvidenceItem, Scale
from src.programmer.schemas import (
    Program,
    ProgramInput,
    ProgramOperation,
    ProgramOutputKind,
    ProgramStep,
    ProgrammerInput,
)
from src.programmer.generator import generate_program
from src.retrieval.evidence import (
    CellLocation,
    EvidenceCompletenessResult,
    EvidenceRequirementResult,
    generate_plan_evidence_requirements,
)
from src.sandbox.schemas import (
    EXECUTION_RESULT_SCHEMA_VERSION,
    ExecutionDatum,
    ExecutionFailure,
    ExecutionFailureStage,
    ExecutionOutput,
    ExecutionResult,
)
from src.supervisor.schemas import (
    EvidenceSource,
    Plan,
    RetrievalRequirement,
    TableClass,
    VerifyProfile,
)
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from src.understanding.schemas import StatementScope
from src.verification.schemas import VerificationRequest
from tests.evidence.m5_helpers import grounded_cell


def make_plan(sources: Sequence[EvidenceSource]) -> Plan:
    requirements = [
        RetrievalRequirement.create(
            source,
            TableClass.INCOME_STATEMENT if source is EvidenceSource.TABLE else None,
            "LNST",
            "2015",
        )
        for source in sources
    ]
    profile = VerifyProfile.LIGHT if len(requirements) == 1 else VerifyProfile.STRICT
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
            "tables_needed": (
                ["INCOME_STATEMENT"]
                if EvidenceSource.TABLE in sources
                else []
            ),
            "retrieval_requirements": [item.to_dict() for item in requirements],
            "evidence_sources": [source.value for source in sources],
            "reasoning_mode": "DIRECT",
            "requires_scale_resolution": EvidenceSource.TABLE in sources,
            "model_tier": "CHEAP",
            "verify_profile": profile.value,
            "max_retries": 1,
            "confidence": 1.0,
            "abstain": False,
            "abstain_reason": None,
        }
    )


def success_execution(program: Program) -> ExecutionResult:
    return ExecutionResult(
        schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
        program_id=program.program_id,
        success=True,
        output=ExecutionOutput(
            kind=ProgramOutputKind.SCALAR,
            values=[ExecutionDatum(CanonicalDecimal("12.5"), Scale.MILLION, None)],
        ),
        failure=None,
        execution_ms=1,
    )


def failed_execution() -> ExecutionResult:
    return ExecutionResult(
        schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
        program_id="program",
        success=False,
        output=None,
        failure=ExecutionFailure(
            ExecutionFailureStage.ARITHMETIC,
            "DIVISION_BY_ZERO",
            "division by zero",
        ),
        execution_ms=1,
    )


def table_pair() -> tuple[EvidenceItem, CellLocation]:
    item, location = grounded_cell()
    item = replace(
        item,
        period="2015",
        metric="LNST",
        ticker="AAA",
        company_name="Test Company",
        report_year=2015,
    )
    location = replace(
        location,
        matched_metric="LNST",
        matched_period="2015",
        ticker="AAA",
        company_name="Test Company",
        report_year=2015,
        statement_scope=StatementScope.HOP_NHAT,
        table_class=TableClass.INCOME_STATEMENT,
    )
    return item, location


def text_item(*, linked: bool = False) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="evidence-text",
        source_type=EvidenceSource.TEXT,
        report_ref="report",
        page_ref="page",
        table_ref=None,
        associated_table_ref="table" if linked else None,
        statement_scope=StatementScope.HOP_NHAT,
        period="2015",
        metric="LNST",
        row_label=None,
        column_label=None,
        row_path=[],
        column_path=[],
        text_span="Lợi nhuận sau thuế năm 2015 là 12,5 triệu đồng.",
        raw_value=None,
        normalized_value=None,
        unit=None,
        scale=None,
        scale_source=None,
        retrieval_score=0.5,
        rerank_score=0.6,
        ticker="AAA",
        company_name="Test Company",
        report_year=2015,
        paragraph_ref="paragraph",
        provenance_link_ids=["link"] if linked else [],
    )


def execution_contracts(
    plan: Plan, evidence_items: Sequence[EvidenceItem]
) -> tuple[Program, list[ScaleUnitResolution], BindingMap]:
    evidence_by_source = {
        item.source_type: item for item in evidence_items
    }
    inputs = []
    resolutions = []
    bindings = []
    for index, requirement in enumerate(plan.retrieval_requirements):
        item = evidence_by_source[requirement.source_type]
        placeholder = make_value_placeholder(item.evidence_id)
        inputs.append(
            ProgramInput(
                input_id=f"input_{index}",
                placeholder=placeholder,
                evidence_id=item.evidence_id,
                requirement_id=requirement.requirement_id,
            )
        )
        resolutions.append(
            ScaleUnitResolution(
                evidence_id=item.evidence_id,
                status=ScaleUnitResolutionStatus.RESOLVED,
                source_scale=Scale.MILLION,
                source_unit=None,
                requested_output_scale=None,
                requested_output_unit=None,
                winning_hint_ids=[f"hint-{item.evidence_id}"],
                considered_hint_ids=[f"hint-{item.evidence_id}"],
            )
        )
        bindings.append(
            ValueBinding(
                placeholder=placeholder,
                evidence_id=item.evidence_id,
                value=item.normalized_value or CanonicalDecimal("12.5"),
                source_scale=Scale.MILLION,
                source_unit=None,
                requested_output_scale=None,
                requested_output_unit=None,
            )
        )
    program = Program.create(
        formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
        question_type=plan.question_type,
        formula_id=plan.formula_id,
        inputs=inputs,
        steps=[
            ProgramStep(
                step_id="step_output",
                operation=ProgramOperation.IDENTITY,
                input_refs=[inputs[0].input_id],
                formula_id=None,
            )
        ],
        output_ref="step_output",
        output_kind=ProgramOutputKind.SCALAR,
    )
    return program, resolutions, BindingMap(sorted(bindings, key=lambda item: item.placeholder))


def completeness_for(
    plan: Plan,
    evidence_items: Sequence[EvidenceItem],
    *,
    missing_ids: Iterable[str] = (),
) -> EvidenceCompletenessResult:
    missing = set(missing_ids)
    results = []
    missing_ordered = []
    projected = {
        requirement.requirement_id: requirement
        for requirement in generate_plan_evidence_requirements(plan)
    }
    for requirement in plan.retrieval_requirements:
        ids = [
            item.evidence_id
            for item in evidence_items
            if item.source_type is requirement.source_type
            and item.metric == requirement.metric
            and item.period == requirement.period
        ]
        satisfied = requirement.requirement_id not in missing and bool(ids)
        if not satisfied:
            missing_ordered.append(requirement.requirement_id)
        results.append(
            EvidenceRequirementResult(
                projected[requirement.requirement_id],
                satisfied,
                ids if satisfied else [],
                None if satisfied else "REQUIRED_EVIDENCE_MISSING",
            )
        )
    return EvidenceCompletenessResult(not missing_ordered, results, missing_ordered)


def make_request(
    sources: Sequence[EvidenceSource],
    *,
    execution_result: ExecutionResult | None = None,
) -> VerificationRequest:
    plan = make_plan(sources)
    evidence_items: list[EvidenceItem] = []
    cell_locations: list[CellLocation] = []
    schema_links: list[SchemaLinkResult] = []
    if EvidenceSource.TABLE in sources:
        item, location = table_pair()
        evidence_items.append(item)
        cell_locations.append(location)
        schema_links.extend(link_schema(plan, [item], {item.evidence_id: location}))
    if EvidenceSource.TEXT in sources:
        evidence_items.append(text_item(linked=EvidenceSource.TABLE in sources))
    program, resolutions, binding_map = execution_contracts(plan, evidence_items)
    return VerificationRequest(
        plan=plan,
        program=program,
        evidence_items=evidence_items,
        cell_locations=cell_locations,
        schema_links=schema_links,
        scale_unit_resolutions=resolutions,
        binding_map=binding_map,
        evidence_completeness=completeness_for(plan, evidence_items),
        execution_result=execution_result or success_execution(program),
        verify_profile=plan.verify_profile,
    )


def make_program_request(programmer_input: ProgrammerInput) -> VerificationRequest:
    """Build a fully grounded request for deterministic verifier tests."""
    generated = generate_program(programmer_input)
    assert generated.program is not None
    program = generated.program
    requirements = {
        item.requirement_id: item
        for item in programmer_input.plan.retrieval_requirements
    }
    evidence_items = []
    cell_locations = []
    resolutions = []
    bindings = []
    values_by_placeholder = {}
    for index, program_input in enumerate(program.inputs):
        requirement = requirements[program_input.requirement_id]
        value = CanonicalDecimal(str((index + 1) * 10))
        item, location = grounded_cell(
            f"program-{index}",
            column_labels=(requirement.period or "value",),
            raw_value=value,
            decimal_value=value,
        )
        report_year = (
            int(requirement.period[:4])
            if requirement.period is not None
            and len(requirement.period) >= 4
            and requirement.period[:4].isdigit()
            else None
        )
        evidence_items.append(
            replace(
                item,
                evidence_id=program_input.evidence_id,
                metric=requirement.metric,
                period=requirement.period,
                normalized_value=value,
                ticker="AAA",
                company_name="Test Company",
                report_year=report_year,
            )
        )
        cell_locations.append(
            replace(
                location,
                matched_metric=requirement.metric,
                matched_period=requirement.period,
                ticker="AAA",
                company_name="Test Company",
                report_year=report_year,
                statement_scope=StatementScope.HOP_NHAT,
                table_class=requirement.table_class,
            )
        )
        resolutions.append(
            ScaleUnitResolution(
                evidence_id=program_input.evidence_id,
                status=ScaleUnitResolutionStatus.RESOLVED,
                source_scale=Scale.MILLION,
                source_unit=None,
                requested_output_scale=None,
                requested_output_unit=None,
                winning_hint_ids=[f"hint-{program_input.evidence_id}"],
                considered_hint_ids=[f"hint-{program_input.evidence_id}"],
            )
        )
        binding = ValueBinding(
            placeholder=program_input.placeholder,
            evidence_id=program_input.evidence_id,
            value=value,
            source_scale=Scale.MILLION,
            source_unit=None,
            requested_output_scale=None,
            requested_output_unit=None,
        )
        bindings.append(binding)
        values_by_placeholder[binding.placeholder] = value

    step = next(item for item in program.steps if item.step_id == program.output_ref)
    inputs_by_id = {item.input_id: item for item in program.inputs}
    if program.output_kind is ProgramOutputKind.ORDERED_VALUES:
        output_values = [
            ExecutionDatum(
                values_by_placeholder[inputs_by_id[ref].placeholder],
                Scale.MILLION,
                None,
            )
            for ref in step.input_refs
        ]
    elif program.formula_id == "GROWTH_RATE":
        output_values = [ExecutionDatum(CanonicalDecimal("100"), Scale.PERCENT, None)]
    elif program.formula_id == "AVERAGE":
        output_values = [ExecutionDatum(CanonicalDecimal("20"), Scale.RAW, None)]
    else:
        first = inputs_by_id[step.input_refs[0]]
        output_values = [
            ExecutionDatum(
                values_by_placeholder[first.placeholder], Scale.MILLION, None
            )
        ]
    execution_result = ExecutionResult(
        schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
        program_id=program.program_id,
        success=True,
        output=ExecutionOutput(program.output_kind, output_values),
        failure=None,
        execution_ms=1,
    )
    locations_by_evidence = {
        item.evidence_id: location
        for item, location in zip(evidence_items, cell_locations)
    }
    return VerificationRequest(
        plan=programmer_input.plan,
        program=program,
        evidence_items=evidence_items,
        cell_locations=cell_locations,
        schema_links=link_schema(
            programmer_input.plan,
            evidence_items,
            locations_by_evidence,
        ),
        scale_unit_resolutions=resolutions,
        binding_map=BindingMap(sorted(bindings, key=lambda item: item.placeholder)),
        evidence_completeness=completeness_for(
            programmer_input.plan, evidence_items
        ),
        execution_result=execution_result,
        verify_profile=programmer_input.plan.verify_profile,
    )
