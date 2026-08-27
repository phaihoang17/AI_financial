"""Run the deterministic, CPU-only M3 retrieval fixture evaluation."""
from __future__ import annotations
import argparse
import json
from dataclasses import replace

from src.evaluation.retrieval import RequiredEvidence, RetrievalEvaluationCase, RetrievalEvaluationMode, evaluate_cases
from src.evidence.schemas import EvidenceItem
from src.retrieval.evidence import EvidenceCompletenessResult
from src.retrieval.schemas import MultiTableRetrievalResult, SubqueryRetrievalResult
from src.retrieval.schemas import RetrievalCandidate, make_candidate_id
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import StatementScope


def _candidate(name: str, source: EvidenceSource) -> RetrievalCandidate:
    representation_id, chunk_id = f"fixture-representation-{name}", f"fixture-chunk-{name}"
    return RetrievalCandidate(make_candidate_id(source,representation_id,chunk_id),representation_id,chunk_id,source,"fixture-report-aaa-2024",["fixture-page-1"],f"fixture-table-{name}" if source is EvidenceSource.TABLE else None,f"fixture-paragraph-{name}" if source is EvidenceSource.TEXT else None,"AAA","Fixture Company",2024,StatementScope.HOP_NHAT,["2024"],[],[],f"fixture {name}",1.0,0.9,0.03,0.8,1)

def _item(candidate: RetrievalCandidate, *, cell: str = "fixture-cell-1") -> EvidenceItem:
    return EvidenceItem(f"fixture-evidence-{candidate.candidate_id[:12]}",candidate.source_type,candidate.report_id,"fixture-page-1",candidate.table_id,None,candidate.statement_scope,"2024","Doanh thu",None,None,[],[],"fixture text" if candidate.source_type is EvidenceSource.TEXT else None,"1250" if candidate.source_type is EvidenceSource.TABLE else None,"1250" if candidate.source_type is EvidenceSource.TABLE else None,None,None,None,0.03,0.8)

def fixture_cases():
    """Twelve deterministic cases whose expected IDs are actual fixture IDs."""
    table, text = _candidate("revenue",EvidenceSource.TABLE), _candidate("narrative",EvidenceSource.TEXT)
    expected_table=RequiredEvidence(EvidenceSource.TABLE,table_id=table.table_id,source_cell_ids=("fixture-cell-1",),metric="Doanh thu",period="2024",report_id=table.report_id)
    expected_text=RequiredEvidence(EvidenceSource.TEXT,paragraph_id=text.paragraph_id,report_id=text.report_id)
    labels=("direct-table","multi-period","multi-metric","text-narrative","hybrid","scale-unit","metadata-filter-miss","lexical-only","dense-only","reranker-drop","incomplete-multi-table","provenance-failure")
    result=[]
    for label in labels:
        required=(expected_text,) if label=="text-narrative" else (expected_table,expected_text) if label=="hybrid" else (expected_table,)
        case=RetrievalEvaluationCase(f"fixture-{label}",1,"fixture question","AAA",("2024",),StatementScope.HOP_NHAT,required)
        table_item=_item(table); text_item=_item(text)
        stages={"bm25":[table] if label!="dense-only" else [],"vector":[table] if label!="lexical-only" else [],"rrf":[table],"rerank":[table],"evidence":[table_item],"evidence_source_cells":{table_item.evidence_id:("fixture-cell-1",)},"evidence_provenance":{table_item.evidence_id:{"source_cell_ids":("fixture-cell-1",)}}}
        if label=="text-narrative": stages.update({key:[text] for key in ("bm25","vector","rrf","rerank")}); stages["evidence"]=[text_item]; stages["evidence_source_cells"]={}; stages["evidence_provenance"]={text_item.evidence_id:{"paragraph_id":text.paragraph_id}}
        if label=="hybrid": stages.update({key:[table,text] for key in ("bm25","vector","rrf","rerank")}); stages["evidence"]=[table_item,text_item]; stages["evidence_provenance"][text_item.evidence_id]={"paragraph_id":text.paragraph_id}
        if label=="hybrid": stages["completeness"]=EvidenceCompletenessResult(True,[],[])
        if label=="metadata-filter-miss": stages.update({key:[] for key in ("bm25","vector","rrf","rerank")})
        if label=="reranker-drop": stages["rerank"]=[]
        if label=="incomplete-multi-table": stages["multi_table"]=MultiTableRetrievalResult("a"*64,[SubqueryRetrievalResult("b"*64,[table],None)],False,["c"*64])
        if label=="provenance-failure": stages["evidence_source_cells"]={}; stages["evidence_provenance"]={}
        result.append((case,stages))
    return result

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--mode",choices=("fixture","full-corpus"),default="fixture"); args=parser.parse_args(argv)
    if args.mode == "full-corpus":
        # This CLI is CPU regression only. Real full-corpus retrieval production
        # validation (artifact/serving correctness + LIVE model probes) lives in
        # a dedicated runner and cannot be produced here. Exit non-zero so this
        # branch is never mistaken for a passing production gate.
        print(json.dumps({
            "mode":"FULL_CORPUS",
            "status":"NOT_RUN_HERE",
            "evidence_class":"NONE",
            "blocker":"GPU_PRODUCTION_VALIDATION_PENDING",
            "note":"fixture mode is CPU regression only; it proves no production retrieval behavior",
            "use":"python -m src.evaluation.run_retrieval_production_validation --corpus-artifact <m2 corpus dir> --vector-artifact <builder --output-root> --bm25-artifact <m3-bm25-index-v1 dir> --serving-config deploy/serving/retrieval-endpoints.json --retrieval-smoke smoke-result.json",
        },sort_keys=True))
        return 2
    print(json.dumps(evaluate_cases(RetrievalEvaluationMode.FIXTURE,fixture_cases()).to_dict(),ensure_ascii=False,sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
