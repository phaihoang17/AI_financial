from decimal import Decimal

from src.formulas.identities import (
    CURRENT,
    FAIL,
    IDENTITIES,
    MISSING,
    PASS,
    PRIOR,
    check_identities,
    expenses_parenthesized,
    missing_required,
)
from src.text_to_pandas.catalog_builder import _IDENTITIES
from src.text_to_pandas.metrics import BALANCE_SHEET, CASH_FLOW_STATEMENT, INCOME_STATEMENT, METRICS_BY_ID

BS, IS, CF = BALANCE_SHEET, INCOME_STATEMENT, CASH_FLOW_STATEMENT


def values(rows, column=CURRENT, unit=1):
    return {(statement, code, column): (Decimal(value), unit) for statement, code, value in rows}


def status(results, name, column=CURRENT):
    return next(r["status"] for r in results if r["identity"] == name and r["column"] == column)


def test_balance_sheet_passes_with_omitted_lines_and_fails_on_a_misread_total():
    rows = [(BS, "110", 40), (BS, "130", 60), (BS, "100", 100), (BS, "200", 50), (BS, "270", 150),
            (BS, "300", 90), (BS, "400", 60), (BS, "440", 150)]
    results = check_identities(values(rows))
    assert status(results, "B01 100 = 110 + 120 + 130 + 140 + 150") == PASS  # 120/140/150 omitted
    assert status(results, "B01 270 = 440") == PASS
    assert status(results, "B01 300 = 310 + 330") == MISSING  # no summand printed
    misread = check_identities(values([*rows[:-1], (BS, "440", 160)]))
    assert status(misread, "B01 270 = 440") == FAIL
    assert status(misread, "B01 270 = 440", PRIOR) == MISSING


def test_tolerance_is_two_printed_units_of_the_coarsest_scale():
    million = 10**6
    rows = values([(BS, "270", 150 * million), (BS, "440", 150 * million + 2 * million)], unit=million)
    assert status(check_identities(rows), "B01 270 = 440") == PASS
    rows[(BS, "440", CURRENT)] = (Decimal(150 * million + 3 * million), million)
    assert status(check_identities(rows), "B01 270 = 440") == FAIL


def test_parenthesized_expenses_are_read_as_costs():
    printed = [(IS, "10", 100), (IS, "11", -70), (IS, "20", 30), (IS, "22", -5), (IS, "25", -3), (IS, "26", -2),
               (IS, "30", 20), (IS, "50", 20), (IS, "51", -4), (IS, "52", 1), (IS, "60", 17)]
    assert expenses_parenthesized(values(printed))
    results = check_identities(values(printed))
    assert status(results, "B02 20 = 10 - 11") == PASS
    assert status(results, "B02 30 = 20 + 21 - 22 + 24 - 25 - 26") == PASS
    assert status(results, "B02 60 = 50 - 51 - 52") == PASS  # deferred tax income printed positive
    as_costs = [(s, c, abs(v)) if c in {"11", "22", "25", "26", "51"} else (s, c, v) for s, c, v in printed]
    as_costs = [(s, c, -1) if c == "52" else (s, c, v) for s, c, v in as_costs]
    assert not expenses_parenthesized(values(as_costs))
    assert status(check_identities(values(as_costs)), "B02 60 = 50 - 51 - 52") == PASS


def test_cash_flow_ties_to_the_balance_sheet_cash_line():
    rows = {**values([(CF, "50", 10), (CF, "60", 30), (CF, "70", 40), (BS, "110", 40)]),
            **values([(BS, "110", 30)], column=PRIOR)}
    results = check_identities(rows)
    assert status(results, "B03 70 = 50 + 60 + 61") == PASS
    assert status(results, "B03 70 = B01 110") == PASS
    assert status(results, "B03 60 = B01 110 đầu năm") == PASS
    assert not any(r["identity"] == "B03 60 = B01 110 đầu năm" and r["column"] == PRIOR for r in results)


def test_missing_required_lists_absent_current_totals():
    rows = values([(BS, "270", 1), (BS, "440", 1), (IS, "50", 1), (CF, "50", 1), (CF, "70", 1)])
    assert missing_required(rows) == ["INCOME_STATEMENT 60"]


def test_catalog_guard_identities_have_code_keyed_counterparts():
    """The catalog's metric-keyed guard must not drift from this source of truth."""
    members = [{(term.statement, term.code) for term in (identity.total, *identity.summands)}
               for identity in IDENTITIES]
    implied = {"LIABILITIES_EQUITY_ASSETS"}  # 270 = 300 + 400 follows from 440 = 300 + 400 and 270 = 440
    for name, summands, total in _IDENTITIES:
        if name in implied:
            continue
        lines = {(METRICS_BY_ID[metric].statement, METRICS_BY_ID[metric].vas_codes[0])
                 for metric in (*summands, total)}
        assert lines in members, name
