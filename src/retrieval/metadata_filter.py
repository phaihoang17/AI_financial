"""TASK-030 exact report-level filtering with no automatic relaxation."""

from __future__ import annotations

import re
from typing import Iterable, List, Tuple

from src.retrieval.schemas import RetrievalCandidate, RetrievalContractError, RetrievalQuery


_YEAR = re.compile(r"^(?P<year>[0-9]{4})$")
_QUARTER = re.compile(r"^(?P<year>[0-9]{4})-Q[1-4]$")
_CUMULATIVE = re.compile(r"^(?P<year>[0-9]{4})-(?:[1-9]|1[0-2])M$")
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def derive_report_years(periods: Iterable[str]) -> List[int]:
    """Derive report years in source order, removing only duplicate years."""

    years: List[int] = []
    seen = set()
    for index, period in enumerate(periods):
        if not isinstance(period, str):
            raise RetrievalContractError("INVALID_PERIOD", f"periods[{index}] must be a string")
        match = _YEAR.fullmatch(period) or _QUARTER.fullmatch(period) or _CUMULATIVE.fullmatch(period)
        if match is None:
            raise RetrievalContractError(
                "REPORT_YEAR_UNRESOLVED",
                f"periods[{index}] must be YYYY, YYYY-QN, or YYYY-NM",
            )
        year = int(match.group("year"))
        if year not in seen:
            seen.add(year)
            years.append(year)
    return years


def report_metadata_sql_constraints(
    query: RetrievalQuery, *, table_alias: str = ""
) -> Tuple[str, List[object]]:
    """Return TASK-030's exact constraints for a compatible SQLite table.

    This is deliberately the single SQL representation of TASK-030 semantics.
    Retrieval backends may use it only to pre-select candidate IDs; they must
    not add metric, content, or filter-relaxation behavior here.
    """

    if not isinstance(query, RetrievalQuery):
        raise TypeError("query must be a RetrievalQuery")
    if table_alias and not _SQL_IDENTIFIER.fullmatch(table_alias):
        raise RetrievalContractError("INVALID_SQL_TABLE_ALIAS", "table_alias must be an SQL identifier")

    prefix = f"{table_alias}." if table_alias else ""
    predicates: List[str] = []
    parameters: List[object] = []
    if query.company.ticker:
        predicates.append(f"{prefix}ticker = ?")
        parameters.append(query.company.ticker)
    if query.statement_scope is not None:
        predicates.append(f"{prefix}statement_scope = ?")
        parameters.append(query.statement_scope.value)
    years = derive_report_years(query.periods)
    if years:
        predicates.append(f"{prefix}report_year IN ({','.join('?' for _ in years)})")
        parameters.extend(years)
    return (" AND ".join(predicates) if predicates else "1=1", parameters)


def filter_by_report_metadata(
    query: RetrievalQuery, candidates: Iterable[RetrievalCandidate]
) -> List[RetrievalCandidate]:
    """Apply only exact ticker, explicit scope, and derived report-year filters.

    Candidate iteration order is intentionally retained.  This function does
    not inspect metric, scale, unit, content, or any retrieval score.
    """

    if not isinstance(query, RetrievalQuery):
        raise TypeError("query must be a RetrievalQuery")
    report_years = derive_report_years(query.periods)
    allowed_years = set(report_years)
    result: List[RetrievalCandidate] = []
    for candidate in candidates:
        if not isinstance(candidate, RetrievalCandidate):
            raise TypeError("candidates must contain RetrievalCandidate values")
        if query.company.ticker and candidate.ticker != query.company.ticker:
            continue
        if query.statement_scope is not None and candidate.statement_scope != query.statement_scope:
            continue
        if allowed_years and candidate.report_year not in allowed_years:
            continue
        result.append(candidate)
    return result
