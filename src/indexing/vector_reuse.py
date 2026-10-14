"""ADR-063: reuse earlier vectors for byte-identical chunk content."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from src.indexing.embedding_artifact_builder import (
    APPROVED_EMBEDDING_FINGERPRINT,
    EmbeddingArtifactBuilderError,
    _sha256_file,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EmbeddingArtifactBuilderError("REUSE_SOURCE_INVALID", message)


class ContentReuseEncoder:
    """Serve vectors an earlier committed artifact computed for the same content.

    `embed_chunks` embeds only `chunk.content`, so under the same approved
    fingerprint a vector of byte-identical content is the same vector. Only
    unseen content reaches the wrapped encoder, one text at a time: long table
    chunks padded into one batch exhaust an 8 GiB host.
    """

    def __init__(self, vector_artifact: str | Path, corpus_artifact: str | Path, encoder: Any) -> None:
        import faiss

        vector_dir, corpus_dir = Path(vector_artifact), Path(corpus_artifact)
        manifest = json.loads((vector_dir / "manifest.json").read_text(encoding="utf-8"))
        _require(manifest.get("artifact_status") == "COMMITTED", "reuse source is not committed")
        _require(manifest.get("embedding_fingerprint") == APPROVED_EMBEDDING_FINGERPRINT, "embedding fingerprint differs")
        corpus_manifest_path = corpus_dir / "manifest.json"
        _require(_sha256_file(corpus_manifest_path) == manifest.get("input_manifest_sha256"), "corpus is not the reuse source's input")
        metadata = vector_dir / manifest["metadata"]["file"]
        _require(_sha256_file(metadata) == manifest["metadata"]["sha256"], "reuse metadata hash differs")
        self._shards = []
        for shard in manifest["shards"]:
            path = vector_dir / shard["file"]
            _require(_sha256_file(path) == shard["sha256"], f"{shard['file']} hash differs")
            self._shards.append(faiss.read_index(str(path), faiss.IO_FLAG_MMAP | faiss.IO_FLAG_READ_ONLY))

        corpus_manifest = json.loads(corpus_manifest_path.read_text(encoding="utf-8"))
        self._locations: dict[bytes, tuple[int, int]] = {}
        connection = sqlite3.connect(f"file:{metadata}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT source_shard, source_record_index, faiss_shard_number, local_vector_id"
                " FROM embeddings ORDER BY source_order"
            )
            count = 0
            for shard in corpus_manifest["shards"]:
                path = corpus_dir / shard["file"]
                _require(_sha256_file(path) == shard["sha256"], f"{shard['file']} hash differs")
                with path.open("rb") as handle:
                    for index, line in enumerate(handle):
                        row = next(rows, None)
                        _require(row is not None and row[:2] == (shard["file"], index), "vector order differs from corpus order")
                        content = json.loads(line)["chunk"]["content"]
                        self._locations.setdefault(sha256(content.encode("utf-8")).digest(), (row[2], row[3]))
                        count += 1
            _require(next(rows, None) is None and count == manifest["vector_count"], "vector count differs from corpus")
        finally:
            connection.close()
        self._encoder = encoder
        self.reused = 0
        self.encoded = 0

    def encode(self, texts: Sequence[str], *, batch_size: int, max_length: int) -> list[Any]:
        vectors: list[Any] = []
        for text in texts:
            location = self._locations.get(sha256(text.encode("utf-8")).digest())
            if location is not None:
                vectors.append(self._shards[location[0]].reconstruct(location[1]))
                self.reused += 1
                continue
            vectors.append(self._encoder.encode([text], batch_size=1, max_length=max_length)[0])
            self.encoded += 1
            if self.encoded % 100 == 0:
                print(f"reuse: {self.reused} reused, {self.encoded} encoded", file=sys.stderr, flush=True)
        return vectors
