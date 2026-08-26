from __future__ import annotations

from dataclasses import replace

from src.evidence.schemas import CanonicalDecimal, Scale
from src.sandbox.schemas import ExecutionDatum, ExecutionOutput
from src.supervisor.schemas import EvidenceSource
from src.verification.verifier import verify
from tests.verification.helpers import failed_execution, make_request


def _requests():
    """A spread of requests exercising every independent check group."""
    return {
        "table_light": make_request([EvidenceSource.TABLE]),
        "text_light": make_request([EvidenceSource.TEXT]),
        "hybrid_strict": make_request([EvidenceSource.TABLE, EvidenceSource.TEXT]),
    }


def _wrong_scale(request):
    execution = request.execution_result
    values = list(execution.output.values)
    values[0] = ExecutionDatum(
        CanonicalDecimal(values[0].value), Scale.BILLION, values[0].unit
    )
    return replace(
        request,
        execution_result=replace(
            execution, output=ExecutionOutput(execution.output.kind, values)
        ),
    )


def test_parallel_matches_sequential_report_for_passing_requests():
    for name, request in _requests().items():
        sequential = verify(request)
        parallel = verify(request, parallel=True)
        assert sequential is not None and parallel is not None
        assert sequential.to_dict() == parallel.to_dict(), name
        # identical checks, in identical order (fixes failure precedence).
        assert [c.check_id for c in sequential.checks] == [
            c.check_id for c in parallel.checks
        ]


def test_parallel_matches_sequential_report_for_failing_checks():
    request = _wrong_scale(make_request([EvidenceSource.TABLE, EvidenceSource.TEXT]))
    sequential = verify(request)
    parallel = verify(request, parallel=True)

    assert sequential is not None and parallel is not None
    # We actually exercised a failure, and it is identical across both paths.
    assert sequential.passed is False
    assert sequential.to_dict() == parallel.to_dict()
    assert sequential.failure_category is parallel.failure_category
    assert sequential.failure_reason == parallel.failure_reason


def test_parallel_and_sequential_agree_on_bypassed_execution():
    request = replace(
        make_request([EvidenceSource.TABLE]),
        execution_result=failed_execution(),
    )
    assert verify(request) is None
    assert verify(request, parallel=True) is None


def test_sequential_is_the_default_path():
    request = make_request([EvidenceSource.TABLE, EvidenceSource.TEXT])
    # The default call must equal an explicit sequential call (M9 behavior).
    assert verify(request).to_dict() == verify(request, parallel=False).to_dict()
