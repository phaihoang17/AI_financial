"""Deterministic real-data smoke subset of a committed M2 corpus artifact.

Selects the lowest chunk IDs per source type (chunk IDs are SHA-256 values) and
swaps in one table chunk at or above the table p95 content-token count, so the
smoke artifact exercises long-table encoding. Records are copied byte-for-byte
except `source_order`, which is renumbered to stay contiguous; the report
inventory is narrowed to the covered reports. The output is a regular committed
corpus artifact that the embedding builder and retrieval gates accept unchanged.
"""

from __future__ import annotations

import heapq
import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.indexing.embedding_artifact_builder import (
    _atomic_write_bytes,
    _atomic_write_json,
    _canonical_json,
    _sha256_json,
    _validate_input_manifest,
)

SELECTION_VERSION = "m2b-real-smoke-sample-v1"
SELECTION_METHOD = "lowest_sha256_chunk_id_per_source_type_with_p95_table_inclusion"


def _scan(root: Path, shard_entries: Sequence[Dict[str, Any]], per_type: int) -> Tuple[Dict[str, List[Tuple[str, int]]], List[int], Tuple[int, str, int]]:
    # Max-heaps (negated) keep the `per_type` lowest chunk IDs per source type.
    heaps: Dict[str, List[Tuple[str, int]]] = {"TABLE": [], "TEXT": []}
    table_tokens: List[int] = []
    longest: Tuple[int, str, int] = (-1, "", -1)
    for entry in shard_entries:
        with (root / str(entry["file"])).open("rb") as handle:
            for line in handle:
                record = json.loads(line)
                chunk = record["chunk"]
                source_type = chunk["source_type"]
                key = (_negate(chunk["chunk_id"]), record["source_order"])
                heap = heaps[source_type]
                if len(heap) < per_type:
                    heapq.heappush(heap, key)
                elif key > heap[0]:
                    heapq.heapreplace(heap, key)
                if source_type == "TABLE":
                    tokens = int(chunk["content_token_count"])
                    table_tokens.append(tokens)
                    if tokens > longest[0]:
                        longest = (tokens, chunk["chunk_id"], record["source_order"])
    selected = {name: sorted((_negate(cid), order) for cid, order in heap) for name, heap in heaps.items()}
    return selected, table_tokens, longest


def _negate(chunk_id: str) -> str:
    return "".join("%x" % (15 - int(char, 16)) for char in chunk_id)


def _p95(values: List[int]) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def build_smoke_subset(input_artifact: str | Path, output_root: str | Path, *, per_type: int = 250) -> Path:
    source = _validate_input_manifest(input_artifact, verify_hashes=True)
    manifest = source.manifest
    selected, table_tokens, longest = _scan(source.root, source.shard_entries, per_type)
    p95 = _p95(table_tokens)
    long_table: Optional[Tuple[str, int]] = None
    if not any(order == longest[2] for _, order in selected["TABLE"]) and longest[0] >= p95:
        # Replace the highest selected table ID with the longest table chunk.
        selected["TABLE"] = sorted(selected["TABLE"][:-1] + [(longest[1], longest[2])])
        long_table = (longest[1], longest[0])
    orders = sorted(order for items in selected.values() for _, order in items)
    wanted = set(orders)

    lines: List[bytes] = []
    covered: set = set()
    cells = 0
    for entry in source.shard_entries:
        with (source.root / str(entry["file"])).open("rb") as handle:
            for line in handle:
                record = json.loads(line)
                if record["source_order"] not in wanted:
                    continue
                record["source_order"] = len(lines)
                covered.add(record["report"]["report_id"])
                cells += len(record["source_cell_ids"])
                lines.append(_canonical_json(record) + b"\n")
    reports = [report for report in manifest["reports"] if report["report_id"] in covered]
    shard_bytes = b"".join(lines)
    table_count = len(selected["TABLE"])
    text_count = len(selected["TEXT"])
    selection = {
        "selection_version": SELECTION_VERSION,
        "source_artifact_id": manifest["artifact_id"],
        "source_corpus_fingerprint": manifest["source_inventory_sha256"],
        "method": SELECTION_METHOD,
        "table_chunk_count": table_count,
        "text_chunk_count": text_count,
        "table_p95_content_token_count": p95,
        "included_long_table_chunk_id": long_table[0] if long_table else None,
        "included_long_table_content_token_count": long_table[1] if long_table else None,
        "canonical_source_orders": orders,
    }
    inventory = _sha256_json(reports)
    artifact_id = sha256(_canonical_json({"source_inventory_sha256": inventory, "smoke_selection": selection}, sort_keys=True)).hexdigest()
    out = {
        **{key: manifest[key] for key in (
            "manifest_schema_version", "record_schema_version")},
        "artifact_id": artifact_id,
        "artifact_status": "COMMITTED",
        "corpus_id": manifest["corpus_id"],
        "source_inventory_sha256": inventory,
        "reports": reports,
        **{key: manifest[key] for key in (
            "source_order", "record_order", "m2a_schema_version", "normalization_version",
            "representation_version", "chunk_schema_version", "chunking_config_fingerprint",
            "tokenizer", "format")},
        "max_records_per_shard": len(lines),
        "max_shard_bytes": len(shard_bytes),
        "report_count": len(reports),
        "representation_count": len(lines),
        "table_representation_count": table_count,
        "text_representation_count": text_count,
        "chunk_count": len(lines),
        "table_chunk_count": table_count,
        "text_chunk_count": text_count,
        "source_cell_count": cells,
        "emitted_source_cell_count": cells,
        "shard_count": 1,
        "shards": [{
            "shard_id": "shard-000000",
            "file": "shards/shard-000000.jsonl",
            "record_count": len(lines),
            "table_chunk_count": table_count,
            "text_chunk_count": text_count,
            "byte_count": len(shard_bytes),
            "sha256": sha256(shard_bytes).hexdigest(),
        }],
        "omitted_representation_count": 0,
        "omitted_source_cell_count": 0,
        "smoke_selection": selection,
    }
    root = Path(output_root)
    _atomic_write_bytes(root / "shards" / "shard-000000.jsonl", shard_bytes)
    _atomic_write_json(root / "manifest.json", out, sort_keys=False)
    _validate_input_manifest(root, verify_hashes=True)
    return root


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Cut the deterministic real-data M2 smoke subset")
    parser.add_argument("--input-artifact", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--per-type", type=int, default=250)
    args = parser.parse_args(argv)
    root = build_smoke_subset(args.input_artifact, args.output_root, per_type=args.per_type)
    print(json.dumps({"status": "PASS", "output_root": str(root)}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
