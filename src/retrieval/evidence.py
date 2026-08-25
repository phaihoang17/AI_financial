"""TASK-038/039/03E exact provenance location, evidence, and completeness."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from src.evidence.schemas import CanonicalDecimal, EvidenceItem
from src.indexing.document_parser import parse_document
from src.indexing.embedding_artifact_builder import _iter_input_records, _validate_input_manifest
from src.indexing.grid_builder import build_logical_table_grid
from src.indexing.normalized_table_assembler import assemble_normalized_table
from src.indexing.paragraph_extractor import extract_paragraphs
from src.indexing.provenance_sidecar import _source_from_inventory
from src.indexing.schemas import HeaderPathEntry, NormalizedCell, Paragraph, SourceCell
from src.retrieval.schemas import RetrievalCandidate, RetrievalContractError, RetrievalQuery
from src.supervisor.schemas import EvidenceSource


class EvidenceLocationError(RetrievalContractError): pass

class ProvenanceRole(str, Enum): PRIMARY="PRIMARY"; CONTEXT="CONTEXT"
class MatchBasis(str, Enum):
    EXACT_ROW_PATH="EXACT_ROW_PATH"; EXACT_COLUMN_PATH="EXACT_COLUMN_PATH"; EXACT_PERIOD_LABEL="EXACT_PERIOD_LABEL"; STRUCTURAL="STRUCTURAL"; NONE="NONE"

def _id(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _norm(value: str) -> str:
    from src.indexing.cell_normalizer import normalize_cell_text
    return normalize_cell_text(value).casefold()

@dataclass(frozen=True)
class CellLocation:
    location_id: str; candidate_id: str; representation_id: str; chunk_id: str
    report_id: str; page_id: str; table_id: str; source_cell_id: str
    anchor_row: int; anchor_column: int; row_path: List[HeaderPathEntry]; column_path: List[HeaderPathEntry]
    normalized_text: str; numeric: object; provenance_role: ProvenanceRole
    matched_metric: Optional[str]; matched_period: Optional[str]; match_basis: MatchBasis

@dataclass(frozen=True)
class _Resolved:
    candidate: RetrievalCandidate; chunk: object; source: object; cells: Dict[str, NormalizedCell]; source_cells: Dict[str, SourceCell]; paragraphs: Dict[str, Paragraph]

class EvidenceProvenanceRepository:
    """Reconstruct only exact selected M2 records and their raw source objects."""
    def __init__(self, corpus_artifact_root: str | Path, raw_corpus_root: str | Path, *, sidecar=None):
        self.corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
        self.raw_root = Path(raw_corpus_root); self.sidecar = sidecar; self._cache: Dict[str, _Resolved] = {}

    def _source(self, report_id: str):
        item = next((item for item in self.corpus.manifest["reports"] if item["report_id"] == report_id), None)
        if item is None: raise EvidenceLocationError("EVIDENCE_REPORT_MISSING", report_id)
        return _source_from_inventory(self.raw_root, self.corpus.corpus_id, item)

    def resolve(self, candidate: RetrievalCandidate) -> _Resolved:
        if candidate.candidate_id in self._cache: return self._cache[candidate.candidate_id]
        record = next((r for r in _iter_input_records(self.corpus) if r.chunk.chunk_id == candidate.chunk_id and r.representation.representation_id == candidate.representation_id), None)
        if record is None: raise EvidenceLocationError("EVIDENCE_CHUNK_MISSING", candidate.candidate_id)
        source = self._source(candidate.report_id); document = parse_document(source); paragraphs = extract_paragraphs(source, document)
        tables = [assemble_normalized_table(table, build_logical_table_grid(table)) for page in document.pages for table in page.tables]
        normalized = next((table for table in tables if table.source_table_id == candidate.table_id), None)
        cells = {} if normalized is None else {cell.source_cell_id: cell for cell in normalized.cells}
        source_cells = {cell.cell_id: cell for page in document.pages for table in page.tables for cell in table.cells}
        value = _Resolved(candidate, record.chunk, source, cells, source_cells, {p.paragraph_id:p for p in paragraphs})
        self._cache[candidate.candidate_id] = value; return value

def locate_cells(query: RetrievalQuery, candidates: Sequence[RetrievalCandidate], repository: EvidenceProvenanceRepository) -> List[CellLocation]:
    """Locate only primary/context TABLE source cells; TEXT deliberately emits none."""
    locations=[]
    for candidate in candidates:
        if candidate.source_type is EvidenceSource.TEXT: continue
        resolved=repository.resolve(candidate); roles={**{x:ProvenanceRole.PRIMARY for x in resolved.chunk.primary_source_cell_ids}, **{x:ProvenanceRole.CONTEXT for x in resolved.chunk.context_source_cell_ids}}
        rows=[]
        for source_id, role in roles.items():
            cell=resolved.cells.get(source_id); source_cell=resolved.source_cells.get(source_id)
            if cell is None or source_cell is None: raise EvidenceLocationError("EVIDENCE_SOURCE_CELL_MISSING", source_id)
            rows.append((source_cell.source_span.start, source_id, role, cell))
        for _, source_id, role, cell in sorted(rows):
            labels_row=[entry.label for entry in cell.row_path]; labels_col=[entry.label for entry in cell.column_path]
            metric=next((m for m in query.target_metrics if _norm(m) in {_norm(x) for x in labels_row+labels_col}),None)
            period=next((p for p in query.periods if p in labels_row+labels_col),None)
            basis=MatchBasis.EXACT_ROW_PATH if metric and any(_norm(metric)==_norm(x) for x in labels_row) else MatchBasis.EXACT_COLUMN_PATH if metric else MatchBasis.EXACT_PERIOD_LABEL if period else MatchBasis.STRUCTURAL if labels_row or labels_col else MatchBasis.NONE
            locations.append(CellLocation(_id({"candidate":candidate.candidate_id,"cell":source_id}),candidate.candidate_id,candidate.representation_id,candidate.chunk_id or "",candidate.report_id,candidate.page_ids[0],candidate.table_id or "",source_id,cell.anchor_row,cell.anchor_column,list(cell.row_path),list(cell.column_path),cell.normalized_text,cell.numeric,role,metric,period,basis))
    return locations

def build_evidence_items(locations: Sequence[CellLocation], candidates: Sequence[RetrievalCandidate], repository: EvidenceProvenanceRepository, *, linked_table_ids_by_candidate: Optional[Mapping[str, Sequence[str]]]=None) -> List[EvidenceItem]:
    by_id={candidate.candidate_id:candidate for candidate in candidates}; result=[]
    for location in locations:
        candidate=by_id[location.candidate_id]; resolved=repository.resolve(candidate); source_cell=resolved.source_cells[location.source_cell_id]
        decimal_value=location.numeric.decimal_value
        result.append(EvidenceItem(_id({"candidate":candidate.candidate_id,"cell":location.source_cell_id}),EvidenceSource.TABLE,candidate.report_id,location.page_id,location.table_id,None,candidate.statement_scope,location.matched_period,location.matched_metric,location.row_path[-1].label if location.row_path else None,location.column_path[-1].label if location.column_path else None,[x.label for x in location.row_path],[x.label for x in location.column_path],None,source_cell.extracted_text,None if decimal_value is None else CanonicalDecimal(decimal_value),None,None,None,candidate.rrf_score if candidate.rrf_score is not None else candidate.bm25_score,candidate.rerank_score))
    for candidate in candidates:
        if candidate.source_type is EvidenceSource.TEXT:
            resolved=repository.resolve(candidate); paragraph=resolved.paragraphs.get(candidate.paragraph_id or "")
            if paragraph is None: raise EvidenceLocationError("EVIDENCE_PARAGRAPH_MISSING", candidate.candidate_id)
            persisted=[] if repository.sidecar is None else [link.table_id for link in repository.sidecar.get_links_by_paragraph_id(paragraph.paragraph_id)]
            requested=persisted if linked_table_ids_by_candidate is None else list(linked_table_ids_by_candidate.get(candidate.candidate_id, []))
            linked=[table_id for table_id in requested if table_id in persisted]
            associated=linked[0] if len(linked)==1 else None
            result.append(EvidenceItem(_id({"candidate":candidate.candidate_id,"paragraph":paragraph.paragraph_id}),EvidenceSource.TEXT,candidate.report_id,paragraph.page_id,None,associated,candidate.statement_scope,None,None,None,None,[],[],paragraph.raw_text,None,None,None,None,None,candidate.rrf_score if candidate.rrf_score is not None else candidate.bm25_score,candidate.rerank_score))
    return result

@dataclass(frozen=True)
class EvidenceRequirement:
    requirement_id:str; source_type:EvidenceSource; metric:Optional[str]; period:Optional[str]; required:bool
@dataclass(frozen=True)
class EvidenceRequirementResult:
    requirement:EvidenceRequirement; satisfied:bool; evidence_ids:List[str]; reason:Optional[str]
@dataclass(frozen=True)
class EvidenceCompletenessResult:
    complete:bool; requirements:List[EvidenceRequirementResult]; missing_requirement_ids:List[str]

def generate_evidence_requirements(query: RetrievalQuery) -> List[EvidenceRequirement]:
    pairs=[(m,p) for m in query.target_metrics for p in query.periods] if query.target_metrics and query.periods else [(m,None) for m in query.target_metrics] if query.target_metrics else [(None,p) for p in query.periods]
    result=[]
    if EvidenceSource.TABLE in query.evidence_sources:
        result += [EvidenceRequirement(_id({"source":"TABLE","metric":m,"period":p}),EvidenceSource.TABLE,m,p,True) for m,p in pairs]
    if EvidenceSource.TEXT in query.evidence_sources:
        result.append(EvidenceRequirement(_id({"source":"TEXT"}),EvidenceSource.TEXT,None,None,True))
    return result

def check_evidence_completeness(requirements: Sequence[EvidenceRequirement], evidence: Sequence[EvidenceItem], locations: Mapping[str,CellLocation]) -> EvidenceCompletenessResult:
    results=[]; missing=[]
    for requirement in requirements:
        ids=[]
        for item in evidence:
            if item.source_type is not requirement.source_type: continue
            if requirement.source_type is EvidenceSource.TEXT: ids.append(item.evidence_id); continue
            loc=locations.get(item.evidence_id)
            if loc and loc.match_basis not in {MatchBasis.STRUCTURAL,MatchBasis.NONE} and item.metric==requirement.metric and item.period==requirement.period: ids.append(item.evidence_id)
        satisfied=bool(ids) if requirement.required else True
        results.append(EvidenceRequirementResult(requirement,satisfied,ids,None if satisfied else "REQUIRED_EVIDENCE_MISSING"))
        if not satisfied: missing.append(requirement.requirement_id)
    return EvidenceCompletenessResult(not missing,results,missing)
