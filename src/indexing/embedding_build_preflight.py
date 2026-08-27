"""Preflight for the streaming BGE-M3 vector build (ADR-057).

Read-only, CPU-only, no model load. It resolves the committed corpus artifact,
computes the deterministic ``build_id`` for the chosen ``--max-vectors-per-shard``,
and inspects ``--output-root`` to decide exactly one of:

- ``ALREADY_PUBLISHED`` — ``artifacts/<build_id>/`` exists; re-running the
  builder only re-publishes the ``CURRENT`` pointer.
- ``RESUME``           — ``.staging/<build_id>/checkpoint.json`` exists and its
  corpus / chunking / embedding fingerprints match. Use ``--resume``.
- ``FRESH``            — no compatible artifact or staging checkpoint. Run
  WITHOUT ``--resume``. (An incompatible ``.staging/<build_id>`` blocks a fresh
  run with ``STAGING_EXISTS`` and must be removed deliberately.)

Exit status: 0 on a clean decision, 1 on an input/compat error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from src.indexing.embedding_artifact_builder import (
    APPROVED_EMBEDDING_FINGERPRINT,
    MAX_VECTORS_PER_SHARD,
    EmbeddingArtifactBuilderError,
    _build_id,
    _validate_input_manifest,
)
from src.indexing.embedding_chunker import CHUNKING_CONFIG_FINGERPRINT


PREFLIGHT_SCHEMA_VERSION = "m2b-vector-build-preflight-v1"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def preflight_vector_build(
    input_artifact: str | Path,
    output_root: str | Path,
    *,
    max_vectors_per_shard: int = MAX_VECTORS_PER_SHARD,
) -> Dict[str, Any]:
    """Decide FRESH / RESUME / ALREADY_PUBLISHED without loading a model."""
    input_data = _validate_input_manifest(input_artifact, verify_hashes=False)
    build_id = _build_id(input_data, max_vectors_per_shard)
    root = Path(output_root)

    published = root / "artifacts" / build_id / "manifest.json"
    staging = root / ".staging" / build_id
    checkpoint_path = staging / "checkpoint.json"

    result: Dict[str, Any] = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "build_id": build_id,
        "input_artifact_id": input_data.artifact_id,
        "corpus_id": input_data.corpus_id,
        "corpus_fingerprint": input_data.corpus_fingerprint,
        "chunk_count": input_data.chunk_count,
        "chunking_config_fingerprint": CHUNKING_CONFIG_FINGERPRINT,
        "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
        "max_vectors_per_shard": max_vectors_per_shard,
    }

    if published.is_file():
        result["mode"] = "ALREADY_PUBLISHED"
        result["use_resume_flag"] = False
        result["reason"] = "artifacts/<build_id>/manifest.json already exists"
        return result

    checkpoint = _read_json(checkpoint_path) if checkpoint_path.is_file() else None
    if checkpoint is not None:
        matches = (
            checkpoint.get("input_artifact_id") == input_data.artifact_id
            and checkpoint.get("input_manifest_sha256") == input_data.manifest_sha256
            and checkpoint.get("corpus_fingerprint") == input_data.corpus_fingerprint
            and checkpoint.get("chunking_config_fingerprint") == CHUNKING_CONFIG_FINGERPRINT
            and checkpoint.get("embedding_fingerprint") == APPROVED_EMBEDDING_FINGERPRINT
        )
        if matches:
            result["mode"] = "RESUME"
            result["use_resume_flag"] = True
            result["reason"] = "compatible .staging/<build_id>/checkpoint.json found"
            result["completed_shards"] = len(checkpoint.get("completed_shards", []) or [])
            return result
        result["mode"] = "FRESH"
        result["use_resume_flag"] = False
        result["staging_conflict"] = True
        result["reason"] = (
            ".staging/<build_id>/checkpoint.json exists but fingerprints differ; "
            "remove it deliberately before a fresh build"
        )
        return result

    if staging.exists():
        result["mode"] = "FRESH"
        result["use_resume_flag"] = False
        result["staging_conflict"] = True
        result["reason"] = (
            ".staging/<build_id> exists without a checkpoint; the builder will "
            "reject a fresh run with STAGING_EXISTS until it is removed"
        )
        return result

    result["mode"] = "FRESH"
    result["use_resume_flag"] = False
    result["staging_conflict"] = False
    result["reason"] = "no published artifact and no staging checkpoint"
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-artifact", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--max-vectors-per-shard", type=int, default=MAX_VECTORS_PER_SHARD
    )
    args = parser.parse_args(argv)
    try:
        result = preflight_vector_build(
            args.input_artifact,
            args.output_root,
            max_vectors_per_shard=args.max_vectors_per_shard,
        )
    except EmbeddingArtifactBuilderError as error:
        print(json.dumps({"error": error.code, "message": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
