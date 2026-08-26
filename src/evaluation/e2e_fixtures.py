"""CPU-only injected fixtures for the M9 retrieved-evidence E2E evaluation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Sequence

from src.evaluation.e2e import (
    ExecutionExpectation,
    RetrievedEvidenceE2ECase,
    VerificationExpectation,
)
from src.evaluation.harness import EvaluationFailureStage
from src.evidence.numeric_masking import mask_numeric_evidence
from src.evidence.scale_unit_resolver import resolve_scale_units
from src.evidence.schema_linker import link_schema
from src.evidence.schemas import CanonicalDecimal, EvidenceItem, Scale
from src.evidence.value_binder import build_binding_map
from src.indexing.schemas import (
    HeaderPathEntry,
    NumericParseResult,
    NumericParseStatus,
    ScaleHintSource,
    ScaleHintStatus,
    SourceSpan,
)
from src.orchestration.answer_builder import build_final_answer
from src.orchestration.nodes import OrchestrationDependencies
from src.orchestration.execution_routing import build_execution_directive
from src.orchestration.graph import OrchestrationGraph
from src.orchestration.ports import NLUStageOutput, RetrievalArtifacts
from src.orchestration.schemas import FailureAttribution, FinalResponseStatus
from src.programmer.generator import generate_program
from src.programmer.schemas import ProgramOutputKind
from src.retrieval.evidence import (
    CellLocation,
    MatchBasis,
    ProvenanceRole,
    check_evidence_completeness,
    generate_plan_evidence_requirements,
)
from src.retrieval.schemas import (
    HintAssociation,
    RetrievedScaleUnitHint,
    make_candidate_id,
)
from src.sandbox.interpreter import interpret_execution_request
from src.sandbox.limits import ResourceLimitFailureCode
from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    EXECUTION_RESULT_SCHEMA_VERSION,
    ExecutionDatum,
    ExecutionFailure,
    ExecutionFailureStage,
    ExecutionOutput,
    ExecutionResult,
    SandboxExecutionRequest,
)
from src.sandbox.security import SecurityFailureCode
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from src.supervisor.planner import supervise_batch1
from src.supervisor.schemas import EvidenceSource, ModelTier, TableClass
from src.understanding.planning_gate import FindingName, PlanningGate
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
from src.verification.retry import build_retry_directive
from src.verification.schemas import (
    RetryState,
    VerificationRequest,
)
from src.verification.verifier import verify


ARTIFACT_VERSIONS = {
    "bm25": "fixture-bm25-v1",
    "embedding": "fixture-bge-m3-v1",
    "reranker": "fixture-reranker-v1",
}


def fixture_understanding(
    case_id: str,
    periods: Sequence[str] = ("2015",),
    operation: Operation = Operation.NONE,
) -> QueryUnderstanding:
    return QueryUnderstanding(
        raw_question=f"fixture:{case_id}",
        company=CompanyUnderstanding("AAA", "Test Company", "AAA", 1.0),
        periods=[
            PeriodUnderstanding(period, PeriodKind.NAM, period)
            for period in periods
        ],
        statement_scope=StatementScopeUnderstanding(
            StatementScope.HOP_NHAT, False, 1.0
        ),
        metrics=[MetricUnderstanding("LNST", "LNST", 1.0)],
        operation=operation,
        requested_scale=None,
        requested_unit=None,
        missing_information=[],
        ambiguities=[],
        confidence=1.0,
    )


def fixture_pairs(
    periods: Sequence[str], values: Sequence[str], *, table_class=True
) -> tuple[tuple[EvidenceItem, CellLocation], ...]:
    pairs = []
    for period, value in zip(periods, values):
        name = f"{period}-{value.replace('.', '_')}"
        candidate_id = make_candidate_id(
            EvidenceSource.TABLE, f"representation-{name}", f"chunk-{name}"
        )
        row_path = [
            HeaderPathEntry(f"{name}-row-0", "Báo cáo kết quả"),
            HeaderPathEntry(f"{name}-row-1", "LNST"),
        ]
        column_path = [HeaderPathEntry(f"{name}-column-0", period)]
        numeric = NumericParseResult(
            NumericParseStatus.PARSED, value, value, value, False
        )
        item = EvidenceItem(
            evidence_id=f"evidence-{name}",
            source_type=EvidenceSource.TABLE,
            report_ref="report",
            page_ref="page",
            table_ref="table",
            associated_table_ref=None,
            statement_scope=StatementScope.HOP_NHAT,
            period=period,
            metric="LNST",
            row_label="LNST",
            column_label=period,
            row_path=[entry.label for entry in row_path],
            column_path=[entry.label for entry in column_path],
            text_span=None,
            raw_value=value,
            normalized_value=CanonicalDecimal(value),
            unit=None,
            scale=None,
            scale_source=None,
            retrieval_score=0.5,
            rerank_score=0.6,
            ticker="AAA",
            company_name="Test Company",
            report_year=int(period),
        )
        location = CellLocation(
            location_id=f"location-{name}",
            candidate_id=candidate_id,
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
            normalized_text=value,
            numeric=numeric,
            provenance_role=ProvenanceRole.PRIMARY,
            matched_metric="LNST",
            matched_period=period,
            match_basis=MatchBasis.EXACT_ROW_PATH,
            ticker="AAA",
            company_name="Test Company",
            report_year=int(period),
            statement_scope=StatementScope.HOP_NHAT,
            table_class=(TableClass.INCOME_STATEMENT if table_class else None),
        )
        pairs.append((item, location))
    return tuple(pairs)


def _scale_hint(location: CellLocation, index: int) -> RetrievedScaleUnitHint:
    return RetrievedScaleUnitHint(
        hint_id=f"hint-{location.location_id}-{index}",
        candidate_id=location.candidate_id,
        source_kind=ScaleHintSource.HEADER,
        source_ref=f"source-{location.location_id}",
        source_span=SourceSpan(index, index + 1),
        raw_hint_text="triệu đồng",
        scale_candidate=Scale.MILLION,
        unit_candidate=None,
        status=ScaleHintStatus.EXTRACTED,
        association=HintAssociation.DIRECT,
    )


class _FixtureNLU:
    def __init__(self, output: NLUStageOutput):
        self.output = output

    def understand(self, raw_question: str) -> NLUStageOutput:
        if raw_question != self.output.query_understanding.raw_question:
            raise ValueError("fixture raw question mismatch")
        return self.output


class _FixtureEvidenceAdapter:
    def __init__(self, pair_scripts):
        self.pair_scripts = tuple(tuple(items) for items in pair_scripts)
        self.current = 0
        self.retrieval_calls = 0

    @property
    def pairs(self):
        return self.pair_scripts[self.current]

    def retrieve(self, request):
        self.current = min(self.retrieval_calls, len(self.pair_scripts) - 1)
        self.retrieval_calls += 1
        return RetrievalArtifacts(
            query=request.query,
            candidates=(),
            scale_hints_by_candidate={
                location.candidate_id: (_scale_hint(location, index),)
                for index, (_, location) in enumerate(self.pairs)
            },
        )

    def locate(self, query, candidates):
        return [location for _, location in self.pairs]

    def build(self, locations, candidates):
        return [item for item, _ in self.pairs]


class _FixtureProgrammer:
    def generate(self, programmer_input, model_tier):
        return generate_program(programmer_input)


class _FixtureSandbox:
    def __init__(self, outcomes):
        self.outcomes = tuple(outcomes)
        self.calls = 0

    def execute(self, request):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, tuple):
            stage, code = outcome
            return ExecutionResult(
                schema_version=EXECUTION_RESULT_SCHEMA_VERSION,
                program_id=request.program.program_id,
                success=False,
                output=None,
                failure=ExecutionFailure(stage, code, "fixture execution failure"),
                execution_ms=0,
            )
        result = replace(interpret_execution_request(request), execution_ms=0)
        if outcome == "wrong_kind":
            kind = (
                ProgramOutputKind.ORDERED_VALUES
                if result.output.kind is ProgramOutputKind.SCALAR
                else ProgramOutputKind.SCALAR
            )
            return replace(result, output=ExecutionOutput(kind, list(result.output.values)))
        if outcome == "wrong_scale":
            values = list(result.output.values)
            values[0] = ExecutionDatum(
                CanonicalDecimal(values[0].value), Scale.BILLION, values[0].unit
            )
            return replace(result, output=ExecutionOutput(result.output.kind, values))
        return result


class _MemoryBindingStore:
    def __init__(self):
        self.values = {}

    def put(self, binding_map):
        encoded = json.dumps(binding_map.to_dict(), sort_keys=True).encode()
        reference = f"binding://e2e/{sha256(encoded).hexdigest()}"
        self.values[reference] = binding_map
        return reference

    def contains(self, binding_ref):
        return binding_ref in self.values

    def resolve(self, binding_ref):
        return self.values[binding_ref]


def _graph_factory(understanding, gate, pair_scripts, outcomes):
    def create():
        adapter = _FixtureEvidenceAdapter(pair_scripts)
        return OrchestrationGraph(
            OrchestrationDependencies(
                nlu=_FixtureNLU(NLUStageOutput(understanding, gate)),
                retrieval=adapter,
                cell_locator=adapter,
                evidence_builder=adapter,
                programmer=_FixtureProgrammer(),
                sandbox=_FixtureSandbox(outcomes),
                binding_store=_MemoryBindingStore(),
                top_k=10,
                artifact_versions=ARTIFACT_VERSIONS,
            )
        )

    return create


@dataclass(frozen=True)
class _OracleArtifacts:
    plan: object
    completeness: object
    program: object
    execution: ExecutionResult
    verification: object
    answer: object


def _oracle_artifacts(understanding, pairs, *, output_variant="success"):
    gate = PlanningGate(True, [], [])
    plan = supervise_batch1(understanding, gate).plan
    assert plan is not None
    evidence = [item for item, _ in pairs]
    locations = [location for _, location in pairs]
    locations_by_evidence = {
        item.evidence_id: location for item, location in pairs
    }
    completeness = check_evidence_completeness(
        generate_plan_evidence_requirements(plan), evidence, locations_by_evidence
    )
    hints = {
        location.candidate_id: (_scale_hint(location, index),)
        for index, location in enumerate(locations)
    }
    resolutions = resolve_scale_units(
        understanding, evidence, locations_by_evidence, hints
    )
    links = link_schema(plan, evidence, locations_by_evidence)
    masked = mask_numeric_evidence(
        plan, evidence, locations_by_evidence, links, resolutions
    )
    bindings = build_binding_map(masked, evidence, locations_by_evidence, resolutions)
    from src.programmer.schemas import ProgrammerInput

    programmer_input = ProgrammerInput(plan, masked, FORMULA_REGISTRY_FINGERPRINT)
    programmer_result = generate_program(programmer_input)
    program = programmer_result.program
    assert program is not None
    request = SandboxExecutionRequest(
        EXECUTION_REQUEST_SCHEMA_VERSION,
        program,
        programmer_input,
        bindings,
        EXECUTION_LIMITS_PROFILE_ID,
    )
    execution = replace(interpret_execution_request(request), execution_ms=0)
    if output_variant == "wrong_kind":
        kind = (
            ProgramOutputKind.ORDERED_VALUES
            if execution.output.kind is ProgramOutputKind.SCALAR
            else ProgramOutputKind.SCALAR
        )
        execution = replace(
            execution, output=ExecutionOutput(kind, list(execution.output.values))
        )
    elif output_variant == "wrong_scale":
        values = list(execution.output.values)
        values[0] = ExecutionDatum(
            CanonicalDecimal(values[0].value), Scale.BILLION, values[0].unit
        )
        execution = replace(
            execution, output=ExecutionOutput(execution.output.kind, values)
        )
    report = verify(
        VerificationRequest(
            plan=plan,
            program=program,
            evidence_items=evidence,
            cell_locations=locations,
            schema_links=links,
            scale_unit_resolutions=resolutions,
            binding_map=bindings,
            evidence_completeness=completeness,
            execution_result=execution,
            verify_profile=plan.verify_profile,
        )
    )
    assert report is not None
    answer = (
        build_final_answer(plan, program, execution, report, evidence, links)
        if report.passed
        else None
    )
    return _OracleArtifacts(plan, completeness, program, execution, report, answer)


def _success_case(case_id, periods, values, operation, *, pair_scripts=None, outcomes=("success",), retries=0, escalated=False):
    understanding = fixture_understanding(case_id, periods, operation)
    good = fixture_pairs(periods, values)
    scripts = [good] if pair_scripts is None else pair_scripts
    oracle = _oracle_artifacts(understanding, good)
    return RetrievedEvidenceE2ECase(
        case_id=case_id,
        raw_question=understanding.raw_question,
        graph_factory=_graph_factory(
            understanding, PlanningGate(True, [], []), scripts, outcomes
        ),
        expected_nlu=understanding,
        expected_plan=oracle.plan,
        expected_evidence=oracle.completeness,
        expected_program=oracle.program,
        expected_execution=ExecutionExpectation(True, oracle.execution.output, None, None),
        expected_verification=VerificationExpectation(True, None, None),
        expected_status=FinalResponseStatus.PASS,
        expected_answer=oracle.answer,
        expected_retries_used=retries,
        expected_strong_escalated=escalated,
        expected_failure_attribution=None,
    )


def e2e_fixture_cases() -> list[RetrievedEvidenceE2ECase]:
    cases = [
        _success_case("lookup", ("2015",), ("12.5",), Operation.NONE),
        _success_case(
            "compare", ("2014", "2015"), ("10", "12.5"), Operation.COMPARE
        ),
        _success_case(
            "growth-rate", ("2014", "2015"), ("100", "125"), Operation.GROWTH
        ),
        _success_case(
            "average",
            ("2013", "2014", "2015"),
            ("10", "20", "30"),
            Operation.AGGREGATE,
        ),
    ]

    clarification = fixture_understanding("clarification")
    cases.append(
        RetrievedEvidenceE2ECase(
            case_id="clarification",
            raw_question=clarification.raw_question,
            graph_factory=_graph_factory(
                clarification,
                PlanningGate(False, [FindingName.COMPANY], []),
                [fixture_pairs(("2015",), ("12.5",))],
                ("success",),
            ),
            expected_nlu=clarification,
            expected_plan=None,
            expected_evidence=None,
            expected_program=None,
            expected_execution=None,
            expected_verification=None,
            expected_status=FinalResponseStatus.CLARIFICATION,
            expected_answer=None,
            expected_retries_used=None,
            expected_strong_escalated=False,
            expected_failure_attribution=FailureAttribution(
                EvaluationFailureStage.NLU,
                "MISSING_COMPANY",
                "Clarification required for existing NLU findings: MISSING_COMPANY",
                None,
            ),
        )
    )

    retrieval_retry = fixture_understanding("retrieval-retry", ("2015",))
    good = fixture_pairs(("2015",), ("12.5",))
    bad = tuple(
        (replace(item, report_year=2014), replace(location, report_year=2014))
        for item, location in good
    )
    cases.append(
        _success_case(
            "retrieval-retry",
            ("2015",),
            ("12.5",),
            Operation.NONE,
            pair_scripts=[bad, good],
            retries=1,
        )
    )
    cases.append(
        _success_case(
            "programmer-retry",
            ("2014", "2015"),
            ("100", "125"),
            Operation.GROWTH,
            outcomes=("wrong_kind", "success"),
            retries=1,
        )
    )
    cases.append(
        _success_case(
            "cheap-strong",
            ("2015",),
            ("12.5",),
            Operation.NONE,
            outcomes=("wrong_kind", "success"),
            retries=1,
            escalated=True,
        )
    )
    cases.append(
        _success_case(
            "sandbox-retry",
            ("2015",),
            ("12.5",),
            Operation.NONE,
            outcomes=(
                (
                    ExecutionFailureStage.RESOURCE,
                    ResourceLimitFailureCode.WALL_CLOCK_TIMEOUT.value,
                ),
                "success",
            ),
            retries=1,
        )
    )

    permanent = fixture_understanding("verification-abstain")
    permanent_pairs = fixture_pairs(("2015",), ("12.5",))
    permanent_oracle = _oracle_artifacts(
        permanent, permanent_pairs, output_variant="wrong_scale"
    )
    permanent_directive = build_retry_directive(
        permanent_oracle.plan,
        permanent_oracle.verification,
        RetryState.initial(permanent_oracle.plan),
    )
    cases.append(
        RetrievedEvidenceE2ECase(
            "verification-abstain",
            permanent.raw_question,
            _graph_factory(
                permanent,
                PlanningGate(True, [], []),
                [permanent_pairs],
                ("wrong_scale",),
            ),
            permanent,
            permanent_oracle.plan,
            permanent_oracle.completeness,
            permanent_oracle.program,
            ExecutionExpectation(True, permanent_oracle.execution.output, None, None),
            VerificationExpectation(
                False,
                permanent_oracle.verification.failure_category,
                permanent_oracle.verification.failure_reason,
            ),
            FinalResponseStatus.ABSTAIN,
            None,
            0,
            False,
            FailureAttribution(
                EvaluationFailureStage.VERIFICATION,
                permanent_oracle.verification.failure_reason,
                permanent_directive.reason,
                0,
                evidence_ids=(permanent_pairs[0][0].evidence_id,),
                program_ids=(permanent_oracle.program.program_id,),
            ),
        )
    )

    security = fixture_understanding("security-abstain")
    security_pairs = fixture_pairs(("2015",), ("12.5",))
    security_oracle = _oracle_artifacts(security, security_pairs)
    security_failure = ExecutionResult(
        EXECUTION_RESULT_SCHEMA_VERSION,
        security_oracle.program.program_id,
        False,
        None,
        ExecutionFailure(
            ExecutionFailureStage.SECURITY,
            SecurityFailureCode.NETWORK_ACCESS_DENIED.value,
            "fixture execution failure",
        ),
        0,
    )
    security_directive = build_execution_directive(
        security_oracle.plan, security_failure, RetryState.initial(security_oracle.plan)
    )
    program_input = security_oracle.program.inputs[0]
    cases.append(
        RetrievedEvidenceE2ECase(
            "security-abstain",
            security.raw_question,
            _graph_factory(
                security,
                PlanningGate(True, [], []),
                [security_pairs],
                ((ExecutionFailureStage.SECURITY, SecurityFailureCode.NETWORK_ACCESS_DENIED.value),),
            ),
            security,
            security_oracle.plan,
            security_oracle.completeness,
            security_oracle.program,
            ExecutionExpectation(
                False,
                None,
                ExecutionFailureStage.SECURITY,
                SecurityFailureCode.NETWORK_ACCESS_DENIED.value,
            ),
            None,
            FinalResponseStatus.ABSTAIN,
            None,
            0,
            False,
            FailureAttribution(
                EvaluationFailureStage.SANDBOX,
                SecurityFailureCode.NETWORK_ACCESS_DENIED.value,
                security_directive.reason,
                0,
                (program_input.requirement_id,),
                (program_input.evidence_id,),
                (security_oracle.program.program_id,),
            ),
        )
    )

    exhausted = fixture_understanding("retry-exhausted")
    exhausted_pairs = fixture_pairs(("2015",), ("12.5",))
    exhausted_oracle = _oracle_artifacts(
        exhausted, exhausted_pairs, output_variant="wrong_kind"
    )
    exhausted_state = RetryState(
        1, 1, ModelTier.STRONG, True, False
    )
    exhausted_directive = build_retry_directive(
        exhausted_oracle.plan, exhausted_oracle.verification, exhausted_state
    )
    cases.append(
        RetrievedEvidenceE2ECase(
            "retry-exhausted",
            exhausted.raw_question,
            _graph_factory(
                exhausted,
                PlanningGate(True, [], []),
                [exhausted_pairs],
                ("wrong_kind",),
            ),
            exhausted,
            exhausted_oracle.plan,
            exhausted_oracle.completeness,
            exhausted_oracle.program,
            ExecutionExpectation(True, exhausted_oracle.execution.output, None, None),
            VerificationExpectation(
                False,
                exhausted_oracle.verification.failure_category,
                exhausted_oracle.verification.failure_reason,
            ),
            FinalResponseStatus.ABSTAIN,
            None,
            1,
            True,
            FailureAttribution(
                EvaluationFailureStage.VERIFICATION,
                exhausted_oracle.verification.failure_reason,
                exhausted_directive.reason,
                1,
                program_ids=(exhausted_oracle.program.program_id,),
            ),
        )
    )
    return cases
