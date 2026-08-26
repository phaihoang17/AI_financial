"""M8 deterministic verifier orchestration without retries or model calls.

TASK-104 adds optional parallel execution of the independent check groups. The
check-producing functions are pure functions of the immutable
``VerificationRequest``; running them concurrently and reassembling their
results in the same fixed order yields a byte-identical ``VerificationReport``
— same checks, same order, and therefore the same failure precedence — as the
sequential path. Sequential remains the default so existing M9 behavior is
unchanged.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional, Sequence

from src.verification.evidence_support import verify_evidence_support
from src.verification.financial_logic import verify_financial_logic
from src.verification.grounding import verify_grounding
from src.verification.numeric import verify_numeric
from src.verification.profiles import (
    VerificationProfileDecision,
    select_verification_profile,
)
from src.verification.scale_unit import verify_scale_unit
from src.verification.schemas import (
    VerificationCheckResult,
    VerificationReport,
    VerificationRequest,
)
from src.supervisor.schemas import VerifyProfile


_CheckGroup = Callable[[], Sequence[VerificationCheckResult]]


def _independent_check_groups(
    request: VerificationRequest, profile: VerificationProfileDecision
) -> List[_CheckGroup]:
    """Ordered independent check groups; order fixes the report/precedence."""
    groups: List[_CheckGroup] = [
        lambda: verify_grounding(request),
        lambda: verify_evidence_support(request),
        lambda: verify_numeric(request),
    ]
    if request.plan.requires_scale_resolution:
        groups.append(lambda: verify_scale_unit(request))
    if profile.effective_profile is VerifyProfile.STRICT:
        groups.append(lambda: verify_financial_logic(request))
    return groups


def _run_sequential(
    groups: Sequence[_CheckGroup],
) -> List[List[VerificationCheckResult]]:
    return [list(group()) for group in groups]


def _run_parallel(
    groups: Sequence[_CheckGroup],
) -> List[List[VerificationCheckResult]]:
    # Results are read back in submission order, so the assembled check list is
    # independent of completion order and any first-group exception propagates
    # exactly as it would sequentially.
    with ThreadPoolExecutor(max_workers=len(groups)) as executor:
        futures = [executor.submit(group) for group in groups]
        return [list(future.result()) for future in futures]


def verify(
    request: VerificationRequest, *, parallel: bool = False
) -> Optional[VerificationReport]:
    """Verify a successful execution; return ``None`` when execution is bypassed.

    ``parallel=True`` runs the independent check groups concurrently and must
    produce the same report as the default sequential path.
    """
    if not isinstance(request, VerificationRequest):
        raise TypeError("request must be a VerificationRequest")
    if not request.execution_result.success:
        return None
    profile = select_verification_profile(request)
    groups = _independent_check_groups(request, profile)
    results = _run_parallel(groups) if parallel else _run_sequential(groups)
    checks: List[VerificationCheckResult] = [*profile.checks]
    for group_result in results:
        checks.extend(group_result)
    return VerificationReport.create(checks)
