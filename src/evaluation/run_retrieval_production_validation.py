"""CLI: full-corpus retrieval production validation (TASK-109 / ADR-057).

This is the ONLY command that can produce evidence toward clearing
``GPU_PRODUCTION_VALIDATION_PENDING`` for retrieval. It is not CPU regression:
it must be run on the production GPU host against the real committed artifacts
and the real served models.

    python -m src.evaluation.run_retrieval_production_validation \
        --corpus-artifact  <committed m2 corpus artifact dir> \
        --vector-artifact  <the exact --output-root passed to the builder> \
        --bm25-artifact    <committed m3-bm25-index-v1 dir> \
        --serving-config    deploy/serving/retrieval-endpoints.json \
        --retrieval-smoke   smoke-result.json \
        [--gold-annotations <gold jsonl>] \
        [--operator-expected-vector-count 1743311]

``--vector-artifact`` is the builder's ``--output-root`` verbatim (the family
root that holds ``manifest.json`` + ``CURRENT`` + ``artifacts/<build_id>/``),
NOT the inner ``artifacts/<build_id>`` directory.

``--retrieval-smoke`` is the JSON emitted by
``python -m src.evaluation.run_retrieval_execution_smoke`` (keys ``ran`` /
``byte_identical`` / ``crashes`` / ``timeouts`` / ``sample_size``).

Without ``--serving-config`` the two LIVE probes are ``SKIPPED``; without
``--retrieval-smoke`` the deterministic retrieval-execution check is
``SKIPPED``. Either way the run cannot clear the flag
(``clears_gpu_pending`` stays false). Exit code is 0 only on a full
``overall_status == PASS``.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Optional, Sequence

from src.evaluation.retrieval_production_validation import (
    EXPECTED_CANONICAL_CHUNK_COUNT,
    GPU_PENDING_FLAG,
    ProductionValidationError,
    run_full_corpus_retrieval_validation,
)


def _load_serving_probes(config_path: str, sample_path: Optional[str]) -> dict[str, Any]:  # pragma: no cover - live infra
    """Build HttpTransport-backed clients, compute pinned local references, probe.

    Requires a live vLLM deployment and the pinned BGE-M3 / BGE-reranker weights
    on CPU for the parity reference. Never exercised in CPU regression.
    """
    from src.indexing.embedding_indexer import load_bge_m3_encoder
    from src.retrieval.reranker import load_bge_reranker
    from src.serving.client import HttpTransport, OpenAICompatibleClient
    from src.serving.live_probe import probe_embedding_endpoint, probe_reranker_endpoint
    from src.serving.schemas import ServingEndpoint

    config = json.loads(open(config_path, encoding="utf-8").read())
    embed_ep = ServingEndpoint.from_dict(config["embedding"])
    rerank_ep = ServingEndpoint.from_dict(config["reranker"])
    transport = HttpTransport()
    embed_client = OpenAICompatibleClient(embed_ep, transport)
    rerank_client = OpenAICompatibleClient(rerank_ep, transport)

    sample = json.loads(open(sample_path, encoding="utf-8").read()) if sample_path else config.get("sample", {})
    texts: Sequence[str] = sample["embedding_texts"]
    query: str = sample["reranker_query"]
    docs: Sequence[str] = sample["reranker_documents"]

    encoder = load_bge_m3_encoder(device="cpu")
    local_vectors = encoder.encode(list(texts), batch_size=len(texts), max_length=8192)
    reranker = load_bge_reranker(device="cpu")
    local_scores = reranker.score_pairs(query, list(docs), batch_size=len(docs))

    return {
        "embedding_probe_result": probe_embedding_endpoint(
            embed_client, sample_texts=texts, local_vectors=local_vectors
        ),
        "reranker_probe_result": probe_reranker_endpoint(
            rerank_client, sample_query=query, sample_documents=docs, local_scores=local_scores
        ),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--vector-artifact", required=True)
    parser.add_argument("--bm25-artifact", required=True)
    parser.add_argument("--serving-config", default=None)
    parser.add_argument("--serving-sample", default=None)
    parser.add_argument(
        "--retrieval-smoke",
        default=None,
        help="path to run_retrieval_execution_smoke JSON output",
    )
    parser.add_argument("--gold-annotations", default=None)
    parser.add_argument(
        "--operator-expected-vector-count",
        type=int,
        default=None,
        help="acceptance vector count; must equal the canonical chunk count (never the 146,246 table count)",
    )
    parser.add_argument(
        "--expected-canonical-chunk-count",
        type=int,
        default=EXPECTED_CANONICAL_CHUNK_COUNT,
    )
    args = parser.parse_args(argv)

    probes: dict[str, Any] = {}
    if args.serving_config is not None:
        probes = _load_serving_probes(args.serving_config, args.serving_sample)  # pragma: no cover - live infra

    retrieval_smoke = None
    if args.retrieval_smoke is not None:
        try:
            retrieval_smoke = json.loads(
                open(args.retrieval_smoke, encoding="utf-8").read()
            )
        except (OSError, ValueError) as error:
            print(json.dumps(
                {
                    "overall_status": "FAIL",
                    "code": "RETRIEVAL_SMOKE_UNREADABLE",
                    "message": str(error),
                    "clears_gpu_pending": False,
                    "gpu_pending_flag": GPU_PENDING_FLAG,
                },
                sort_keys=True,
            ))
            return 1

    try:
        report = run_full_corpus_retrieval_validation(
            args.corpus_artifact,
            args.vector_artifact,
            args.bm25_artifact,
            gold_annotations_path=args.gold_annotations,
            expected_canonical_chunk_count=args.expected_canonical_chunk_count,
            operator_expected_vector_count=args.operator_expected_vector_count,
            retrieval_smoke=retrieval_smoke,
            **probes,
        )
    except ProductionValidationError as error:
        print(json.dumps(
            {
                "overall_status": "FAIL",
                "code": error.code,
                "message": str(error),
                "clears_gpu_pending": False,
                "gpu_pending_flag": GPU_PENDING_FLAG,
            },
            ensure_ascii=False,
            sort_keys=True,
        ))
        return 1
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.overall_status == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
