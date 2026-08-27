"""Deterministic retrieval-execution smoke (TASK-109 / ADR-057).

Runs the *real* production retrieval stack (BM25 + FAISS + pinned BGE-M3 query
encoder + pinned BGE-reranker-v2-m3, via ``Batch3Retriever``) over a fixed set
of Qwen-free ``RetrievalQuery`` inputs, twice, and emits the typed smoke result
that ``run_retrieval_production_validation`` gates on:

    {"ran": bool, "byte_identical": bool, "crashes": int, "timeouts": int,
     "sample_size": int, ...}

It changes no retrieval semantics — it only calls ``Batch3Retriever.retrieve``.
It never computes Recall@k / MRR and invents no quality label.

Queries file: JSONL, one ``RetrievalQuery`` dict per line (the exact
``RetrievalQuery.to_dict()`` shape). These are authored by hand or from a Plan;
no NLU/LLM is involved.

    python -m src.evaluation.run_retrieval_execution_smoke \
        --corpus-artifact "$CORPUS" --vector-artifact "$OUT" --bm25-artifact "$BM25" \
        --queries deploy/retrieval/smoke-queries.jsonl --top-k 10 \
        > smoke-result.json

Exit 0 iff ran and byte_identical and crashes == 0 and timeouts == 0.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.retrieval.schemas import RetrievalQuery


RETRIEVAL_EXECUTION_SMOKE_SCHEMA_VERSION = "m10-retrieval-execution-smoke-v1"
DEFAULT_TOP_K = 10
DEFAULT_PER_QUERY_TIMEOUT_S = 30.0


class RetrievalSmokeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def load_smoke_queries(path: str | Path) -> List[RetrievalQuery]:
    """Load a JSONL file of ``RetrievalQuery.to_dict()`` objects."""
    text = Path(path).read_text(encoding="utf-8")
    queries: List[RetrievalQuery] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            queries.append(RetrievalQuery.from_dict(json.loads(line)))
        except Exception as error:
            raise RetrievalSmokeError(
                "SMOKE_QUERIES_INVALID", f"line {line_number}: {error}"
            ) from error
    if not queries:
        raise RetrievalSmokeError("SMOKE_QUERIES_EMPTY", "no RetrievalQuery lines found")
    return queries


def run_retrieval_execution_smoke(
    retriever: Any,
    queries: Sequence[RetrievalQuery],
    *,
    top_k: int = DEFAULT_TOP_K,
    per_query_timeout_s: float = DEFAULT_PER_QUERY_TIMEOUT_S,
) -> Dict[str, Any]:
    """Run each query twice through ``retriever.retrieve`` and compare results."""
    if not queries:
        raise RetrievalSmokeError("SMOKE_QUERIES_EMPTY", "queries must be non-empty")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise RetrievalSmokeError("SMOKE_TOP_K_INVALID", "top_k must be a positive integer")

    per_query: List[Dict[str, Any]] = []
    crashes = 0
    timeouts = 0
    all_identical = True

    for index, query in enumerate(queries):
        eligible = list(query.eligible_source_types or query.evidence_sources)
        record: Dict[str, Any] = {"index": index, "raw_question": query.raw_question}
        try:
            start_first = time.monotonic()
            first = retriever.retrieve(query, top_k=top_k, eligible_source_types=eligible)
            mid = time.monotonic()
            second = retriever.retrieve(query, top_k=top_k, eligible_source_types=eligible)
            end = time.monotonic()
        except Exception as error:  # any backend crash for this query
            crashes += 1
            all_identical = False
            record.update({"ok": False, "error": f"{type(error).__name__}: {error}"})
            per_query.append(record)
            continue

        first_dump = [candidate.to_dict() for candidate in first]
        second_dump = [candidate.to_dict() for candidate in second]
        identical = first_dump == second_dump
        all_identical = all_identical and identical
        slowest = max(mid - start_first, end - mid)
        timed_out = slowest > per_query_timeout_s
        if timed_out:
            timeouts += 1
        record.update(
            {
                "ok": identical and not timed_out,
                "candidates": len(first_dump),
                "identical": identical,
                "max_seconds": round(slowest, 4),
                "timed_out": timed_out,
            }
        )
        per_query.append(record)

    return {
        "schema_version": RETRIEVAL_EXECUTION_SMOKE_SCHEMA_VERSION,
        "ran": True,
        "byte_identical": bool(all_identical and crashes == 0),
        "crashes": crashes,
        "timeouts": timeouts,
        "sample_size": len(queries),
        "top_k": top_k,
        "per_query_timeout_s": per_query_timeout_s,
        "per_query": per_query,
    }


def _build_production_retriever(
    corpus_root: str, vector_root: str, bm25_root: str
) -> Any:  # pragma: no cover - needs real artifacts + pinned weights
    from src.indexing.embedding_indexer import load_bge_m3_encoder
    from src.retrieval.batch3 import Batch3Retriever
    from src.retrieval.bm25 import BM25Index
    from src.retrieval.query_embedding import embed_query_texts
    from src.retrieval.reranker import load_bge_reranker
    from src.retrieval.vector_search import VectorSearcher

    bm25 = BM25Index(bm25_root, corpus_root)
    vector = VectorSearcher(vector_root, corpus_root)
    encoder = load_bge_m3_encoder(device="cpu")
    reranker = load_bge_reranker(device="cpu")

    def query_embedder(query: RetrievalQuery) -> Any:
        return embed_query_texts(query, encoder=encoder)

    return Batch3Retriever(bm25, vector, query_embedder, reranker=reranker)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--vector-artifact", required=True)
    parser.add_argument("--bm25-artifact", required=True)
    parser.add_argument("--queries", required=True, help="JSONL of RetrievalQuery.to_dict() objects")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--per-query-timeout-s", type=float, default=DEFAULT_PER_QUERY_TIMEOUT_S
    )
    args = parser.parse_args(argv)

    try:
        queries = load_smoke_queries(args.queries)
        retriever = _build_production_retriever(  # pragma: no cover - live path
            args.corpus_artifact, args.vector_artifact, args.bm25_artifact
        )
        result = run_retrieval_execution_smoke(
            retriever,
            queries,
            top_k=args.top_k,
            per_query_timeout_s=args.per_query_timeout_s,
        )
    except RetrievalSmokeError as error:
        print(json.dumps({"error": error.code, "message": str(error)}, sort_keys=True))
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    ok = result["ran"] and result["byte_identical"] and result["crashes"] == 0 and result["timeouts"] == 0
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
