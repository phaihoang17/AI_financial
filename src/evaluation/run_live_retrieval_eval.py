"""Evaluate real retrieval with annotated query metadata, without NLU or E2E.

Models run sequentially to bound memory. Backend depth, fusion and reranking
reuse the HybridRetriever policy; gold answers and evidence IDs are used only to score.
"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import sys
import time

from src.evaluation.gold_retrieval import (
    GoldRetrievalCase,
    compute_gold_retrieval_metrics,
    load_gold_file,
)
from src.indexing.embedding_indexer import load_bge_m3_encoder, make_embedding_config_fingerprint
from src.retrieval.bm25 import BM25Index
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.query_embedding import embed_query_texts
from src.retrieval.reranker import load_bge_reranker, make_reranker_config_fingerprint, rerank_candidates
from src.retrieval.rrf import fuse_rrf
from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
from src.retrieval.vector_search import VectorSearcher
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import PeriodKind, StatementScope


def annotated_query(case: GoldRetrievalCase) -> RetrievalQuery:
    """Use only semantic query annotations, never expected evidence or values."""
    return RetrievalQuery(
        raw_question=case.question,
        company=RetrievalCompany(case.company_name or case.company_ticker, case.company_ticker),
        periods=[case.period] if case.period else [],
        period_kind=PeriodKind(case.period_kind) if case.period_kind else None,
        statement_scope=StatementScope(case.statement_scope) if case.statement_scope else None,
        target_metrics=[case.metric] if case.metric else [],
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE, EvidenceSource.TEXT],
        requested_scale=None,
        requested_unit=None,
        query_texts=list(dict.fromkeys([case.question, *([case.metric] if case.metric else [])])),
        eligible_source_types=[EvidenceSource.TABLE, EvidenceSource.TEXT],
    )


def run(cases, *, corpus_root, vector_root, bm25_root, top_k=10):
    if not cases or len({case.question_id for case in cases}) != len(cases):
        raise ValueError("gold must be nonempty with unique question IDs")
    depth = HybridRetriever._candidate_depth(top_k, None)
    started = time.monotonic()
    queries = {case.question_id: annotated_query(case) for case in cases if case.is_gold_bearing()}
    if not queries:
        raise ValueError("gold contains no evidence-bearing annotated cases")
    stages = {name: {} for name in ("bm25", "vector", "rrf", "rerank")}
    errors = []
    timings = {}

    def fail(case_id, stage, error):
        errors.append({"question_id": case_id, "stage": stage,
                       "error": f"{type(error).__name__}: {error}"})

    print("Validating BM25 and searching", file=sys.stderr, flush=True)
    bm25 = BM25Index(bm25_root, corpus_root)
    for case_id, query in queries.items():
        start = time.monotonic()
        try:
            stages["bm25"][case_id] = bm25.search(
                query, top_k=depth, eligible_source_types=query.evidence_sources,
            )
        except Exception as error:
            fail(case_id, "bm25", error)
        timings[case_id] = {"bm25_s": time.monotonic() - start}

    print("Encoding queries with pinned BGE-M3 (CPU, batch 1)", file=sys.stderr, flush=True)
    encoder = load_bge_m3_encoder(device="cpu")
    embeddings = {}
    for case_id, query in queries.items():
        start = time.monotonic()
        try:
            embeddings[case_id] = embed_query_texts(query, encoder=encoder, batch_size=1)
        except Exception as error:
            fail(case_id, "embedding", error)
        timings[case_id]["embedding_s"] = time.monotonic() - start
    del encoder
    gc.collect()

    print("Validating FAISS/SQLite and searching", file=sys.stderr, flush=True)
    vector = VectorSearcher(vector_root, corpus_root)
    for case_id, query in queries.items():
        if case_id not in embeddings or case_id not in stages["bm25"]:
            continue
        start = time.monotonic()
        try:
            stages["vector"][case_id] = vector.search(
                query, embeddings[case_id], top_k=depth,
                eligible_source_types=query.evidence_sources,
            )
            stages["rrf"][case_id] = fuse_rrf(
                stages["bm25"][case_id], stages["vector"][case_id], top_k=depth,
            )
        except Exception as error:
            fail(case_id, "vector_rrf", error)
        timings[case_id]["vector_rrf_s"] = time.monotonic() - start
        print(f"Vector {case_id}: {timings[case_id]['vector_rrf_s']:.1f}s",
              file=sys.stderr, flush=True)
    vector_fingerprint = vector.artifact_fingerprint
    del vector, embeddings
    gc.collect()

    print("Reranking with pinned BGE-reranker-v2-m3 (CPU, batch 1)", file=sys.stderr, flush=True)
    reranker = load_bge_reranker(device="cpu")
    for case_id, query in queries.items():
        if case_id not in stages["rrf"]:
            continue
        start = time.monotonic()
        try:
            stages["rerank"][case_id] = rerank_candidates(
                query, stages["rrf"][case_id], reranker=reranker,
                batch_size=1, top_k=top_k,
            )
        except Exception as error:
            fail(case_id, "rerank", error)
        timings[case_id]["rerank_s"] = time.monotonic() - start
        print(f"Rerank {case_id}: {timings[case_id]['rerank_s']:.1f}s",
              file=sys.stderr, flush=True)

    return {
        "schema_version": "m10-live-retrieval-evaluation-v1",
        "status": "PASS" if not errors else "COMPLETED_WITH_ERRORS",
        "evidence_class": "LIVE",
        "evaluation_scope": "RETRIEVAL_WITH_ANNOTATED_QUERY_METADATA",
        "is_e2e": False,
        "establishes_task100_baseline": False,
        "device": "cpu", "batch_size": 1, "top_k": top_k,
        "candidate_depth": depth,
        "seconds": time.monotonic() - started,
        "artifact_roots": {"corpus": str(corpus_root), "vector": str(vector_root), "bm25": str(bm25_root)},
        "bm25_artifact_fingerprint": bm25.artifact_fingerprint,
        "vector_artifact_fingerprint": vector_fingerprint,
        "embedding_fingerprint": make_embedding_config_fingerprint(),
        "reranker_fingerprint": make_reranker_config_fingerprint(),
        "errors": errors, "timings": timings,
        "metrics_by_stage": {
            name: compute_gold_retrieval_metrics(
                cases, lambda case, found=values: found.get(case.question_id, []),
            ).to_dict()
            for name, values in stages.items()
        },
        "candidates_by_stage": {
            name: {case_id: [candidate.to_dict() for candidate in values]
                   for case_id, values in results.items()}
            for name, results in stages.items()
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--vector-artifact", required=True)
    parser.add_argument("--bm25-artifact", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        report = run(load_gold_file(args.gold), corpus_root=args.corpus_artifact,
                     vector_root=args.vector_artifact, bm25_root=args.bm25_artifact,
                     top_k=args.top_k)
    except Exception as error:
        report = {"status": "FAILED", "is_e2e": False,
                  "establishes_task100_baseline": False,
                  "error": f"{type(error).__name__}: {error}"}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)
    print(json.dumps({"status": report["status"], "output": str(output)}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
