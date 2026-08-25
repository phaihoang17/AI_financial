"""TASK-036 deterministic construction of retrieval queries."""

from __future__ import annotations

from typing import List

from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
from src.supervisor.schemas import Plan
from src.understanding.schemas import QueryUnderstanding


def _deduplicate_exact(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_retrieval_query(
    plan: Plan, understanding: QueryUnderstanding
) -> RetrievalQuery:
    """Build a query without paraphrasing, normalization, or backend access.

    The approved Plan supplies retrieval constraints and canonical metrics.
    QueryUnderstanding supplies the original question plus requested scale and
    unit.  Each query variant contains only an unmodified canonical field.
    """

    if not isinstance(plan, Plan):
        raise TypeError("plan must be a Plan")
    if not isinstance(understanding, QueryUnderstanding):
        raise TypeError("understanding must be a QueryUnderstanding")

    query_texts = [understanding.raw_question]
    query_texts.extend(plan.target_metrics)
    if plan.derived_target is not None:
        query_texts.append(plan.derived_target)
    query_texts.extend(plan.periods)
    query_texts.append(f"{plan.company.ticker} {plan.company.name}")

    return RetrievalQuery(
        raw_question=understanding.raw_question,
        company=RetrievalCompany(name=plan.company.name, ticker=plan.company.ticker),
        periods=list(plan.periods),
        period_kind=plan.period_kind,
        statement_scope=plan.statement_scope,
        target_metrics=list(plan.target_metrics),
        derived_target=plan.derived_target,
        evidence_sources=list(plan.evidence_sources),
        requested_scale=understanding.requested_scale,
        requested_unit=understanding.requested_unit,
        query_texts=_deduplicate_exact(query_texts),
        eligible_source_types=list(plan.evidence_sources),
    )
