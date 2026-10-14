"""TASK-038/039/03E exact provenance location, evidence, and completeness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from src.evidence.schemas import CanonicalDecimal, EvidenceItem
from src.indexing.document_parser import parse_document
from src.indexing.embedding_artifact_builder import _iter_input_records, _validate_input_manifest
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.header_inference import normalize_period_label
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.provenance_sidecar import ProvenanceSidecarError, _extract_report_provenance, _source_from_inventory
from src.indexing.schemas import HeaderPathEntry, NormalizedCell, Paragraph, SourceCell
from src.retrieval.corpus_candidates import CorpusRecordLocator
from src.retrieval.hybrid_retriever import ScaleUnitHintRetriever
from src.retrieval.schemas import RetrievalCandidate, RetrievalContractError, RetrievalQuery, RetrievedScaleUnitHint
from src.supervisor.schemas import EvidenceSource, Plan, TableClass
from src.understanding.metric_normalizer import table_label_metric
from src.understanding.schemas import SchemaValidationError, StatementScope


class EvidenceLocationError(RetrievalContractError):
    pass


class ProvenanceRole(str, Enum):
    PRIMARY = "PRIMARY"
    CONTEXT = "CONTEXT"


class MatchBasis(str, Enum):
    EXACT_ROW_PATH = "EXACT_ROW_PATH"
    EXACT_COLUMN_PATH = "EXACT_COLUMN_PATH"
    EXACT_PERIOD_LABEL = "EXACT_PERIOD_LABEL"
    STRUCTURAL = "STRUCTURAL"
    NONE = "NONE"


def _id(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _norm(value: str) -> str:
    from src.indexing.cell_normalizer import normalize_cell_text

    return normalize_cell_text(value).casefold()


def label_names_metric(label: str, metric: str) -> bool:
    """A header label names metric exactly, or as a registry statement line label."""
    return _norm(label) == _norm(metric) or table_label_metric(label) == metric


# A year column header "Năm 2023" / "Năm tài chính 2023" / "2023 Triệu đồng", optionally
# with its currency and scale word.
_YEAR_COLUMN_HEADER = re.compile(r"(?:năm (?:tài chính )?)?([0-9]{4}) ?(?:(?:triệu|nghìn|ngàn) ?)?(?:vnd|vnđ|đồng)?")

# "(Cho) năm (tài chính) kết thúc (vào) ngày 31 tháng 12 năm 2022" names fiscal year 2022.
_YEAR_END_HEADER = re.compile(
    r"(?:cho )?năm (?:tài chính )?kết thúc (?:vào )?ngày 31 tháng 12 năm ([0-9]{4}) ?(?:vnd|vnđ|đồng)?"
)
# "Năm nay", optionally with its currency ("Năm nay VND").
_CURRENT_YEAR_HEADER = re.compile(r"năm nay ?(?:vnd|vnđ|đồng)?")


def path_periods(labels: Sequence[str], report_year: int | None) -> list[str]:
    """Periods a header path names: its labels, their approved period form, an explicit
    "Năm YYYY" year column, and the report's own year for a VAS "Năm nay" column.
    "Năm trước" is deliberately not mapped: period P comes only from report year P
    (TASK-030 filter, TASK-080 check)."""
    periods = [*labels, *(p for p in map(normalize_period_label, labels) if p is not None)]
    periods.extend(m.group(1) for m in (_YEAR_COLUMN_HEADER.fullmatch(_norm(label)) for label in labels) if m)
    periods.extend(m.group(1) for m in (_YEAR_END_HEADER.fullmatch(_norm(label)) for label in labels) if m)
    if report_year is not None and any(_CURRENT_YEAR_HEADER.fullmatch(_norm(label)) for label in labels):
        periods.append(str(report_year))
    return periods


@dataclass(frozen=True)
class CellLocation:
    location_id: str
    candidate_id: str
    representation_id: str
    chunk_id: str
    report_id: str
    page_id: str
    table_id: str
    source_cell_id: str
    anchor_row: int
    anchor_column: int
    row_path: list[HeaderPathEntry]
    column_path: list[HeaderPathEntry]
    normalized_text: str
    numeric: object
    provenance_role: ProvenanceRole
    matched_metric: str | None
    matched_period: str | None
    match_basis: MatchBasis
    ticker: str | None = None
    company_name: str | None = None
    report_year: int | None = None
    statement_scope: StatementScope | None = None
    table_class: TableClass | None = None

    def to_dict(self) -> dict[str, Any]:
        if not hasattr(self.numeric, "to_dict"):
            raise SchemaValidationError("CellLocation.numeric must be serializable")
        return {
            "location_id": self.location_id,
            "candidate_id": self.candidate_id,
            "representation_id": self.representation_id,
            "chunk_id": self.chunk_id,
            "report_id": self.report_id,
            "page_id": self.page_id,
            "table_id": self.table_id,
            "source_cell_id": self.source_cell_id,
            "anchor_row": self.anchor_row,
            "anchor_column": self.anchor_column,
            "row_path": [item.to_dict() for item in self.row_path],
            "column_path": [item.to_dict() for item in self.column_path],
            "normalized_text": self.normalized_text,
            "numeric": self.numeric.to_dict(),
            "provenance_role": self.provenance_role.value,
            "matched_metric": self.matched_metric,
            "matched_period": self.matched_period,
            "match_basis": self.match_basis.value,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "report_year": self.report_year,
            "statement_scope": (None if self.statement_scope is None else self.statement_scope.value),
            "table_class": (None if self.table_class is None else self.table_class.value),
        }

    @classmethod
    def from_dict(cls, value: Any) -> CellLocation:
        if not isinstance(value, Mapping):
            raise SchemaValidationError("CellLocation must be an object")
        expected = {
            "location_id",
            "candidate_id",
            "representation_id",
            "chunk_id",
            "report_id",
            "page_id",
            "table_id",
            "source_cell_id",
            "anchor_row",
            "anchor_column",
            "row_path",
            "column_path",
            "normalized_text",
            "numeric",
            "provenance_role",
            "matched_metric",
            "matched_period",
            "match_basis",
            "ticker",
            "company_name",
            "report_year",
            "statement_scope",
            "table_class",
        }
        if set(value) != expected:
            raise SchemaValidationError("CellLocation fields must match the canonical schema")
        if not isinstance(value["row_path"], list) or not isinstance(value["column_path"], list):
            raise SchemaValidationError("CellLocation paths must be lists")
        from src.indexing.schemas import NumericParseResult

        try:
            role = ProvenanceRole(value["provenance_role"])
            basis = MatchBasis(value["match_basis"])
            scope = None if value["statement_scope"] is None else StatementScope(value["statement_scope"])
            table_class = None if value["table_class"] is None else TableClass(value["table_class"])
        except (TypeError, ValueError) as error:
            raise SchemaValidationError("CellLocation contains an invalid enum") from error
        return cls(
            location_id=value["location_id"],
            candidate_id=value["candidate_id"],
            representation_id=value["representation_id"],
            chunk_id=value["chunk_id"],
            report_id=value["report_id"],
            page_id=value["page_id"],
            table_id=value["table_id"],
            source_cell_id=value["source_cell_id"],
            anchor_row=value["anchor_row"],
            anchor_column=value["anchor_column"],
            row_path=[HeaderPathEntry.from_dict(item) for item in value["row_path"]],
            column_path=[HeaderPathEntry.from_dict(item) for item in value["column_path"]],
            normalized_text=value["normalized_text"],
            numeric=NumericParseResult.from_dict(value["numeric"]),
            provenance_role=role,
            matched_metric=value["matched_metric"],
            matched_period=value["matched_period"],
            match_basis=basis,
            ticker=value["ticker"],
            company_name=value["company_name"],
            report_year=value["report_year"],
            statement_scope=scope,
            table_class=table_class,
        )


@dataclass(frozen=True)
class _Resolved:
    candidate: RetrievalCandidate
    chunk: object
    source: object
    cells: dict[str, NormalizedCell]
    source_cells: dict[str, SourceCell]
    paragraphs: dict[str, Paragraph]


class _DerivedProvenance:
    """ADR-062: the sidecar lookup contract over links/hints re-derived from raw reports."""

    def __init__(self, hints, links):
        self._hints_by_ref: dict[str, list] = {}
        self._hints_by_table: dict[str, list] = {}
        self._links_by_table: dict[str, list] = {}
        self._links_by_paragraph: dict[str, list] = {}
        for hint in hints:
            self._hints_by_ref.setdefault(hint.source_ref, []).append(hint)
            if hint.table_id is not None:
                self._hints_by_table.setdefault(hint.table_id, []).append(hint)
        for link in links:
            self._links_by_table.setdefault(link.table_id, []).append(link)
            self._links_by_paragraph.setdefault(link.paragraph_id, []).append(link)

    def get_hints_by_source_ref(self, source_ref: str):
        return list(self._hints_by_ref.get(source_ref, []))

    def get_hints_by_table_id(self, table_id: str):
        return list(self._hints_by_table.get(table_id, []))

    def get_links_by_table_id(self, table_id: str):
        return list(self._links_by_table.get(table_id, []))

    def get_links_by_paragraph_id(self, paragraph_id: str):
        return list(self._links_by_paragraph.get(paragraph_id, []))


class EvidenceProvenanceRepository:
    """Reconstruct only exact selected M2 records and their raw source objects."""

    def __init__(
        self,
        corpus_artifact_root: str | Path,
        raw_corpus_root: str | Path,
        *,
        sidecar=None,
        table_class_sidecar=None,
        vector_metadata_path: str | Path | None = None,
    ):
        self.corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
        self.locator = None if vector_metadata_path is None else CorpusRecordLocator(self.corpus, vector_metadata_path)
        self.raw_root = Path(raw_corpus_root)
        self.sidecar = sidecar
        self.table_class_sidecar = table_class_sidecar
        self._cache: dict[str, _Resolved] = {}
        self._table_class_cache: dict[str, TableClass | None] = {}
        self._records: dict[tuple, object] = {}
        self._documents: dict[str, tuple] = {}
        self._provenance: dict[str, tuple] = {}

    def table_class_for(self, candidate: RetrievalCandidate) -> TableClass | None:
        """Exact source-backed TableClass from the sidecar, else None (no fallback)."""
        if self.table_class_sidecar is None:
            return None
        if candidate.candidate_id not in self._table_class_cache:
            page_id = candidate.page_ids[0] if candidate.page_ids else ""
            self._table_class_cache[candidate.candidate_id] = self.table_class_sidecar.table_class_for(
                candidate.report_id, page_id, candidate.table_id or ""
            )
        return self._table_class_cache[candidate.candidate_id]

    def _source(self, report_id: str):
        item = next((item for item in self.corpus.manifest["reports"] if item["report_id"] == report_id), None)
        if item is None:
            raise EvidenceLocationError("EVIDENCE_REPORT_MISSING", report_id)
        return _source_from_inventory(self.raw_root, self.corpus.corpus_id, item)

    def preload(self, candidates: Sequence[RetrievalCandidate]) -> None:
        """Find every uncached candidate record: by persisted position when a locator is
        set, else in one corpus pass (not one pass each)."""
        wanted = {(c.chunk_id, c.representation_id) for c in candidates if c.candidate_id not in self._cache} - set(
            self._records
        )
        if not wanted:
            return
        if self.locator is not None:
            self._records.update(self.locator.records(wanted))
            return
        for r in _iter_input_records(self.corpus):
            key = (r.chunk.chunk_id, r.representation.representation_id)
            if key in wanted:
                self._records[key] = r
                wanted.discard(key)
                if not wanted:
                    break

    def _document(self, report_id: str) -> tuple:
        if report_id not in self._documents:
            source = self._source(report_id)
            document = parse_document(source)
            paragraphs = extract_paragraphs(source, document)
            tables = [
                assemble_normalized_table(table, build_logical_table_grid(table))
                for page in document.pages
                for table in page.tables
            ]
            source_cells = {
                cell.cell_id: cell for page in document.pages for table in page.tables for cell in table.cells
            }
            self._documents[report_id] = (source, tables, source_cells, {p.paragraph_id: p for p in paragraphs})
        return self._documents[report_id]

    def for_candidates(self, candidates: Sequence[RetrievalCandidate]) -> dict[str, object]:
        """The CorpusChunkLookup contract, served from preloaded M2 records."""
        self.preload(candidates)
        chunks = {}
        for candidate in candidates:
            if candidate.source_type is not EvidenceSource.TABLE:
                continue
            record = self._records.get((candidate.chunk_id, candidate.representation_id))
            if record is None:
                raise EvidenceLocationError("EVIDENCE_CHUNK_MISSING", candidate.candidate_id)
            chunks[candidate.candidate_id] = record.chunk
        return chunks

    def scale_unit_hints(self, candidates: Sequence[RetrievalCandidate]) -> dict[str, list[RetrievedScaleUnitHint]]:
        """ADR-062: associate hints re-derived from the raw reports the cells are rebuilt from."""
        hints, links = [], []
        try:
            for report_id in sorted({candidate.report_id for candidate in candidates}):
                if report_id not in self._provenance:
                    self._provenance[report_id] = _extract_report_provenance(self._source(report_id))
                hints += self._provenance[report_id][0]
                links += self._provenance[report_id][1]
        except ProvenanceSidecarError as error:
            raise EvidenceLocationError(error.code, str(error)) from error
        return ScaleUnitHintRetriever(_DerivedProvenance(hints, links), self).retrieve(list(candidates))

    def resolve(self, candidate: RetrievalCandidate) -> _Resolved:
        if candidate.candidate_id in self._cache:
            return self._cache[candidate.candidate_id]
        self.preload([candidate])
        record = self._records.get((candidate.chunk_id, candidate.representation_id))
        if record is None:
            raise EvidenceLocationError("EVIDENCE_CHUNK_MISSING", candidate.candidate_id)
        source, tables, source_cells, paragraphs = self._document(candidate.report_id)
        normalized = next((table for table in tables if table.source_table_id == candidate.table_id), None)
        cells = {} if normalized is None else {cell.source_cell_id: cell for cell in normalized.cells}
        value = _Resolved(candidate, record.chunk, source, cells, source_cells, paragraphs)
        self._cache[candidate.candidate_id] = value
        return value


def locate_cells(
    query: RetrievalQuery, candidates: Sequence[RetrievalCandidate], repository: EvidenceProvenanceRepository
) -> list[CellLocation]:
    """Locate only primary/context TABLE source cells; TEXT deliberately emits none."""
    repository.preload(candidates)
    locations = []
    for candidate in candidates:
        if candidate.source_type is EvidenceSource.TEXT:
            continue
        resolved = repository.resolve(candidate)
        roles = {
            **{x: ProvenanceRole.PRIMARY for x in resolved.chunk.primary_source_cell_ids},
            **{x: ProvenanceRole.CONTEXT for x in resolved.chunk.context_source_cell_ids},
        }
        rows = []
        for source_id, role in roles.items():
            cell = resolved.cells.get(source_id)
            source_cell = resolved.source_cells.get(source_id)
            if cell is None or source_cell is None:
                raise EvidenceLocationError("EVIDENCE_SOURCE_CELL_MISSING", source_id)
            rows.append((source_cell.source_span.start, source_id, role, cell))
        for _, source_id, role, cell in sorted(rows):
            labels_row = [entry.label for entry in cell.row_path]
            labels_col = [entry.label for entry in cell.column_path]
            metric = next(
                (m for m in query.target_metrics if any(label_names_metric(x, m) for x in labels_row + labels_col)),
                None,
            )
            period = next(
                (p for p in query.periods if p in path_periods(labels_row + labels_col, candidate.report_year)), None
            )
            basis = (
                MatchBasis.EXACT_ROW_PATH
                if metric and any(label_names_metric(x, metric) for x in labels_row)
                else MatchBasis.EXACT_COLUMN_PATH
                if metric
                else MatchBasis.EXACT_PERIOD_LABEL
                if period
                else MatchBasis.STRUCTURAL
                if labels_row or labels_col
                else MatchBasis.NONE
            )
            locations.append(
                CellLocation(
                    _id({"candidate": candidate.candidate_id, "cell": source_id}),
                    candidate.candidate_id,
                    candidate.representation_id,
                    candidate.chunk_id or "",
                    candidate.report_id,
                    candidate.page_ids[0],
                    candidate.table_id or "",
                    source_id,
                    cell.anchor_row,
                    cell.anchor_column,
                    list(cell.row_path),
                    list(cell.column_path),
                    cell.normalized_text,
                    cell.numeric,
                    role,
                    metric,
                    period,
                    basis,
                    candidate.ticker,
                    candidate.company_name,
                    candidate.report_year,
                    candidate.statement_scope,
                    repository.table_class_for(candidate),
                )
            )
    return locations


def build_evidence_items(
    locations: Sequence[CellLocation],
    candidates: Sequence[RetrievalCandidate],
    repository: EvidenceProvenanceRepository,
    *,
    linked_table_ids_by_candidate: Mapping[str, Sequence[str]] | None = None,
) -> list[EvidenceItem]:
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    result = []
    repository.preload(candidates)
    for location in locations:
        candidate = by_id[location.candidate_id]
        resolved = repository.resolve(candidate)
        source_cell = resolved.source_cells[location.source_cell_id]
        decimal_value = location.numeric.decimal_value
        result.append(
            EvidenceItem(
                location.location_id,
                EvidenceSource.TABLE,
                candidate.report_id,
                location.page_id,
                location.table_id,
                None,
                candidate.statement_scope,
                location.matched_period,
                location.matched_metric,
                location.row_path[-1].label if location.row_path else None,
                location.column_path[-1].label if location.column_path else None,
                [x.label for x in location.row_path],
                [x.label for x in location.column_path],
                None,
                source_cell.extracted_text,
                None if decimal_value is None else CanonicalDecimal(decimal_value),
                None,
                None,
                None,
                candidate.rrf_score if candidate.rrf_score is not None else candidate.bm25_score,
                candidate.rerank_score,
                candidate.ticker,
                candidate.company_name,
                candidate.report_year,
                None,
                [],
            )
        )
    for candidate in candidates:
        if candidate.source_type is EvidenceSource.TEXT:
            resolved = repository.resolve(candidate)
            paragraph = resolved.paragraphs.get(candidate.paragraph_id or "")
            if paragraph is None:
                raise EvidenceLocationError("EVIDENCE_PARAGRAPH_MISSING", candidate.candidate_id)
            persisted_links = (
                []
                if repository.sidecar is None
                else repository.sidecar.get_links_by_paragraph_id(paragraph.paragraph_id)
            )
            persisted = [link.table_id for link in persisted_links]
            requested = (
                persisted
                if linked_table_ids_by_candidate is None
                else list(linked_table_ids_by_candidate.get(candidate.candidate_id, []))
            )
            linked = [table_id for table_id in requested if table_id in persisted]
            associated = linked[0] if len(linked) == 1 else None
            link_ids = (
                [] if associated is None else [link.link_id for link in persisted_links if link.table_id == associated]
            )
            result.append(
                EvidenceItem(
                    _id({"candidate": candidate.candidate_id, "paragraph": paragraph.paragraph_id}),
                    EvidenceSource.TEXT,
                    candidate.report_id,
                    paragraph.page_id,
                    None,
                    associated,
                    candidate.statement_scope,
                    None,
                    None,
                    None,
                    None,
                    [],
                    [],
                    paragraph.raw_text,
                    None,
                    None,
                    None,
                    None,
                    None,
                    candidate.rrf_score if candidate.rrf_score is not None else candidate.bm25_score,
                    candidate.rerank_score,
                    candidate.ticker,
                    candidate.company_name,
                    candidate.report_year,
                    paragraph.paragraph_id,
                    link_ids,
                )
            )
    return result


@dataclass(frozen=True)
class EvidenceRequirement:
    requirement_id: str
    source_type: EvidenceSource
    metric: str | None
    period: str | None
    required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "source_type": self.source_type.value,
            "metric": self.metric,
            "period": self.period,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, value: Any) -> EvidenceRequirement:
        if not isinstance(value, Mapping) or set(value) != {
            "requirement_id",
            "source_type",
            "metric",
            "period",
            "required",
        }:
            raise SchemaValidationError("EvidenceRequirement fields must match the canonical schema")
        try:
            source_type = EvidenceSource(value["source_type"])
        except (TypeError, ValueError) as error:
            raise SchemaValidationError("EvidenceRequirement.source_type is invalid") from error
        return cls(value["requirement_id"], source_type, value["metric"], value["period"], value["required"])


@dataclass(frozen=True)
class EvidenceRequirementResult:
    requirement: EvidenceRequirement
    satisfied: bool
    evidence_ids: list[str]
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement.to_dict(),
            "satisfied": self.satisfied,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Any) -> EvidenceRequirementResult:
        if not isinstance(value, Mapping) or set(value) != {"requirement", "satisfied", "evidence_ids", "reason"}:
            raise SchemaValidationError("EvidenceRequirementResult fields must match the canonical schema")
        if not isinstance(value["evidence_ids"], list):
            raise SchemaValidationError("evidence_ids must be a list")
        return cls(
            EvidenceRequirement.from_dict(value["requirement"]),
            value["satisfied"],
            list(value["evidence_ids"]),
            value["reason"],
        )


@dataclass(frozen=True)
class EvidenceCompletenessResult:
    complete: bool
    requirements: list[EvidenceRequirementResult]
    missing_requirement_ids: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.complete, bool):
            raise SchemaValidationError("complete must be a boolean")
        if not isinstance(self.requirements, list) or not all(
            isinstance(item, EvidenceRequirementResult) for item in self.requirements
        ):
            raise SchemaValidationError("requirements must contain EvidenceRequirementResult values")
        if not isinstance(self.missing_requirement_ids, list) or not all(
            isinstance(item, str) and item for item in self.missing_requirement_ids
        ):
            raise SchemaValidationError("missing_requirement_ids must contain non-empty strings")
        if len(self.missing_requirement_ids) != len(set(self.missing_requirement_ids)):
            raise SchemaValidationError("missing_requirement_ids must not contain duplicates")
        if self.complete != (not self.missing_requirement_ids):
            raise SchemaValidationError("complete must exactly reflect missing_requirement_ids")

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "requirements": [item.to_dict() for item in self.requirements],
            "missing_requirement_ids": list(self.missing_requirement_ids),
        }

    @classmethod
    def from_dict(cls, value: Any) -> EvidenceCompletenessResult:
        if not isinstance(value, Mapping) or set(value) != {"complete", "requirements", "missing_requirement_ids"}:
            raise SchemaValidationError("EvidenceCompletenessResult fields must match the canonical schema")
        if not isinstance(value["requirements"], list) or not isinstance(value["missing_requirement_ids"], list):
            raise SchemaValidationError("EvidenceCompletenessResult list fields must be lists")
        return cls(
            complete=value["complete"],
            requirements=[EvidenceRequirementResult.from_dict(item) for item in value["requirements"]],
            missing_requirement_ids=list(value["missing_requirement_ids"]),
        )


def generate_evidence_requirements(query: RetrievalQuery) -> list[EvidenceRequirement]:
    pairs = (
        [(m, p) for m in query.target_metrics for p in query.periods]
        if query.target_metrics and query.periods
        else [(m, None) for m in query.target_metrics]
        if query.target_metrics
        else [(None, p) for p in query.periods]
    )
    result = []
    if EvidenceSource.TABLE in query.evidence_sources:
        result += [
            EvidenceRequirement(_id({"source": "TABLE", "metric": m, "period": p}), EvidenceSource.TABLE, m, p, True)
            for m, p in pairs
        ]
    if EvidenceSource.TEXT in query.evidence_sources:
        result.append(EvidenceRequirement(_id({"source": "TEXT"}), EvidenceSource.TEXT, None, None, True))
    return result


def generate_plan_evidence_requirements(plan: Plan) -> list[EvidenceRequirement]:
    """Project canonical Plan requirements without changing their identities."""
    return [
        EvidenceRequirement(
            requirement.requirement_id,
            requirement.source_type,
            requirement.metric,
            requirement.period,
            requirement.required,
        )
        for requirement in plan.retrieval_requirements
    ]


def check_evidence_completeness(
    requirements: Sequence[EvidenceRequirement], evidence: Sequence[EvidenceItem], locations: Mapping[str, CellLocation]
) -> EvidenceCompletenessResult:
    results = []
    missing = []
    for requirement in requirements:
        ids = []
        for item in evidence:
            if item.source_type is not requirement.source_type:
                continue
            if requirement.source_type is EvidenceSource.TEXT:
                ids.append(item.evidence_id)
                continue
            loc = locations.get(item.evidence_id)
            if (
                loc
                and loc.match_basis not in {MatchBasis.STRUCTURAL, MatchBasis.NONE}
                and item.metric == requirement.metric
                and item.period == requirement.period
            ):
                ids.append(item.evidence_id)
        satisfied = bool(ids) if requirement.required else True
        results.append(
            EvidenceRequirementResult(requirement, satisfied, ids, None if satisfied else "REQUIRED_EVIDENCE_MISSING")
        )
        if not satisfied:
            missing.append(requirement.requirement_id)
    return EvidenceCompletenessResult(not missing, results, missing)
