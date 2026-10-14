"""Independent, source-grounded reference checks for Text-to-Pandas answers.

The generated evidence is checked against the immutable catalog, then the
requested calculation is recomputed from catalog rows, not from that evidence.
Unsupported result shapes or question semantics fail closed.
"""

from decimal import Decimal, InvalidOperation
import math
import re

from src.formulas.ratios import (
    AVERAGE_OPENING_CLOSING, RATIOS_BY_ID, average, compute_ratio, growth_rate,
)
from src.text_to_pandas.metrics import fold
from src.indexing.numeric_parser import parse_numeric_string
from src.indexing.schemas import NumericParseStatus


def _close(left, right):
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        if not a.is_finite() or not b.is_finite():
            return False
        # Allow floating-point rounding, not a relative financial error budget.
        rounding = Decimal(str(math.ulp(float(b)))) * 4
        return rounding.is_finite() and abs(a - b) <= max(rounding, Decimal("1e-12"))
    except (InvalidOperation, TypeError, ValueError):
        return False


def reference_answer(understanding, catalog):
    """Return keyed Decimal results and exact source rows, independently of code."""
    u = understanding
    if not u.required_metrics or (u.ratios and u.metrics) or len(u.ratios) > 1:
        raise ValueError("UNSUPPORTED_VERIFICATION: name explicit metrics or one ratio")
    if u.ratios and u.operation in {"growth", "aggregate"}:
        raise ValueError("UNSUPPORTED_VERIFICATION: compound ratio operations are not yet verified")
    frames = [frame for _, frame in catalog.statement_frames()]
    import pandas as pd
    facts = pd.concat(frames, ignore_index=True)
    tickers = u.tickers or tuple(sorted(facts["ticker"].unique()))
    years = u.years or tuple(sorted(facts[facts["ticker"].isin(tickers)]["fiscal_year"].unique()))
    if not u.tickers:
        # Screening excludes companies without all requested component rows.
        tickers = tuple(ticker for ticker in tickers if all(
            len(facts[(facts["ticker"] == ticker) & (facts["fiscal_year"] == year)
                      & (facts["statement_scope"] == u.statement_scope) & (facts["metric"] == metric)]) == 1
            for year in years for metric in u.required_metrics))
    if not tickers:
        raise ValueError("REFERENCE_INPUT_UNAVAILABLE: no company has the required rows")
    needed, outputs = {}, {}
    units = set()

    def source(ticker, year, metric):
        rows = facts[(facts["ticker"] == ticker) & (facts["fiscal_year"] == year) & (facts["metric"] == metric)]
        if u.statement_scope:
            rows = rows[rows["statement_scope"] == u.statement_scope]
        if len(rows) != 1:
            raise ValueError(f"REFERENCE_INPUT_UNAVAILABLE: {ticker}/{year}/{metric} requires one source row")
        row = rows.iloc[0].to_dict()
        needed[row["fact_id"]] = row
        units.add(row["unit"])
        return Decimal(str(row["value"]))

    text = fold(u.question)
    if any(cue in text for cue in ("lon hon", "nho hon", "greater than", "less than", "chenh lech", "difference")):
        raise ValueError("UNSUPPORTED_VERIFICATION: threshold/difference semantics are not yet verified")
    for ticker in tickers:
        for metric in u.ratios or u.metrics:
            if u.ratios:
                ratio = RATIOS_BY_ID[metric]
                for year in years:
                    numerator = source(ticker, year, ratio.numerator)
                    denominator = source(ticker, year, ratio.denominator)
                    opening = source(ticker, year - 1, ratio.denominator) \
                        if ratio.denominator_basis == AVERAGE_OPENING_CLOSING else None
                    value = compute_ratio(ratio, numerator, denominator, opening)
                    if value is None:
                        raise ValueError("ZERO_DENOMINATOR: ratio denominator is zero")
                    outputs[(ticker, int(year), metric)] = value
            elif u.operation == "growth":
                if len(years) != 2:
                    raise ValueError("UNSUPPORTED_VERIFICATION: growth requires exactly two years")
                previous, current = sorted(years)
                value = growth_rate(source(ticker, previous, metric), source(ticker, current, metric))
                if value is None:
                    raise ValueError("ZERO_DENOMINATOR: previous value is zero")
                outputs[(ticker, int(current), metric)] = value
            elif u.operation == "aggregate":
                values = [source(ticker, year, metric) for year in years]
                if any(cue in text for cue in ("trung binh", "binh quan", "average", "mean")):
                    value = average(values)
                elif any(cue in text for cue in ("tong", "sum")):
                    value = sum(values, Decimal(0))
                else:
                    raise ValueError("UNSUPPORTED_VERIFICATION: aggregate operation is unresolved")
                if value is None:
                    raise ValueError("REFERENCE_INPUT_UNAVAILABLE: aggregate requires more periods")
                outputs[(ticker, int(max(years)), metric)] = value
            elif u.operation in ("none", "compare") or u.ranking:
                for year in years:
                    outputs[(ticker, int(year), metric)] = source(ticker, year, metric)
            else:
                raise ValueError("UNSUPPORTED_VERIFICATION: operation is unresolved")
    if len(units) != 1:
        raise ValueError("UNIT_MISMATCH: source units differ")
    unit = RATIOS_BY_ID[u.ratios[0]].unit if u.ratios else "PERCENT" if u.operation == "growth" else next(iter(units))
    if u.ranking:
        if len(years) != 1 or len(u.required_metrics) != 1:
            raise ValueError("UNSUPPORTED_VERIFICATION: ranking requires one year and metric")
        low = any(cue in text for cue in ("thap nhat", "nho nhat", "lowest", "smallest"))
        match = re.search(r"\btop\s+(\d+)\b", text)
        count = int(match[1]) if match else 1
        ordered = sorted(outputs, key=lambda key: (outputs[key] if low else -outputs[key], key))[:count]
        outputs = {key: outputs[key] for key in ordered}
    return outputs, needed, unit


def verify_answer(understanding, program, execution, catalog, discovery):
    """Check source identity, period, scale/unit and financial arithmetic."""
    checks = []

    def check(code, passed, message):
        checks.append({"code": code, "passed": bool(passed), "message": message})

    if not execution.success:
        check("EXECUTION_FAILED", False, "execution must succeed before verification")
        return {"passed": False, "checks": checks, "sources": []}
    try:
        expected, required, unit = reference_answer(understanding, catalog)
    except ValueError as error:
        code, _, message = str(error).partition(": ")
        check(code, False, message)
        return {"passed": False, "checks": checks, "sources": []}

    evidence = execution.evidence or {}
    columns = evidence.get("columns", [])
    mandatory = {"fact_id", "ticker", "fiscal_year", "statement_scope", "metric", "line_item", "value", "unit", "source_scale"}
    valid_shape = (evidence.get("kind") == "dataframe" and not evidence.get("truncated")
                   and mandatory.issubset(columns) and len(columns) == len(set(columns))
                   and all(len(row) == len(columns) for row in evidence.get("data", [])))
    check("EVIDENCE_SHAPE", valid_shape, "evidence must retain complete, unmodified source columns")
    sources = []
    if valid_shape:
        rows = [dict(zip(columns, row)) for row in evidence["data"]]
        ids = [row["fact_id"] for row in rows]
        provenance = catalog.provenance(ids)
        allowed = {row["fact_id"] for _, frame in discovery.data.frames.items() if "fact_id" in frame
                   for row in frame[["fact_id"]].to_dict("records")}
        identity_ok = len(ids) == len(set(ids)) and set(ids) == set(required) and set(ids).issubset(allowed)
        values_ok = identity_ok and all(
            all(_close(row[key], required[row["fact_id"]][key]) if key == "value"
                else row[key] == required[row["fact_id"]][key] for key in mandatory)
            for row in rows)
        check("SOURCE_ROWS", values_ok, "evidence must match every independently required catalog row")
        source_ok = identity_ok and all(
            fact_id in provenance and all(provenance[fact_id].get(key) for key in
                ("report_id", "table_id", "source_cell_id", "source_ref", "period_basis", "scale_basis"))
            for fact_id in ids)
        check("SOURCE_PROVENANCE", source_ok, "every fact must have report, cell, period and scale provenance")
        raw_ok = source_ok
        for fact_id in ids if source_ok else []:
            row, origin = required[fact_id], provenance[fact_id]
            path = origin["source_ref"].split("/")
            numeric = parse_numeric_string(origin.get("raw_text", ""))
            scale = {"RAW": 1, "THOUSAND": 1000, "MILLION": 1000000, "BILLION": 1000000000}.get(row["source_scale"])
            if len(path) < 2 or path[:2] != [row["ticker"], str(row["fiscal_year"])] \
                    or numeric.status is not NumericParseStatus.PARSED or scale is None:
                raw_ok = False
                continue
            value = Decimal(numeric.decimal_value) * scale
            if origin.get("sign_basis") == "EXPENSES_PARENTHESIZED" and row["metric"] in {
                "COGS", "SELLING_EXPENSES", "GA_EXPENSES", "FINANCIAL_EXPENSES", "INTEREST_EXPENSE",
                "OTHER_EXPENSES", "CURRENT_TAX", "DEFERRED_TAX",
            }:
                value = -value
            raw_ok = raw_ok and _close(value, row["value"])
        check("SOURCE_PERIOD_SCALE", raw_ok, "source path year and parsed raw amount/scale must reproduce the catalog fact")
        sources = [provenance[fact_id] for fact_id in ids if fact_id in provenance]
    check("RESULT_UNIT", program.result_unit == unit, f"expected {unit}")
    result = execution.result or {}
    check("RESULT_TYPE", result.get("kind") == program.expected_result_type.value and not result.get("truncated"),
          "executed result must match its declared type without truncation")
    matches = result_matches(understanding, result, expected)
    check("FORMULA_RESULT", matches, "result must equal the independent calculation for the requested companies and periods")
    return {"passed": all(item["passed"] for item in checks), "checks": checks, "sources": sources}


def result_matches(understanding, result, expected):
    """Compare keyed executed values with an independent reference."""
    matches = False
    if result.get("kind") == "scalar":
        matches = len(expected) == 1 and _close(result.get("value"), next(iter(expected.values())))
    elif result.get("kind") == "dataframe":
        result_columns = result.get("columns", [])
        expected_columns = {"ticker", "fiscal_year", "metric", "value"} if "metric" in result_columns \
            else {"ticker", "fiscal_year", *(understanding.ratios or understanding.metrics)}
        if len(result_columns) != len(set(result_columns)) or set(result_columns) != expected_columns:
            return False
        records = [dict(zip(result_columns, row)) for row in result.get("data", [])]
        actual = {}
        try:
            for row in records:
                if "metric" in row and "value" in row:
                    actual[(row["ticker"], int(row["fiscal_year"]), row["metric"])] = row["value"]
                else:
                    for metric in understanding.ratios or understanding.metrics:
                        actual[(row["ticker"], int(row["fiscal_year"]), metric)] = row[metric]
            matches = (len(actual) == len(records) * (1 if "metric" in result_columns else len(understanding.ratios or understanding.metrics))
                       and set(actual) == set(expected) and all(_close(actual[key], value) for key, value in expected.items()))
            if understanding.ranking:
                matches = matches and list(actual) == list(expected)
        except (KeyError, TypeError, ValueError):
            matches = False
    elif result.get("kind") == "series" and len(understanding.ratios or understanding.metrics) == 1:
        metric = (understanding.ratios or understanding.metrics)[0]
        actual = {}
        try:
            for index, value in zip(result.get("index", []), result.get("values", [])):
                ticker, year = index if isinstance(index, list) else (understanding.tickers[0], index)
                actual[(ticker, int(year), metric)] = value
            matches = (len(actual) == len(result.get("values", [])) and set(actual) == set(expected)
                       and all(_close(actual[key], value) for key, value in expected.items()))
        except (IndexError, TypeError, ValueError):
            matches = False
    return matches
