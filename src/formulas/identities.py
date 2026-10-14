"""Accounting identities of the TT200/TT202 corporate statements (ADR-075).

This module is the single source of truth for how printed lines of B01-DN,
B02-DN and B03-DN add up, keyed by the printed line code ("Mã số"). A
reconstructed statement set is checked against them per period column; checks
only report PASS/FAIL/MISSING and never change a printed figure.

Values are ``(amount_in_VND, printed_unit)`` keyed by ``(statement, code,
column)`` where column is ``current`` or ``prior``. The printed unit (1 for VND,
10**6 for millions, ...) sets the rounding tolerance: two printed units of the
coarsest member, plus a 1e-9 relative slack, as the catalog guard does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from src.text_to_pandas.metrics import BALANCE_SHEET, CASH_FLOW_STATEMENT, INCOME_STATEMENT

CURRENT = "current"
PRIOR = "prior"
COLUMNS = (CURRENT, PRIOR)
PASS, FAIL, MISSING = "PASS", "FAIL", "MISSING"

Values = Mapping[tuple[str, str, str], tuple[Decimal, int]]


@dataclass(frozen=True)
class Term:
    statement: str
    code: str
    sign: int = 1
    column: str | None = None  # None: the column being checked


@dataclass(frozen=True)
class Identity:
    name: str
    total: Term
    summands: tuple[Term, ...]
    columns: tuple[str, ...] = COLUMNS


def _sum(statement: str, total: str, *codes: str, name: str | None = None) -> Identity:
    """``total = ±code ± code ...``; a leading ``-`` subtracts the line."""
    terms = tuple(Term(statement, code.lstrip("-"), -1 if code.startswith("-") else 1) for code in codes)
    text = " ".join(("- " if term.sign < 0 else "+ ") + term.code for term in terms).lstrip("+ ")
    return Identity(name or f"{FORM[statement]} {total} = {text}", Term(statement, total), terms)


BS, IS, CF = BALANCE_SHEET, INCOME_STATEMENT, CASH_FLOW_STATEMENT
FORM = {BS: "B01", IS: "B02", CF: "B03"}
IDENTITIES: tuple[Identity, ...] = (
    _sum(BS, "100", "110", "120", "130", "140", "150"),
    _sum(BS, "200", "210", "220", "230", "240", "250", "260"),
    _sum(BS, "270", "100", "200"),
    _sum(BS, "300", "310", "330"),
    _sum(BS, "400", "410", "430"),
    _sum(BS, "440", "300", "400"),
    _sum(BS, "270", "440"),
    _sum(IS, "10", "01", "-02"),
    _sum(IS, "20", "10", "-11"),
    _sum(IS, "30", "20", "21", "-22", "24", "-25", "-26"),
    _sum(IS, "40", "31", "-32"),
    _sum(IS, "50", "30", "40"),
    _sum(IS, "60", "50", "-51", "-52"),
    _sum(IS, "60", "61", "62"),
    _sum(CF, "50", "20", "30", "40"),
    _sum(CF, "70", "50", "60", "61"),
    Identity("B03 70 = B01 110", Term(CF, "70"), (Term(BS, "110"),)),
    Identity("B03 60 = B01 110 đầu năm", Term(CF, "60"), (Term(BS, "110", column=PRIOR),), columns=(CURRENT,)),
)

# Lines every complete statement prints; a missing one flags the report.
REQUIRED_TOTALS = {BS: ("270", "440"), IS: ("50", "60"), CF: ("50", "70")}

# Deductions some auditors print in parentheses; the sign is a presentation choice.
EXPENSE_CODES = ("02", "11", "22", "25", "26", "32", "51", "52")
_CONVENTION_PROBES = ("11", "22", "25", "26")


def expenses_parenthesized(values: Values) -> bool:
    """True when most nonzero current-year expense probes are printed negative."""
    probes = [values[(IS, code, CURRENT)][0] for code in _CONVENTION_PROBES
              if (IS, code, CURRENT) in values and values[(IS, code, CURRENT)][0] != 0]
    return bool(probes) and sum(value < 0 for value in probes) * 2 > len(probes)


def _as_costs(values: Values) -> dict[tuple[str, str, str], tuple[Decimal, int]]:
    """Expense lines as positive costs when the report prints them in parentheses."""
    if not expenses_parenthesized(values):
        return dict(values)
    return {key: ((-value, unit) if key[0] == IS and key[1] in EXPENSE_CODES else (value, unit))
            for key, (value, unit) in values.items()}


def _key(term: Term, column: str) -> tuple[str, str, str]:
    return (term.statement, term.code, term.column or column)


def check_identities(values: Values) -> list[dict[str, object]]:
    """One result per identity and column: PASS, FAIL, or MISSING when it cannot be tested.

    A missing summand counts as zero (forms omit lines without data); the check is
    MISSING only when the total, or every summand, is absent.
    """
    costs = _as_costs(values)
    results: list[dict[str, object]] = []
    for identity in IDENTITIES:
        for column in identity.columns:
            total = costs.get(_key(identity.total, column))
            present = [(term, costs[_key(term, column)]) for term in identity.summands
                       if _key(term, column) in costs]
            result: dict[str, object] = {
                "identity": identity.name, "column": column, "total_code": identity.total.code,
                "codes": [term.code for term in identity.summands],
                "lines": [[term.statement, term.code] for term in (identity.total, *identity.summands)],
            }
            if total is None or not present:
                results.append({**result, "status": MISSING})
                continue
            computed = sum((term.sign * value for term, (value, _unit) in present), Decimal(0))
            unit = max([total[1]] + [member_unit for _term, (_value, member_unit) in present])
            difference = computed - total[0]
            passed = abs(difference) <= 2 * unit + abs(total[0]) * Decimal("1e-9")
            results.append({**result, "status": PASS if passed else FAIL, "total": str(total[0]),
                            "computed": str(computed), "difference": str(difference)})
    return results


def missing_required(values: Values) -> list[str]:
    """``"<statement> <code>"`` for each required total absent from the current column."""
    return [f"{statement} {code}" for statement, codes in REQUIRED_TOTALS.items() for code in codes
            if (statement, code, CURRENT) not in values]
