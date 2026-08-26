import inspect

import pytest

from src.orchestration.ports import (
    NLUStageOutput,
    ProgrammerStagePort,
    RetrievalArtifacts,
    RetrievalStageRequest,
    SandboxStagePort,
)
from src.orchestration.schemas import make_retrieval_policy_fingerprint
from src.retrieval.query_builder import build_retrieval_query
from src.understanding.schemas import SchemaValidationError
from tests.orchestration.helpers import ARTIFACT_VERSIONS, approved_contracts
from tests.evidence.m5_helpers import grounded_cell


def test_nlu_stage_output_round_trip():
    understanding, gate, *_ = approved_contracts()
    output = NLUStageOutput(understanding, gate)

    assert NLUStageOutput.from_dict(output.to_dict()).to_dict() == output.to_dict()


def test_retrieval_request_freezes_exact_policy():
    understanding, _, _, plan, query, plan_fingerprint, policy = (
        approved_contracts()
    )
    request = RetrievalStageRequest(
        plan=plan,
        query=query,
        top_k=10,
        artifact_versions=ARTIFACT_VERSIONS,
        plan_fingerprint=plan_fingerprint,
        retrieval_policy_fingerprint=policy,
    )
    assert dict(request.artifact_versions) == ARTIFACT_VERSIONS
    request.plan.periods.append("2016")
    with pytest.raises(SchemaValidationError, match="mutated"):
        request.assert_immutable()

    changed_query = build_retrieval_query(plan, understanding)
    changed_query.query_texts.append("hidden widening")
    changed_policy = make_retrieval_policy_fingerprint(
        plan,
        changed_query,
        top_k=10,
        artifact_versions=ARTIFACT_VERSIONS,
    )
    assert changed_policy != policy
    with pytest.raises(SchemaValidationError, match="immutable policy"):
        RetrievalStageRequest(
            plan=plan,
            query=changed_query,
            top_k=10,
            artifact_versions=ARTIFACT_VERSIONS,
            plan_fingerprint=plan_fingerprint,
            retrieval_policy_fingerprint=policy,
        )


def test_programmer_and_sandbox_ports_expose_only_approved_inputs():
    programmer = inspect.signature(ProgrammerStagePort.generate)
    sandbox = inspect.signature(SandboxStagePort.execute)

    assert list(programmer.parameters) == [
        "self",
        "programmer_input",
        "model_tier",
    ]
    assert "binding" not in str(programmer).casefold()
    assert list(sandbox.parameters) == ["self", "request"]


def test_retrieval_artifacts_preserve_missing_source_table_class():
    _, _, _, _, query, _, _ = approved_contracts()
    evidence, location = grounded_cell()
    assert location.table_class is None

    artifacts = RetrievalArtifacts(
        query=query,
        cell_locations=(location,),
        evidence_items=(evidence,),
    )

    assert artifacts.cell_locations[0].table_class is None
