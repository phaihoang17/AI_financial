"""Canonical-corpus reference index for gold-annotation validation (TASK-100).

The gold retrieval/answer evaluation set (``src/evaluation/gold_retrieval.py``)
must only reference report / table / paragraph / chunk / source-cell ids that
*actually exist* in the committed canonical M2 corpus artifact. This module
builds that lookup by streaming the corpus shards once and caching the result.

It reads only the committed corpus artifact (``manifest.json`` + ``shards/``).
It never loads a model, never touches the vector or BM25 index, and computes no
retrieval metric. Building the full index is a single linear pass over the
shard JSONL; the cache lets validation runs re-use it.

    from src.evaluation.corpus_reference_index import load_or_build_reference_index
    index = load_or_build_reference_index("<corpus artifact dir>")
    index.has_report(report_id)         # -> bool
    index.table_report(table_id)        # -> report_id | None
    index.report_meta(report_id)        # -> ReportMeta | None
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, Set

CORPUS_REFERENCE_INDEX_SCHEMA_VERSION = "m10-corpus-reference-index-v1"


class CorpusReferenceIndexError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReportMeta:
    report_id: str
    ticker: str
    company_name: str
    report_year: int
    statement_scope: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "report_year": self.report_year,
            "statement_scope": self.statement_scope,
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ReportMeta":
        return cls(
            report_id=value["report_id"],
            ticker=value["ticker"],
            company_name=value["company_name"],
            report_year=int(value["report_year"]),
            statement_scope=value["statement_scope"],
        )


@dataclass
class CorpusReferenceIndex:
    """Existence sets + minimal metadata drawn from the canonical corpus."""

    corpus_artifact_id: str
    source_inventory_sha256: str
    reports: Dict[str, ReportMeta] = field(default_factory=dict)
    table_to_report: Dict[str, str] = field(default_factory=dict)
    paragraph_ids: Set[str] = field(default_factory=set)
    chunk_ids: Set[str] = field(default_factory=set)
    source_cell_ids: Set[str] = field(default_factory=set)
    representation_ids: Set[str] = field(default_factory=set)
    tickers_by_year_scope: Dict[str, Set[str]] = field(default_factory=dict)

    # -- lookups -----------------------------------------------------------
    def has_report(self, report_id: str) -> bool:
        return report_id in self.reports

    def report_meta(self, report_id: str) -> Optional[ReportMeta]:
        return self.reports.get(report_id)

    def has_table(self, table_id: str) -> bool:
        return table_id in self.table_to_report

    def table_report(self, table_id: str) -> Optional[str]:
        return self.table_to_report.get(table_id)

    def has_paragraph(self, paragraph_id: str) -> bool:
        return paragraph_id in self.paragraph_ids

    def has_chunk(self, chunk_id: str) -> bool:
        return chunk_id in self.chunk_ids

    def has_source_cell(self, source_cell_id: str) -> bool:
        return source_cell_id in self.source_cell_ids

    def reports_for(self, ticker: str, year: int, scope: Optional[str] = None) -> list[ReportMeta]:
        out = [
            meta
            for meta in self.reports.values()
            if meta.ticker == ticker
            and meta.report_year == year
            and (scope is None or meta.statement_scope == scope)
        ]
        return sorted(out, key=lambda m: m.report_id)

    def known_ticker(self, ticker: str) -> bool:
        return any(meta.ticker == ticker for meta in self.reports.values())

    # -- summary ---------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        return {
            "schema_version": CORPUS_REFERENCE_INDEX_SCHEMA_VERSION,
            "corpus_artifact_id": self.corpus_artifact_id,
            "source_inventory_sha256": self.source_inventory_sha256,
            "report_count": len(self.reports),
            "table_count": len(self.table_to_report),
            "paragraph_count": len(self.paragraph_ids),
            "chunk_count": len(self.chunk_ids),
            "source_cell_count": len(self.source_cell_ids),
            "representation_count": len(self.representation_ids),
            "distinct_tickers": len({m.ticker for m in self.reports.values()}),
        }

    # -- cache serialisation -------------------------------------------
    def to_cache_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": CORPUS_REFERENCE_INDEX_SCHEMA_VERSION,
            "corpus_artifact_id": self.corpus_artifact_id,
            "source_inventory_sha256": self.source_inventory_sha256,
            "reports": [meta.to_dict() for meta in self.reports.values()],
            "table_to_report": self.table_to_report,
            "paragraph_ids": sorted(self.paragraph_ids),
            "chunk_ids": sorted(self.chunk_ids),
            "source_cell_ids": sorted(self.source_cell_ids),
            "representation_ids": sorted(self.representation_ids),
        }

    @classmethod
    def from_cache_dict(cls, value: Dict[str, Any]) -> "CorpusReferenceIndex":
        if value.get("schema_version") != CORPUS_REFERENCE_INDEX_SCHEMA_VERSION:
            raise CorpusReferenceIndexError(
                "CACHE_SCHEMA_MISMATCH", f"unexpected schema {value.get('schema_version')!r}"
            )
        index = cls(
            corpus_artifact_id=value["corpus_artifact_id"],
            source_inventory_sha256=value["source_inventory_sha256"],
        )
        for meta_dict in value["reports"]:
            meta = ReportMeta.from_dict(meta_dict)
            index.reports[meta.report_id] = meta
            key = _year_scope_key(meta.report_year, meta.statement_scope)
            index.tickers_by_year_scope.setdefault(key, set()).add(meta.ticker)
        index.table_to_report = dict(value["table_to_report"])
        index.paragraph_ids = set(value["paragraph_ids"])
        index.chunk_ids = set(value["chunk_ids"])
        index.source_cell_ids = set(value["source_cell_ids"])
        index.representation_ids = set(value["representation_ids"])
        return index


def _year_scope_key(year: int, scope: str) -> str:
    return f"{year}::{scope}"


def _iter_shard_paths(corpus_root: Path) -> list[Path]:
    shards_dir = corpus_root / "shards"
    if not shards_dir.is_dir():
        raise CorpusReferenceIndexError(
            "CORPUS_SHARDS_MISSING", f"no shards/ under {corpus_root}"
        )
    paths = sorted(shards_dir.glob("shard-*.jsonl"))
    if not paths:
        raise CorpusReferenceIndexError("CORPUS_SHARDS_EMPTY", f"no shard files in {shards_dir}")
    return paths


def _iter_records(paths: Iterable[Path]) -> Iterator[Dict[str, Any]]:
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError as error:
                    raise CorpusReferenceIndexError(
                        "CORPUS_RECORD_INVALID", f"{path.name}:{line_number}: {error}"
                    ) from error


def build_reference_index(
    corpus_artifact_dir: str | Path, *, max_shards: Optional[int] = None
) -> CorpusReferenceIndex:
    """Stream the canonical corpus shards and collect existence sets.

    ``max_shards`` bounds the scan for fast local iteration; a real validation
    run must build the full index (``max_shards=None``).
    """
    corpus_root = Path(corpus_artifact_dir)
    manifest_path = corpus_root / "manifest.json"
    if not manifest_path.is_file():
        raise CorpusReferenceIndexError(
            "CORPUS_MANIFEST_MISSING", f"no manifest.json under {corpus_root}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    index = CorpusReferenceIndex(
        corpus_artifact_id=manifest.get("artifact_id", ""),
        source_inventory_sha256=manifest.get("source_inventory_sha256", ""),
    )
    for report in manifest.get("reports", []):
        meta = ReportMeta(
            report_id=report["report_id"],
            ticker=report["ticker"],
            company_name=report.get("company_name", ""),
            report_year=int(report["report_year"]),
            statement_scope=report["statement_scope"],
        )
        index.reports[meta.report_id] = meta
        key = _year_scope_key(meta.report_year, meta.statement_scope)
        index.tickers_by_year_scope.setdefault(key, set()).add(meta.ticker)

    paths = _iter_shard_paths(corpus_root)
    if max_shards is not None:
        paths = paths[:max_shards]

    for record in _iter_records(paths):
        representation = record.get("representation", {})
        report = record.get("report", {})
        report_id = report.get("report_id") or representation.get("report_id")
        representation_id = representation.get("representation_id")
        if representation_id:
            index.representation_ids.add(representation_id)
        table_id = representation.get("table_id")
        if table_id and report_id:
            index.table_to_report.setdefault(table_id, report_id)
        paragraph_id = representation.get("paragraph_id")
        if paragraph_id:
            index.paragraph_ids.add(paragraph_id)
        chunk = record.get("chunk") or {}
        chunk_id = chunk.get("chunk_id")
        if chunk_id:
            index.chunk_ids.add(chunk_id)
        for cell_id in record.get("source_cell_ids", []) or []:
            index.source_cell_ids.add(cell_id)
        # report metadata may only appear on records if the manifest omitted it
        if report_id and report_id not in index.reports and report.get("ticker"):
            meta = ReportMeta(
                report_id=report_id,
                ticker=report["ticker"],
                company_name=report.get("company_name", ""),
                report_year=int(report.get("report_year", 0)),
                statement_scope=report.get("statement_scope", ""),
            )
            index.reports[report_id] = meta

    return index


def _cache_key(corpus_artifact_dir: Path, max_shards: Optional[int]) -> str:
    raw = f"{corpus_artifact_dir.resolve()}::{max_shards}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def default_cache_path(corpus_artifact_dir: str | Path, max_shards: Optional[int] = None) -> Path:
    corpus_root = Path(corpus_artifact_dir)
    cache_dir = Path("artifacts/gold")
    return cache_dir / f"corpus-reference-index-{_cache_key(corpus_root, max_shards)}.json"


def load_or_build_reference_index(
    corpus_artifact_dir: str | Path,
    *,
    max_shards: Optional[int] = None,
    cache_path: Optional[str | Path] = None,
    rebuild: bool = False,
) -> CorpusReferenceIndex:
    resolved_cache = Path(cache_path) if cache_path else default_cache_path(corpus_artifact_dir, max_shards)
    if resolved_cache.is_file() and not rebuild:
        try:
            return CorpusReferenceIndex.from_cache_dict(
                json.loads(resolved_cache.read_text(encoding="utf-8"))
            )
        except (ValueError, KeyError, CorpusReferenceIndexError):
            pass  # fall through to a fresh build
    index = build_reference_index(corpus_artifact_dir, max_shards=max_shards)
    resolved_cache.parent.mkdir(parents=True, exist_ok=True)
    resolved_cache.write_text(
        json.dumps(index.to_cache_dict(), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return index
