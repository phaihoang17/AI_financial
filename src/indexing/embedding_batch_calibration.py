"""BGE-M3 batch-size calibration for a single GPU (ADR-057).

Purpose: choose ``--batch-size`` for the streaming vector build from *evidence*,
not a guess. It runs the pinned BGE-M3 encoder over a bounded corpus sample at
several batch sizes and records throughput, peak VRAM, and OOM/failures.

Hard guarantees:

- It never calls ``build_streaming_embedding_artifact`` and never writes to any
  output root. Nothing is published or mutated.
- It reads at most ``--sample-size`` chunks (capped at
  ``MAX_CALIBRATION_SAMPLE``) from the committed corpus artifact.
- Vectors produced during calibration are discarded.

Recommendation rule: pick the largest batch size that ran without OOM and whose
throughput (chunks/sec) is at least ``--min-speedup`` times the previous
smaller stable batch. If a larger batch is not meaningfully faster, the smaller
one wins.
"""

from __future__ import annotations

import argparse
from itertools import islice
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.indexing.embedding_artifact_builder import (
    APPROVED_EMBEDDING_FINGERPRINT,
    _iter_input_records,
    _validate_input_manifest,
)
from src.indexing.embedding_indexer import embed_chunks
from src.indexing.embedding_schemas import EmbeddingChunk


CALIBRATION_SCHEMA_VERSION = "m2b-batch-calibration-v1"
MAX_CALIBRATION_SAMPLE = 20_000
DEFAULT_BATCH_SIZES = (8, 16, 32, 64)
DEFAULT_MIN_SPEEDUP = 1.05


def load_calibration_sample(
    input_artifact: str | Path, sample_size: int
) -> List[EmbeddingChunk]:
    """Read the first ``sample_size`` chunks (bounded) from the corpus artifact."""
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size < 1:
        raise ValueError("sample_size must be a positive integer")
    bounded = min(sample_size, MAX_CALIBRATION_SAMPLE)
    input_data = _validate_input_manifest(input_artifact, verify_hashes=False)
    return [record.chunk for record in islice(_iter_input_records(input_data), bounded)]


def _peak_vram_bytes(device: str) -> Optional[int]:
    if not device.startswith("cuda"):
        return None
    try:  # pragma: no cover - requires CUDA
        import torch

        return int(torch.cuda.max_memory_allocated())
    except Exception:  # pragma: no cover - no CUDA / no torch
        return None


def _reset_peak_vram(device: str) -> None:
    if not device.startswith("cuda"):
        return
    try:  # pragma: no cover - requires CUDA
        import torch

        torch.cuda.reset_peak_memory_stats()
        torch.cuda.empty_cache()
    except Exception:  # pragma: no cover
        pass


def calibrate_batch_sizes(
    sample: Sequence[EmbeddingChunk],
    encoder: Any,
    batch_sizes: Sequence[int] = DEFAULT_BATCH_SIZES,
    *,
    device: str = "cuda",
) -> List[Dict[str, Any]]:
    """Time the pinned embedding path over ``sample`` for each batch size."""
    if not sample:
        raise ValueError("sample must be non-empty")
    chunks = list(sample)
    results: List[Dict[str, Any]] = []
    for batch_size in batch_sizes:
        _reset_peak_vram(device)
        started = time.monotonic()
        error: Optional[str] = None
        produced = 0
        try:
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start:start + batch_size]
                records = embed_chunks(
                    batch,
                    encoder=encoder,
                    model_fingerprint=APPROVED_EMBEDDING_FINGERPRINT,
                    batch_size=batch_size,
                    device=device,
                )
                produced += len(records)
        except Exception as exc:  # OOM or any encode failure at this batch size
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        ok = error is None and produced == len(chunks)
        results.append(
            {
                "batch_size": batch_size,
                "ok": ok,
                "chunks": produced,
                "seconds": round(elapsed, 4),
                "chunks_per_sec": round(produced / elapsed, 2) if ok and elapsed > 0 else None,
                "peak_vram_bytes": _peak_vram_bytes(device),
                "error": error,
            }
        )
    return results


def recommend_batch_size(
    results: Sequence[Dict[str, Any]], *, min_speedup: float = DEFAULT_MIN_SPEEDUP
) -> Dict[str, Any]:
    """Largest stable batch that is at least ``min_speedup`` faster than the prior."""
    ordered = sorted((r for r in results if r.get("ok")), key=lambda r: r["batch_size"])
    if not ordered:
        return {"recommended_batch_size": None, "reason": "no batch size ran to completion"}
    chosen = ordered[0]
    for candidate in ordered[1:]:
        prev = chosen["chunks_per_sec"] or 0.0
        cur = candidate["chunks_per_sec"] or 0.0
        if prev > 0 and cur >= prev * min_speedup:
            chosen = candidate
        # else: larger batch is not meaningfully faster; keep the smaller one.
    return {
        "recommended_batch_size": chosen["batch_size"],
        "chunks_per_sec": chosen["chunks_per_sec"],
        "peak_vram_bytes": chosen["peak_vram_bytes"],
        "reason": "largest stable batch with >= min_speedup throughput gain",
        "min_speedup": min_speedup,
    }


def _load_pinned_encoder(device: str) -> Any:  # pragma: no cover - requires CUDA + weights
    from src.indexing.embedding_indexer import load_bge_m3_encoder

    return load_bge_m3_encoder(device=device)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-artifact", required=True)
    parser.add_argument("--sample-size", type=int, default=2000)
    parser.add_argument("--batch-sizes", default="8,16,32,64")
    parser.add_argument("--device", default="cuda", choices=("cpu", "cuda", "mps"))
    parser.add_argument("--min-speedup", type=float, default=DEFAULT_MIN_SPEEDUP)
    args = parser.parse_args(argv)

    batch_sizes = tuple(int(value) for value in args.batch_sizes.split(",") if value.strip())
    sample = load_calibration_sample(args.input_artifact, args.sample_size)
    encoder = _load_pinned_encoder(args.device)  # pragma: no cover - live GPU
    results = calibrate_batch_sizes(sample, encoder, batch_sizes, device=args.device)
    recommendation = recommend_batch_size(results, min_speedup=args.min_speedup)
    print(json.dumps(
        {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "sample_size": len(sample),
            "device": args.device,
            "results": results,
            "recommendation": recommendation,
            "published_or_mutated_artifact": False,
        },
        ensure_ascii=False,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
