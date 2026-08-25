"""Deterministic M3 retrieval evaluation contracts and metrics."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence

from src.evidence.schemas import EvidenceItem
from src.retrieval.evidence import EvidenceCompletenessResult
from src.retrieval.failure_taxonomy import RetrievalFailureStage, attribute_earliest_failure
from src.retrieval.schemas import MultiTableRetrievalResult, RetrievalCandidate, RetrievalContractError
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope


class RetrievalEvaluationMode(str, Enum): FIXTURE = "FIXTURE"; FULL_CORPUS = "FULL_CORPUS"

@dataclass(frozen=True)
class RequiredEvidence:
    source_type: EvidenceSource; table_id: Optional[str] = None; paragraph_id: Optional[str] = None
    source_cell_ids: tuple[str, ...] = (); metric: Optional[str] = None; period: Optional[str] = None
    report_id: Optional[str] = None
    def __post_init__(self):
        if not isinstance(self.source_type, EvidenceSource): object.__setattr__(self, "source_type", EvidenceSource(self.source_type))
        if self.source_type is EvidenceSource.TABLE and self.paragraph_id is not None: raise RetrievalContractError("EVAL_EXPECTED_INVALID", "TABLE expected evidence cannot have paragraph_id")
        if self.source_type is EvidenceSource.TEXT and self.table_id is not None: raise RetrievalContractError("EVAL_EXPECTED_INVALID", "TEXT expected evidence cannot have table_id")
        if any(not isinstance(value, str) or not value for value in self.source_cell_ids): raise RetrievalContractError("EVAL_EXPECTED_INVALID", "source_cell_ids must be non-empty strings")
    def to_dict(self): return {"source_type":self.source_type.value,"table_id":self.table_id,"paragraph_id":self.paragraph_id,"source_cell_ids":list(self.source_cell_ids),"metric":self.metric,"period":self.period,"report_id":self.report_id}
    @classmethod
    def from_dict(cls,value):
        fields={"source_type","table_id","paragraph_id","source_cell_ids","metric","period","report_id"}
        if not isinstance(value,dict) or set(value)!=fields: raise RetrievalContractError("EVAL_EXPECTED_INVALID","expected evidence fields do not match canonical schema")
        if not isinstance(value["source_cell_ids"],list): raise RetrievalContractError("EVAL_EXPECTED_INVALID","source_cell_ids must be a list")
        return cls(value["source_type"],value["table_id"],value["paragraph_id"],tuple(value["source_cell_ids"]),value["metric"],value["period"],value["report_id"])

@dataclass(frozen=True)
class RetrievalEvaluationCase:
    case_id: str; version: int; question: str; ticker: str; periods: tuple[str, ...]; statement_scope: Optional[StatementScope]; required_evidence: tuple[RequiredEvidence, ...]
    def __post_init__(self):
        if not isinstance(self.case_id,str) or not self.case_id or not isinstance(self.question,str) or not self.question or not isinstance(self.ticker,str) or not self.ticker: raise RetrievalContractError("EVAL_CASE_INVALID", "case_id, question, and ticker are required strings")
        if isinstance(self.version,bool) or not isinstance(self.version,int) or self.version < 1: raise RetrievalContractError("EVAL_CASE_INVALID", "version must be positive integer")
        if self.statement_scope is not None and not isinstance(self.statement_scope,StatementScope): object.__setattr__(self,"statement_scope",StatementScope(self.statement_scope))
        if not self.required_evidence: raise RetrievalContractError("EVAL_CASE_INVALID", "required_evidence cannot be empty")
    def to_dict(self): return {"case_id":self.case_id,"version":self.version,"question":self.question,"expected":{"ticker":self.ticker,"periods":list(self.periods),"statement_scope":None if self.statement_scope is None else self.statement_scope.value,"required_evidence":[x.to_dict() for x in self.required_evidence]}}
    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or set(value)!={"case_id","version","question","expected"}: raise RetrievalContractError("EVAL_CASE_INVALID","case fields do not match canonical schema")
        expected=value["expected"]; fields={"ticker","periods","statement_scope","required_evidence"}
        if not isinstance(expected,dict) or set(expected)!=fields: raise RetrievalContractError("EVAL_CASE_INVALID","expected fields do not match canonical schema")
        if not isinstance(expected["periods"],list) or not isinstance(expected["required_evidence"],list): raise RetrievalContractError("EVAL_CASE_INVALID","periods and required_evidence must be lists")
        return cls(value["case_id"],value["version"],value["question"],expected["ticker"],tuple(expected["periods"]),expected["statement_scope"],tuple(RequiredEvidence.from_dict(item) for item in expected["required_evidence"]))

def _match(candidate: RetrievalCandidate, expected: RequiredEvidence) -> bool:
    return candidate.source_type is expected.source_type and (expected.report_id is None or candidate.report_id == expected.report_id) and (expected.table_id is None or candidate.table_id == expected.table_id) and (expected.paragraph_id is None or candidate.paragraph_id == expected.paragraph_id)
def recall_at_k(candidates: Sequence[RetrievalCandidate], required: Sequence[RequiredEvidence], k: int) -> float:
    if k < 1: raise RetrievalContractError("EVAL_METRIC_INVALID", "k must be positive")
    if not required: return 1.0
    return sum(any(_match(candidate, expected) for candidate in candidates[:k]) for expected in required) / len(required)
def mrr(candidates: Sequence[RetrievalCandidate], required: Sequence[RequiredEvidence]) -> Optional[float]:
    for rank, candidate in enumerate(candidates,1):
        if any(_match(candidate, expected) for expected in required): return 1.0/rank
    return None

@dataclass(frozen=True)
class RetrievalEvaluationCaseResult:
    case_id:str; passed:bool; failure_stage:Optional[RetrievalFailureStage]; bm25_recall_at_k:float; vector_recall_at_k:float; rrf_recall_at_k:float; rerank_recall_at_k:float; mrr:Optional[float]; multi_table_completeness:float; evidence_grounded:bool; hybrid_complete:bool; table_requirement_recall:float; text_requirement_recall:float
    def to_dict(self): return {**self.__dict__,"failure_stage":None if self.failure_stage is None else self.failure_stage.value}

@dataclass(frozen=True)
class RetrievalEvaluationReport:
    mode:RetrievalEvaluationMode; results:tuple[RetrievalEvaluationCaseResult,...]
    @property
    def case_count(self): return len(self.results)
    @property
    def aggregate_metrics(self):
        keys=("bm25_recall_at_k","vector_recall_at_k","rrf_recall_at_k","rerank_recall_at_k","multi_table_completeness","table_requirement_recall","text_requirement_recall")
        return {key:sum(getattr(x,key) for x in self.results)/len(self.results) if self.results else 0.0 for key in keys}
    @property
    def failure_counts(self):
        result={}
        for item in self.results:
            if item.failure_stage is not None: result[item.failure_stage.value]=result.get(item.failure_stage.value,0)+1
        return result
    def to_dict(self): return {"mode":self.mode.value,"case_count":len(self.results),"passed_count":sum(x.passed for x in self.results),"failed_count":sum(not x.passed for x in self.results),"aggregate_metrics":self.aggregate_metrics,"failure_counts":self.failure_counts,"results":[x.to_dict() for x in self.results]}

def evaluate_case(case: RetrievalEvaluationCase, *, bm25:Sequence[RetrievalCandidate], vector:Sequence[RetrievalCandidate], rrf:Sequence[RetrievalCandidate], rerank:Sequence[RetrievalCandidate], evidence:Sequence[EvidenceItem]=(), evidence_source_cells:Mapping[str,Sequence[str]]={}, evidence_provenance:Mapping[str,Mapping[str,object]]={}, multi_table:Optional[MultiTableRetrievalResult]=None, completeness:Optional[EvidenceCompletenessResult]=None, k:int=10) -> RetrievalEvaluationCaseResult:
    required=case.required_evidence; table=[x for x in required if x.source_type is EvidenceSource.TABLE]; text=[x for x in required if x.source_type is EvidenceSource.TEXT]
    bm,ve,rf,rr=(recall_at_k(stage,required,k) for stage in (bm25,vector,rrf,rerank))
    table_recall=recall_at_k(rerank,table,k) if table else 1.0; text_recall=recall_at_k(rerank,text,k) if text else 1.0
    def item_matches(item, expected):
        provenance=evidence_provenance.get(item.evidence_id,{})
        return item.source_type is expected.source_type and (expected.report_id is None or item.report_ref==expected.report_id) and (expected.table_id is None or item.table_ref==expected.table_id) and (expected.paragraph_id is None or provenance.get("paragraph_id")==expected.paragraph_id) and (not expected.source_cell_ids or set(expected.source_cell_ids).issubset(set(evidence_source_cells.get(item.evidence_id,provenance.get("source_cell_ids",())))))
    grounded=all(any(item_matches(item, expected) for item in evidence) for expected in required)
    hybrid = completeness.complete if completeness is not None else not (table and text)
    if multi_table is None: multi=1.0
    else:
        required_count=len({result.subquery_id for result in multi_table.subquery_results} | set(multi_table.missing_subquery_ids))
        multi=sum(bool(result.candidates) and result.failure_code is None for result in multi_table.subquery_results)/required_count if required_count else 1.0
    outcomes=((RetrievalFailureStage.BM25,bm==1.0 or ve==1.0),(RetrievalFailureStage.RRF,rf==1.0),(RetrievalFailureStage.RERANKER,rr==1.0),(RetrievalFailureStage.MULTI_TABLE,multi==1.0),(RetrievalFailureStage.EVIDENCE_BUILD,grounded),(RetrievalFailureStage.EVIDENCE_COMPLETENESS,hybrid))
    failure=attribute_earliest_failure(outcomes); return RetrievalEvaluationCaseResult(case.case_id,failure is None,failure,bm,ve,rf,rr,mrr(rerank,required),multi,grounded,hybrid,table_recall,text_recall)

def evaluate_cases(mode:RetrievalEvaluationMode, cases:Sequence[tuple[RetrievalEvaluationCase,dict]]) -> RetrievalEvaluationReport:
    if not isinstance(mode,RetrievalEvaluationMode): mode=RetrievalEvaluationMode(mode)
    return RetrievalEvaluationReport(mode,tuple(evaluate_case(case,**inputs) for case,inputs in cases))
