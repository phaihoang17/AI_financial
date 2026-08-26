"""Deterministic orchestration stage integration.

The functions in this module own boundary validation and deterministic stage
composition. LangGraph wiring lives in :mod:`src.orchestration.graph` and
contains no financial or retrieval policy logic.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence

from src.evidence.numeric_masking import mask_numeric_evidence
from src.evidence.scale_unit_resolver import resolve_scale_units
from src.evidence.schema_linker import link_schema
from src.evidence.value_binder import build_binding_map
from src.orchestration.ports import (
    BindingStorePort,
    CellLocatorStagePort,
    EvidenceBuilderStagePort,
    NLUStagePort,
    ProgrammerStagePort,
    RetrievalArtifacts,
    RetrievalStagePort,
    RetrievalStageRequest,
    SandboxStagePort,
)
from src.orchestration.schemas import (
    M9Phase,
    M9_STATE_SCHEMA_VERSION,
    make_plan_fingerprint,
    make_retrieval_policy_fingerprint,
)
from src.orchestration.state import AttemptEntryStage, AttemptRecord, M9State
from src.programmer.schemas import ProgrammerInput, ProgrammerResult, ProgrammerStatus
from src.programmer.validator import ProgramValidationError, validate_program
from src.retrieval.evidence import (
    CellLocation,
    check_evidence_completeness,
    generate_plan_evidence_requirements,
)
from src.retrieval.query_builder import build_retrieval_query
from src.retrieval.schemas import RetrievalContractError
from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    SandboxExecutionRequest,
)
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from src.supervisor.planner import supervise_batch1
from src.supervisor.schemas import EvidenceSource, Plan
from src.understanding.schemas import SchemaValidationError
from src.verification.schemas import RetryState, VerificationRequest
from src.verification.verifier import verify


class OrchestrationFailureCode(str, Enum):
    NLU_STAGE_FAILED = "NLU_STAGE_FAILED"
    RETRIEVAL_STAGE_FAILED = "RETRIEVAL_STAGE_FAILED"
    RETRIEVAL_QUERY_MISMATCH = "RETRIEVAL_QUERY_MISMATCH"
    UNSUPPORTED_RETRIEVAL_SHAPE = "UNSUPPORTED_RETRIEVAL_SHAPE"
    CELL_LOCATION_FAILED = "CELL_LOCATION_FAILED"
    EVIDENCE_BUILD_FAILED = "EVIDENCE_BUILD_FAILED"
    EVIDENCE_LOCATION_MISSING = "EVIDENCE_LOCATION_MISSING"
    EVIDENCE_LOCATION_AMBIGUOUS = "EVIDENCE_LOCATION_AMBIGUOUS"
    TABLE_CLASS_UNAVAILABLE = "TABLE_CLASS_UNAVAILABLE"
    TABLE_CLASS_MISMATCH = "TABLE_CLASS_MISMATCH"
    EVIDENCE_PIPELINE_FAILED = "EVIDENCE_PIPELINE_FAILED"
    BINDING_STORE_FAILED = "BINDING_STORE_FAILED"
    BINDING_REFERENCE_UNAVAILABLE = "BINDING_REFERENCE_UNAVAILABLE"
    PROGRAMMER_STAGE_FAILED = "PROGRAMMER_STAGE_FAILED"
    SANDBOX_STAGE_FAILED = "SANDBOX_STAGE_FAILED"
    VERIFICATION_STAGE_FAILED = "VERIFICATION_STAGE_FAILED"
    VERIFICATION_BYPASSED = "VERIFICATION_BYPASSED"


class OrchestrationFailure(SchemaValidationError):
    """Typed M9 failure for orchestration boundaries without a native result."""

    def __init__(self, stage: M9Phase, code: str | Enum, message: str) -> None:
        self.stage = stage
        self.code = code.value if isinstance(code, Enum) else code
        self.message = message
        super().__init__(f"{stage.value}:{self.code}: {message}")


@dataclass(frozen=True)
class StageTransition:
    state: M9State
    stop: bool = False
    failure_kind: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    failure_payload: Optional[dict[str, Any]] = None

    @classmethod
    def stopped(
        cls,
        state: M9State,
        *,
        kind: str,
        code: str,
        message: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> "StageTransition":
        return cls(state, True, kind, code, message, payload)


@dataclass(frozen=True)
class OrchestrationDependencies:
    nlu: NLUStagePort
    retrieval: RetrievalStagePort
    cell_locator: CellLocatorStagePort
    evidence_builder: EvidenceBuilderStagePort
    programmer: ProgrammerStagePort
    sandbox: SandboxStagePort
    binding_store: BindingStorePort
    top_k: int
    artifact_versions: Mapping[str, str]

    def __post_init__(self) -> None:
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or self.top_k < 1:
            raise SchemaValidationError("top_k must be a positive integer")
        if not isinstance(self.artifact_versions, Mapping) or not self.artifact_versions:
            raise SchemaValidationError("artifact_versions must be a non-empty mapping")
        versions = {}
        for key, value in self.artifact_versions.items():
            if not isinstance(key, str) or not key.strip():
                raise SchemaValidationError("artifact_versions keys must be non-empty")
            if not isinstance(value, str) or not value.strip():
                raise SchemaValidationError("artifact_versions values must be non-empty")
            versions[key] = value
        object.__setattr__(
            self,
            "artifact_versions",
            MappingProxyType(dict(sorted(versions.items()))),
        )


def initial_state(*, request_id: str, raw_question: str) -> M9State:
    """Create the non-terminal checkpoint state for one orchestration run."""
    return M9State(
        schema_version=M9_STATE_SCHEMA_VERSION,
        request_id=request_id,
        raw_question=raw_question,
        phase=M9Phase.INPUT,
        outcome=None,
        query_understanding=None,
        planning_gate=None,
        supervisor_result=None,
        plan_fingerprint=None,
        retrieval_policy_fingerprint=None,
        retry_state=None,
        attempts=(),
        final_response=None,
        failure_attribution=None,
    )


def _exception_transition(
    state: M9State,
    stage: M9Phase,
    code: OrchestrationFailureCode,
    error: Exception,
) -> StageTransition:
    failure = OrchestrationFailure(stage, code, str(error))
    return StageTransition.stopped(
        replace(state, phase=stage),
        kind="ORCHESTRATION",
        code=failure.code,
        message=failure.message,
        payload={"stage": stage.value},
    )


def run_nlu(state: M9State, deps: OrchestrationDependencies) -> StageTransition:
    try:
        output = deps.nlu.understand(state.raw_question)
        next_state = replace(
            state,
            phase=M9Phase.NLU,
            query_understanding=output.query_understanding,
            planning_gate=output.planning_gate,
        )
    except Exception as error:  # injected boundary: preserve as typed M9 failure
        return _exception_transition(
            state, M9Phase.NLU, OrchestrationFailureCode.NLU_STAGE_FAILED, error
        )
    if not output.planning_gate.allowed:
        return StageTransition.stopped(
            next_state,
            kind="PLANNING_GATE",
            code="PLANNING_GATE_BLOCKED",
            message="PlanningGate blocked the request before Supervisor/retrieval.",
            payload=output.planning_gate.to_dict(),
        )
    return StageTransition(next_state)


def _validate_supported_retrieval_shape(plan: Plan) -> None:
    """Accept only the exact M3 v1 Cartesian TABLE shape plus one TEXT request."""
    table_requirements = [
        item for item in plan.retrieval_requirements
        if item.source_type is EvidenceSource.TABLE
    ]
    actual_pairs = [(item.metric, item.period) for item in table_requirements]
    expected_pairs = [
        (metric, period)
        for metric in plan.target_metrics
        for period in plan.periods
    ] if table_requirements else []
    text_requirements = [
        item for item in plan.retrieval_requirements
        if item.source_type is EvidenceSource.TEXT
    ]
    text_shape_supported = len(text_requirements) <= 1 and all(
        item.table_class is None and item.metric is None and item.period is None
        for item in text_requirements
    )
    if actual_pairs != expected_pairs or not text_shape_supported:
        raise OrchestrationFailure(
            M9Phase.RETRIEVAL,
            OrchestrationFailureCode.UNSUPPORTED_RETRIEVAL_SHAPE,
            "Plan requirements cannot be represented by the supported M3 v1 shapes",
        )


def run_supervisor(state: M9State, deps: OrchestrationDependencies) -> StageTransition:
    assert state.query_understanding is not None
    assert state.planning_gate is not None
    result = supervise_batch1(state.query_understanding, state.planning_gate)
    next_state = replace(
        state,
        phase=M9Phase.SUPERVISOR,
        supervisor_result=result,
        plan_fingerprint=(
            None if result.plan is None else make_plan_fingerprint(result.plan)
        ),
    )
    if result.plan is None:
        return StageTransition.stopped(
            next_state,
            kind="SUPERVISOR_RESULT",
            code=(
                "SUPERVISOR_ABSTAIN"
                if result.abstain_reason is None
                else result.abstain_reason.value
            ),
            message="Supervisor produced no executable Plan.",
            payload=result.to_dict(),
        )

    plan = result.plan
    query = build_retrieval_query(plan, state.query_understanding)
    policy_fingerprint = make_retrieval_policy_fingerprint(
        plan,
        query,
        top_k=deps.top_k,
        artifact_versions=deps.artifact_versions,
    )
    request = RetrievalStageRequest(
        plan=plan,
        query=query,
        top_k=deps.top_k,
        artifact_versions=deps.artifact_versions,
        plan_fingerprint=next_state.plan_fingerprint,
        retrieval_policy_fingerprint=policy_fingerprint,
    )
    request.assert_immutable()
    attempt = AttemptRecord(
        attempt_index=0,
        entry_stage=AttemptEntryStage.RETRIEVAL,
        model_tier=plan.model_tier,
        retrieval_query=query,
    )
    next_state = replace(
        next_state,
        phase=M9Phase.RETRIEVAL,
        retrieval_policy_fingerprint=policy_fingerprint,
        retry_state=RetryState.initial(plan),
        attempts=(attempt,),
    )
    try:
        _validate_supported_retrieval_shape(plan)
    except OrchestrationFailure as failure:
        return StageTransition.stopped(
            next_state,
            kind="ORCHESTRATION",
            code=failure.code,
            message=failure.message,
            payload={"stage": failure.stage.value},
        )
    return StageTransition(next_state)


def _replace_attempt(state: M9State, phase: M9Phase, **updates: Any) -> M9State:
    if not state.attempts:
        raise SchemaValidationError("attempt update requires the initial attempt")
    current = state.attempts[-1]
    updated = replace(current, **updates)
    return replace(state, phase=phase, attempts=(*state.attempts[:-1], updated))


def _retrieval_request(
    state: M9State, deps: OrchestrationDependencies
) -> RetrievalStageRequest:
    plan = state.supervisor_result.plan
    query = state.attempts[-1].retrieval_query
    assert plan is not None and query is not None
    return RetrievalStageRequest(
        plan=plan,
        query=query,
        top_k=deps.top_k,
        artifact_versions=deps.artifact_versions,
        plan_fingerprint=state.plan_fingerprint,
        retrieval_policy_fingerprint=state.retrieval_policy_fingerprint,
    )


def run_retrieval(
    state: M9State, deps: OrchestrationDependencies
) -> StageTransition:
    try:
        request = _retrieval_request(state, deps)
        artifacts = deps.retrieval.retrieve(request)
        request.assert_immutable()
        if not isinstance(artifacts, RetrievalArtifacts):
            raise RetrievalContractError(
                "INVALID_RETRIEVAL_ARTIFACTS",
                "RetrievalStagePort must return RetrievalArtifacts",
            )
        if artifacts.query.to_dict() != request.query.to_dict():
            raise RetrievalContractError(
                OrchestrationFailureCode.RETRIEVAL_QUERY_MISMATCH.value,
                "RetrievalArtifacts.query must equal the frozen request query",
            )
        next_state = _replace_attempt(
            state,
            M9Phase.EVIDENCE,
            retrieval_candidates=artifacts.candidates,
            scale_hints_by_candidate=artifacts.scale_hints_by_candidate,
        )
        return StageTransition(next_state)
    except RetrievalContractError as error:
        return StageTransition.stopped(
            replace(state, phase=M9Phase.RETRIEVAL),
            kind="RETRIEVAL_ERROR",
            code=error.code,
            message=str(error),
        )
    except Exception as error:
        return _exception_transition(
            state,
            M9Phase.RETRIEVAL,
            OrchestrationFailureCode.RETRIEVAL_STAGE_FAILED,
            error,
        )


def _locations_by_evidence(
    evidence_items: Sequence[Any], locations: Sequence[CellLocation]
) -> dict[str, CellLocation]:
    result: dict[str, CellLocation] = {}
    for item in evidence_items:
        if item.source_type is not EvidenceSource.TABLE:
            continue
        matches = [
            location for location in locations
            if item.report_ref == location.report_id
            and item.page_ref == location.page_id
            and item.table_ref == location.table_id
            and item.row_path == [entry.label for entry in location.row_path]
            and item.column_path == [entry.label for entry in location.column_path]
        ]
        if not matches:
            raise OrchestrationFailure(
                M9Phase.EVIDENCE,
                OrchestrationFailureCode.EVIDENCE_LOCATION_MISSING,
                f"TABLE evidence {item.evidence_id} has no exact CellLocation",
            )
        if len(matches) != 1:
            raise OrchestrationFailure(
                M9Phase.EVIDENCE,
                OrchestrationFailureCode.EVIDENCE_LOCATION_AMBIGUOUS,
                f"TABLE evidence {item.evidence_id} has multiple CellLocations",
            )
        result[item.evidence_id] = matches[0]
    return result


def _validate_source_table_classes(
    plan: Plan,
    completeness: Any,
    locations_by_evidence: Mapping[str, CellLocation],
) -> None:
    requirements = {
        item.requirement_id: item for item in plan.retrieval_requirements
    }
    for result in completeness.requirements:
        requirement = requirements[result.requirement.requirement_id]
        if requirement.source_type is not EvidenceSource.TABLE:
            continue
        for evidence_id in result.evidence_ids:
            location = locations_by_evidence[evidence_id]
            if location.table_class is None:
                raise OrchestrationFailure(
                    M9Phase.EVIDENCE,
                    OrchestrationFailureCode.TABLE_CLASS_UNAVAILABLE,
                    f"source-backed table_class is unavailable for {evidence_id}",
                )
            if location.table_class is not requirement.table_class:
                raise OrchestrationFailure(
                    M9Phase.EVIDENCE,
                    OrchestrationFailureCode.TABLE_CLASS_MISMATCH,
                    f"source-backed table_class does not match {requirement.requirement_id}",
                )


def _orchestration_stop(state: M9State, failure: OrchestrationFailure) -> StageTransition:
    return StageTransition.stopped(
        replace(state, phase=failure.stage),
        kind="ORCHESTRATION",
        code=failure.code,
        message=failure.message,
        payload={"stage": failure.stage.value},
    )


def run_evidence(
    state: M9State, deps: OrchestrationDependencies
) -> StageTransition:
    plan = state.supervisor_result.plan
    understanding = state.query_understanding
    attempt = state.attempts[-1]
    assert plan is not None and understanding is not None
    assert attempt.retrieval_query is not None
    current_state = state
    try:
        locations = tuple(
            deps.cell_locator.locate(
                attempt.retrieval_query, attempt.retrieval_candidates
            )
        )
        current_state = _replace_attempt(
            current_state, M9Phase.EVIDENCE, cell_locations=locations
        )
    except Exception as error:
        return _exception_transition(
            current_state,
            M9Phase.EVIDENCE,
            OrchestrationFailureCode.CELL_LOCATION_FAILED,
            error,
        )
    try:
        evidence_items = tuple(
            deps.evidence_builder.build(locations, attempt.retrieval_candidates)
        )
        current_state = _replace_attempt(
            current_state, M9Phase.EVIDENCE, evidence_items=evidence_items
        )
    except Exception as error:
        return _exception_transition(
            current_state,
            M9Phase.EVIDENCE,
            OrchestrationFailureCode.EVIDENCE_BUILD_FAILED,
            error,
        )

    try:
        locations_by_evidence = _locations_by_evidence(evidence_items, locations)
        completeness = check_evidence_completeness(
            generate_plan_evidence_requirements(plan),
            evidence_items,
            locations_by_evidence,
        )
        current_state = _replace_attempt(
            current_state,
            M9Phase.EVIDENCE,
            evidence_completeness=completeness,
        )
        if not completeness.complete:
            return StageTransition.stopped(
                current_state,
                kind="EVIDENCE_COMPLETENESS",
                code="EVIDENCE_INCOMPLETE",
                message="Required evidence is incomplete.",
                payload=completeness.to_dict(),
            )
        _validate_source_table_classes(plan, completeness, locations_by_evidence)
        resolutions = tuple(
            resolve_scale_units(
                understanding,
                evidence_items,
                locations_by_evidence,
                current_state.attempts[-1].scale_hints_by_candidate,
            )
        )
        current_state = _replace_attempt(
            current_state,
            M9Phase.EVIDENCE,
            scale_unit_resolutions=resolutions,
        )
        schema_links = tuple(link_schema(plan, evidence_items, locations_by_evidence))
        current_state = _replace_attempt(
            current_state, M9Phase.EVIDENCE, schema_links=schema_links
        )
        masked = mask_numeric_evidence(
            plan,
            evidence_items,
            locations_by_evidence,
            schema_links,
            resolutions,
        )
        current_state = _replace_attempt(
            current_state, M9Phase.EVIDENCE, masked_evidence=masked
        )
        binding_map = build_binding_map(
            masked, evidence_items, locations_by_evidence, resolutions
        )
        binding_ref = deps.binding_store.put(binding_map)
        if not isinstance(binding_ref, str) or not binding_ref.strip():
            raise OrchestrationFailure(
                M9Phase.EVIDENCE,
                OrchestrationFailureCode.BINDING_STORE_FAILED,
                "BindingStorePort returned an invalid reference",
            )
        programmer_input = ProgrammerInput(
            plan=plan,
            masked_evidence=masked,
            formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
        )
        current_state = _replace_attempt(
            current_state,
            M9Phase.PROGRAMMER,
            binding_ref=binding_ref,
            programmer_input=programmer_input,
        )
        return StageTransition(current_state)
    except OrchestrationFailure as failure:
        return _orchestration_stop(current_state, failure)
    except Exception as error:
        return _exception_transition(
            current_state,
            M9Phase.EVIDENCE,
            OrchestrationFailureCode.EVIDENCE_PIPELINE_FAILED,
            error,
        )


def run_programmer(
    state: M9State, deps: OrchestrationDependencies
) -> StageTransition:
    attempt = state.attempts[-1]
    assert attempt.programmer_input is not None
    try:
        result = deps.programmer.generate(attempt.programmer_input, attempt.model_tier)
        if not isinstance(result, ProgrammerResult):
            raise TypeError("ProgrammerStagePort must return ProgrammerResult")
        if result.status is ProgrammerStatus.GENERATED:
            assert result.program is not None
            try:
                validate_program(result.program, attempt.programmer_input)
            except ProgramValidationError as error:
                result = ProgrammerResult(
                    status=ProgrammerStatus.REJECTED,
                    program=None,
                    failure_code=error.code.value,
                    failure_message=str(error),
                )
        next_state = _replace_attempt(
            state, M9Phase.PROGRAMMER, programmer_result=result
        )
    except Exception as error:
        return _exception_transition(
            state,
            M9Phase.PROGRAMMER,
            OrchestrationFailureCode.PROGRAMMER_STAGE_FAILED,
            error,
        )
    if result.status is ProgrammerStatus.REJECTED:
        return StageTransition.stopped(
            next_state,
            kind="PROGRAMMER_RESULT",
            code=result.failure_code or "PROGRAMMER_REJECTED",
            message=result.failure_message or "Programmer rejected the request.",
            payload=result.to_dict(),
        )
    return StageTransition(replace(next_state, phase=M9Phase.SANDBOX))


def _resolve_binding(state: M9State, deps: OrchestrationDependencies):
    binding_ref = state.attempts[-1].binding_ref
    if binding_ref is None or not deps.binding_store.contains(binding_ref):
        raise OrchestrationFailure(
            M9Phase.EVIDENCE,
            OrchestrationFailureCode.BINDING_REFERENCE_UNAVAILABLE,
            "process-local binding reference is unavailable; re-enter EVIDENCE",
        )
    return deps.binding_store.resolve(binding_ref)


def run_sandbox(
    state: M9State, deps: OrchestrationDependencies
) -> StageTransition:
    attempt = state.attempts[-1]
    assert attempt.programmer_input is not None
    assert attempt.programmer_result is not None
    assert attempt.programmer_result.program is not None
    try:
        binding_map = _resolve_binding(state, deps)
        request = SandboxExecutionRequest(
            schema_version=EXECUTION_REQUEST_SCHEMA_VERSION,
            program=attempt.programmer_result.program,
            programmer_input=attempt.programmer_input,
            binding_map=binding_map,
            limits_profile_id=EXECUTION_LIMITS_PROFILE_ID,
        )
        result = deps.sandbox.execute(request)
        next_state = _replace_attempt(
            state, M9Phase.SANDBOX, execution_result=result
        )
    except OrchestrationFailure as failure:
        return _orchestration_stop(state, failure)
    except Exception as error:
        return _exception_transition(
            state,
            M9Phase.SANDBOX,
            OrchestrationFailureCode.SANDBOX_STAGE_FAILED,
            error,
        )
    if not result.success:
        assert result.failure is not None
        return StageTransition.stopped(
            next_state,
            kind="EXECUTION_RESULT",
            code=result.failure.code,
            message=result.failure.message,
            payload=result.to_dict(),
        )
    return StageTransition(replace(next_state, phase=M9Phase.VERIFICATION))


def run_verification(
    state: M9State, deps: OrchestrationDependencies
) -> StageTransition:
    attempt = state.attempts[-1]
    plan = state.supervisor_result.plan
    assert plan is not None
    assert attempt.programmer_result is not None
    assert attempt.programmer_result.program is not None
    assert attempt.execution_result is not None
    assert attempt.evidence_completeness is not None
    try:
        binding_map = _resolve_binding(state, deps)
        request = VerificationRequest(
            plan=plan,
            program=attempt.programmer_result.program,
            evidence_items=list(attempt.evidence_items),
            cell_locations=list(attempt.cell_locations),
            schema_links=list(attempt.schema_links),
            scale_unit_resolutions=list(attempt.scale_unit_resolutions),
            binding_map=binding_map,
            evidence_completeness=attempt.evidence_completeness,
            execution_result=attempt.execution_result,
            verify_profile=plan.verify_profile,
        )
        report = verify(request)
        if report is None:
            raise OrchestrationFailure(
                M9Phase.VERIFICATION,
                OrchestrationFailureCode.VERIFICATION_BYPASSED,
                "successful execution unexpectedly bypassed verification",
            )
        next_state = _replace_attempt(
            state, M9Phase.VERIFICATION, verification_report=report
        )
    except OrchestrationFailure as failure:
        return _orchestration_stop(state, failure)
    except Exception as error:
        return _exception_transition(
            state,
            M9Phase.VERIFICATION,
            OrchestrationFailureCode.VERIFICATION_STAGE_FAILED,
            error,
        )
    if not report.passed:
        return StageTransition.stopped(
            next_state,
            kind="VERIFICATION_REPORT",
            code=report.failure_reason or "VERIFICATION_FAILED",
            message="VerificationReport failed.",
            payload=report.to_dict(),
        )
    return StageTransition(next_state, stop=True)
