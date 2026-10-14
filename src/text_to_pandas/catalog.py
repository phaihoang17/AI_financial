"""Read-only access to a built Text-to-Pandas catalog (ADR-069).

The catalog holds four approved DataFrames (three long-format statement
datasets and ``companies``) plus a provenance file that maps every ``fact_id``
back to its report, table and source cell. The parent process loads them once;
the isolated worker only ever receives a per-question slice as data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from src.formulas.ratios import RATIOS
from src.supervisor.formula_registry import get_formula_implementation
from src.text_to_pandas.catalog_builder import CATALOG_SCHEMA_VERSION, COMPANY_COLUMNS, FACT_COLUMNS
from src.text_to_pandas.metrics import DATASET_BY_STATEMENT, METRICS, METRICS_BY_ID

STATEMENT_DATASETS = tuple(DATASET_BY_STATEMENT.values())
DATASET_NAMES = STATEMENT_DATASETS + ("companies",)

_FACT_COLUMN_DOCS = {
    "fact_id": "str, unique source row id; keep it in `evidence`",
    "ticker": "str, stock ticker (e.g. 'AAA')",
    "company_name": "str",
    "fiscal_year": "int, fiscal year of the report the row was read from",
    "statement_scope": "str, 'HOP_NHAT' (consolidated) or 'RIENG' (separate/parent-only)",
    "line_code": "str or NaN, VAS line code 'Mã số' as printed (e.g. '60')",
    "line_item": "str, statement line label as printed (OCR text)",
    "metric": "str or NaN, canonical metric id when the line is a dictionary metric",
    "value": "float, amount in VND (raw, unscaled); 'VND/share' rows are per-share amounts",
    "unit": "str, 'VND' or 'VND/share'",
    "source_scale": "str, scale printed in the source table (already applied to value)",
}
_COMPANY_COLUMN_DOCS = {
    "ticker": "str", "company_name": "str",
    "template": "str, 'BANK' (credit-institution forms) or 'CORPORATE'",
    "first_year": "int", "last_year": "int",
    "statement_scopes": "str, comma-joined scopes with reports, e.g. 'HOP_NHAT,RIENG'",
}
_DATASET_DOCS = {
    "income_statement": "Income statement lines (flows for the fiscal year).",
    "balance_sheet": "Balance sheet lines (balances at the END of the fiscal year).",
    "cash_flow": "Cash flow statement lines (flows for the fiscal year).",
    "companies": "One row per company: name, statement template, covered years and scopes.",
}
CONVENTIONS = (
    "One row per reported statement line per report; only the report's own fiscal-year column is included.",
    "A metric appears at most once per (ticker, fiscal_year, statement_scope); a missing row means the value "
    "is not available. Never fill missing values with 0 or invent them.",
    "Expense metrics (COGS, SELLING_EXPENSES, GA_EXPENSES, FINANCIAL_EXPENSES, INTEREST_EXPENSE, "
    "OTHER_EXPENSES, CURRENT_TAX, DEFERRED_TAX) are positive costs; other lines keep the printed sign.",
    "The opening balance of year Y is the balance_sheet row of fiscal_year Y-1.",
    "Bank (template 'BANK') statements have no VAS line codes and only some metrics.",
)


class CatalogError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def frame_payload(frame: pd.DataFrame) -> dict[str, Any]:
    """JSON-safe columnar payload: NaN becomes null, dtypes are kept by name."""
    data = []
    for row in frame.itertuples(index=False, name=None):
        data.append([
            None if isinstance(item, float) and math.isnan(item) else
            (item.item() if hasattr(item, "item") else item)
            for item in row
        ])
    return {
        "columns": list(frame.columns),
        "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "data": data,
    }


@dataclass(frozen=True)
class DatasetSlice:
    """The exact rows one question may read, by dataset name."""

    frames: Mapping[str, pd.DataFrame]
    filters: Mapping[str, Any]

    def payload(self) -> dict[str, dict[str, Any]]:
        return {name: frame_payload(frame) for name, frame in self.frames.items()}

    def row_counts(self) -> dict[str, int]:
        return {name: int(len(frame)) for name, frame in self.frames.items()}


class DatasetCatalog:
    """Validated, in-memory catalog of the approved statement DataFrames."""

    def __init__(self, root: str | Path, *, verify_hashes: bool = True) -> None:
        self.root = Path(root)
        manifest_path = self.root / "manifest.json"
        if not manifest_path.is_file():
            raise CatalogError("CATALOG_MANIFEST_MISSING", str(manifest_path))
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("catalog_schema_version") != CATALOG_SCHEMA_VERSION:
            raise CatalogError("CATALOG_SCHEMA_UNSUPPORTED", str(self.manifest.get("catalog_schema_version")))
        self.catalog_id = self.manifest["catalog_id"]
        provenance_entry = self.manifest["files"]["provenance"]
        if verify_hashes and _sha256(self.root / provenance_entry["file"]) != provenance_entry["sha256"]:
            raise CatalogError("CATALOG_FILE_HASH_MISMATCH", provenance_entry["file"])
        self.frames: dict[str, pd.DataFrame] = {}
        for name in DATASET_NAMES:
            entry = self.manifest["files"][name]
            path = self.root / entry["file"]
            if verify_hashes and _sha256(path) != entry["sha256"]:
                raise CatalogError("CATALOG_FILE_HASH_MISMATCH", entry["file"])
            if name == "companies":
                frame = pd.read_csv(path, dtype={"ticker": str, "company_name": str, "template": str,
                                                 "statement_scopes": str})
                expected = list(COMPANY_COLUMNS)
            else:
                frame = pd.read_csv(path, dtype={"fact_id": str, "ticker": str, "company_name": str,
                                                 "statement_scope": str, "line_code": str, "line_item": str,
                                                 "metric": str, "unit": str, "source_scale": str},
                                    keep_default_na=False, na_values={"line_code": [""], "metric": [""]})
                frame["value"] = frame["value"].astype("float64")
                frame["fiscal_year"] = frame["fiscal_year"].astype("int64")
                expected = list(FACT_COLUMNS)
            if list(frame.columns) != expected:
                raise CatalogError("CATALOG_COLUMNS_INVALID", name)
            self.frames[name] = frame
        self._provenance_offsets: dict[str, int] | None = None

    @classmethod
    def from_frames(cls, frames: Mapping[str, pd.DataFrame], *, catalog_id: str = "fixture",
                    provenance: Mapping[str, Mapping[str, Any]] | None = None) -> DatasetCatalog:
        """An in-memory catalog (tests and explicitly labelled fixtures)."""
        catalog = cls.__new__(cls)
        catalog.root = None
        catalog.manifest = {"catalog_id": catalog_id, "catalog_schema_version": CATALOG_SCHEMA_VERSION}
        catalog.catalog_id = catalog_id
        catalog.frames = {}
        for name in DATASET_NAMES:
            frame = frames[name].copy()
            expected = list(COMPANY_COLUMNS if name == "companies" else FACT_COLUMNS)
            if list(frame.columns) != expected:
                raise CatalogError("CATALOG_COLUMNS_INVALID", name)
            if name != "companies":
                frame["value"] = frame["value"].astype("float64")
                frame["fiscal_year"] = frame["fiscal_year"].astype("int64")
            catalog.frames[name] = frame
        catalog._provenance_offsets = None
        catalog._fixture_provenance = dict(provenance or {})
        return catalog

    # ---- discovery helpers -------------------------------------------------

    @property
    def companies(self) -> pd.DataFrame:
        return self.frames["companies"]

    def tickers(self) -> list[str]:
        return list(self.companies["ticker"])

    def statement_frames(self) -> Iterable[tuple[str, pd.DataFrame]]:
        for name in STATEMENT_DATASETS:
            yield name, self.frames[name]

    def coverage(self, tickers: Sequence[str]) -> dict[tuple[str, int], set]:
        """(ticker, fiscal_year) -> statement scopes that have any statement row."""
        result: dict[tuple[str, int], set] = {}
        for _, frame in self.statement_frames():
            rows = frame[frame["ticker"].isin(list(tickers))][["ticker", "fiscal_year", "statement_scope"]]
            for ticker, year, scope in rows.drop_duplicates().itertuples(index=False, name=None):
                result.setdefault((ticker, int(year)), set()).add(scope)
        return result

    def slice(self, *, tickers: Sequence[str] = (), metrics: Sequence[str] = ()) -> DatasetSlice:
        """Company mode (all rows of the named companies) or screening mode (named metrics)."""
        frames: dict[str, pd.DataFrame] = {}
        for name, frame in self.statement_frames():
            if tickers:
                selected = frame[frame["ticker"].isin(list(tickers))]
            elif metrics:
                selected = frame[frame["metric"].isin(list(metrics))]
            else:
                raise CatalogError("CATALOG_SLICE_UNBOUNDED", "a company or metric filter is required")
            frames[name] = selected.reset_index(drop=True)
        frames["companies"] = self.companies.reset_index(drop=True)
        return DatasetSlice(frames, {"tickers": list(tickers), "metrics": list(metrics)})

    # ---- provenance --------------------------------------------------------

    def _offsets(self) -> dict[str, int]:
        if self._provenance_offsets is None:
            offsets: dict[str, int] = {}
            path = self.root / self.manifest["files"]["provenance"]["file"]
            with path.open("rb") as handle:
                position = 0
                for line in handle:
                    # Canonical sorted-key rows usually start with {"fact_id": "f_...";
                    # any other row is parsed, so a new field can never misindex rows.
                    if line.startswith(b'{"fact_id": "f_'):
                        offsets[line[13:35].decode("ascii")] = position
                    else:
                        offsets[json.loads(line)["fact_id"]] = position
                    position += len(line)
            self._provenance_offsets = offsets
        return self._provenance_offsets

    def provenance(self, fact_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if self.root is None:
            return {fact_id: dict(self._fixture_provenance[fact_id])
                    for fact_id in fact_ids if fact_id in self._fixture_provenance}
        offsets = self._offsets()
        path = self.root / self.manifest["files"]["provenance"]["file"]
        result: dict[str, dict[str, Any]] = {}
        with path.open("rb") as handle:
            for fact_id in fact_ids:
                if fact_id not in offsets:
                    continue
                handle.seek(offsets[fact_id])
                row = json.loads(handle.readline())
                if row["fact_id"] == fact_id:
                    result[fact_id] = row
        return result


# ---- schema context rendered for code generation ---------------------------


def _distinct(values: Iterable[Any], limit: int) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen and not (isinstance(value, float) and math.isnan(value)):
            seen.append(value)
        if len(seen) >= limit:
            break
    return seen


def schema_context(
    data: DatasetSlice,
    *,
    relevant_metrics: Sequence[str] = (),
    include_line_items: bool = False,
) -> dict[str, Any]:
    """Everything the code generator may rely on: datasets, columns, slice values, definitions."""
    datasets: dict[str, Any] = {}
    for name, frame in data.frames.items():
        docs = _COMPANY_COLUMN_DOCS if name == "companies" else _FACT_COLUMN_DOCS
        entry: dict[str, Any] = {
            "description": _DATASET_DOCS[name],
            "rows_in_slice": int(len(frame)),
            "columns": {column: docs[column] for column in frame.columns},
        }
        if name != "companies" and len(frame):
            entry["slice_values"] = {
                "tickers": sorted(frame["ticker"].unique().tolist())[:40],
                "ticker_count": int(frame["ticker"].nunique()),
                "fiscal_years": sorted(int(year) for year in frame["fiscal_year"].unique()),
                "statement_scopes": sorted(frame["statement_scope"].unique().tolist()),
                "metrics": sorted(frame["metric"].dropna().unique().tolist()),
            }
            sample = frame[frame["metric"].isin(list(relevant_metrics))] if relevant_metrics else frame
            sample = (sample if len(sample) else frame).head(3)
            entry["sample_rows"] = frame_payload(sample)["data"]
            if include_line_items:
                lines = frame[["line_code", "line_item", "metric"]].drop_duplicates(["line_item"])
                entry["line_items"] = [
                    [None if isinstance(code, float) else code, item, None if isinstance(metric, float) else metric]
                    for code, item, metric in lines.head(120).itertuples(index=False, name=None)
                ]
        datasets[name] = entry
    growth = get_formula_implementation("GROWTH_RATE")
    return {
        "dataframes": datasets,
        "metrics": [metric.to_dict() for metric in METRICS],
        "ratios": [ratio.to_dict() for ratio in RATIOS],
        "formulas": {
            "GROWTH_RATE": f"(current - previous) / previous * {growth.constants[0]}  (unit PERCENT; "
                           "previous = the earlier fiscal year)",
            "AVERAGE": ("arithmetic mean of the metric's values in exactly the requested fiscal years, e.g. "
                        "'bình quân/trung bình ... năm 2016 và 2017' = (value_2016 + value_2017) / 2; read no other "
                        "year. Opening/closing average balances belong only to ratio denominators."),
        },
        "conventions": list(CONVENTIONS),
    }


def metric_dataset(metric_id: str) -> str | None:
    metric = METRICS_BY_ID.get(metric_id)
    return None if metric is None else DATASET_BY_STATEMENT[metric.statement]
