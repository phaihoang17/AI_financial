"""Immutable BGE-M3 experience index with development-calibrated relevance gates."""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path

from src.indexing.embedding_artifact_builder import APPROVED_EMBEDDING_FINGERPRINT
from src.indexing.embedding_schemas import EMBEDDING_DIMENSION
from src.memory.schemas import MEMORY_SCHEMA_VERSION, MemoryContext, MemoryEntry, RULE_TEXT
from src.programmer.schemas import canonical_json
from src.retrieval.query_embedding import embed_query_texts
from src.retrieval.schemas import RetrievalCompany, RetrievalQuery
from src.supervisor.schemas import EvidenceSource
from src.understanding.schemas import SchemaValidationError, _require_exact_keys

MAX_INJECTED_ENTRIES = 3
MAX_INDEX_BYTES = 10 * 1024 * 1024


def embed_context(context, encoder):
    text = canonical_json(context.to_dict())
    query = RetrievalQuery(text, RetrievalCompany("", ""), [], context.period_kind,
                           None, list(context.metrics), None, [EvidenceSource.TABLE],
                           None, None, [text])
    return tuple(embed_query_texts(query, encoder=encoder, batch_size=1)[0].vector)


def _cosine(left, right):
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right)) /
                         (math.sqrt(sum(v * v for v in left)) * math.sqrt(sum(v * v for v in right)))))


@dataclass(frozen=True)
class DevelopmentExample:
    source_id: str
    context: MemoryContext
    relevant_entry_ids: tuple[str, ...]
    split: str = "DEV"

    def __post_init__(self):
        if self.split != "DEV" or not isinstance(self.source_id, str) or not self.source_id:
            raise SchemaValidationError("calibration requires identified DEV examples")
        if not isinstance(self.context, MemoryContext) or not isinstance(self.relevant_entry_ids, tuple):
            raise SchemaValidationError("calibration context/labels are invalid")


@dataclass(frozen=True)
class Calibration:
    threshold: float
    min_positive: float
    max_negative: float
    source_ids: tuple[str, ...]
    dataset_id: str

    def __post_init__(self):
        values = (self.threshold, self.min_positive, self.max_negative)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise SchemaValidationError("calibration scores must be finite")
        if not (0 <= self.threshold <= 1 and -1 <= self.max_negative < self.threshold <= self.min_positive <= 1):
            raise SchemaValidationError("development scores do not separate relevance")
        if (not isinstance(self.source_ids, tuple) or not self.source_ids
                or any(not isinstance(source, str) or not source.strip() for source in self.source_ids)
                or len(set(self.source_ids)) != len(self.source_ids)):
            raise SchemaValidationError("calibration needs unique source IDs")
        if not isinstance(self.dataset_id, str) or not self.dataset_id:
            raise SchemaValidationError("calibration needs dataset provenance")

    def to_dict(self):
        return {"split": "DEV", "threshold": self.threshold, "min_positive": self.min_positive,
                "max_negative": self.max_negative, "source_ids": list(self.source_ids),
                "dataset_id": self.dataset_id}

    @classmethod
    def from_dict(cls, data):
        _require_exact_keys(data, {"split", "threshold", "min_positive", "max_negative", "source_ids", "dataset_id"}, "Calibration")
        if data["split"] != "DEV" or not isinstance(data["source_ids"], list):
            raise SchemaValidationError("calibration must use DEV provenance")
        return cls(data["threshold"], data["min_positive"], data["max_negative"],
                   tuple(data["source_ids"]), data["dataset_id"])


@dataclass(frozen=True)
class MemoryIndex:
    entries: tuple[MemoryEntry, ...]
    vectors: tuple[tuple[float, ...], ...]
    calibration: Calibration
    encoder: object

    def __post_init__(self):
        if not isinstance(self.entries, tuple) or not isinstance(self.vectors, tuple):
            raise SchemaValidationError("memory entries/vectors must be immutable tuples")
        if not self.entries or len(self.entries) != len(self.vectors):
            raise SchemaValidationError("memory entries/vectors must be non-empty and aligned")
        if not all(isinstance(e, MemoryEntry) for e in self.entries) or not isinstance(self.calibration, Calibration):
            raise SchemaValidationError("invalid memory contracts")
        if len({e.entry_id for e in self.entries}) != len(self.entries):
            raise SchemaValidationError("duplicate memory entry IDs")
        for vector in self.vectors:
            if not isinstance(vector, tuple) or len(vector) != EMBEDDING_DIMENSION or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
                raise SchemaValidationError("memory vector is invalid")
            if abs(sum(v * v for v in vector) - 1) > 0.0001:
                raise SchemaValidationError("memory vector must be normalized")

    def to_dict(self):
        return {"schema_version": MEMORY_SCHEMA_VERSION,
                "embedding_fingerprint": APPROVED_EMBEDDING_FINGERPRINT,
                "calibration": self.calibration.to_dict(),
                "records": [{"entry": e.to_dict(), "embedding_ref": e.entry_id, "vector": list(v)}
                            for e, v in zip(self.entries, self.vectors)]}

    @property
    def artifact_id(self):
        return sha256(canonical_json(self.to_dict()).encode()).hexdigest()

    def retrieve(self, context, *, top_k=MAX_INJECTED_ENTRIES):
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= MAX_INJECTED_ENTRIES:
            raise SchemaValidationError("memory top_k exceeds the fixed injection cap")
        candidates = [(entry, vector) for entry, vector in zip(self.entries, self.vectors)
                      if entry.context.compatible_with(context)]
        if not candidates:
            return ()
        query = embed_context(context, self.encoder)
        # shortcut: use the existing FAISS stack when experience banks exceed 10 MiB.
        ranked = sorted(((-_cosine(query, vector), entry.entry_id, entry) for entry, vector in candidates),
                        key=lambda item: item[:2])
        selected, seen = [], set()
        for negative_score, _, entry in ranked:
            if -negative_score < self.calibration.threshold or entry.content_signature in seen:
                continue
            selected.append(entry)
            seen.add(entry.content_signature)
            if len(selected) == top_k:
                break
        return tuple(selected)

    def advice(self, programmer_input):
        entries = self.retrieve(MemoryContext.from_plan(programmer_input.plan))
        return [{"entry_id": entry.entry_id, "kind": entry.kind.value,
                 "rules": [RULE_TEXT[rule] for rule in entry.rules]} for entry in entries]

    @classmethod
    def load(cls, path, *, encoder):
        path = Path(path)
        if path.stat().st_size > MAX_INDEX_BYTES:
            raise SchemaValidationError("memory artifact exceeds size limit")
        data = json.loads(path.read_text())
        _require_exact_keys(data, {"artifact_id", "schema_version", "embedding_fingerprint", "calibration", "records"}, "MemoryIndex")
        if data["schema_version"] != MEMORY_SCHEMA_VERSION or data["embedding_fingerprint"] != APPROVED_EMBEDDING_FINGERPRINT:
            raise SchemaValidationError("memory artifact/model version mismatch")
        if not isinstance(data["records"], list):
            raise SchemaValidationError("memory records must be a list")
        entries, vectors = [], []
        for record in data["records"]:
            _require_exact_keys(record, {"entry", "embedding_ref", "vector"}, "MemoryRecord")
            entry = MemoryEntry.from_dict(record["entry"])
            if record["embedding_ref"] != entry.entry_id or not isinstance(record["vector"], list):
                raise SchemaValidationError("memory embedding identity is invalid")
            entries.append(entry)
            vectors.append(tuple(record["vector"]))
        index = cls(tuple(entries), tuple(vectors), Calibration.from_dict(data["calibration"]), encoder)
        if data["artifact_id"] != index.artifact_id:
            raise SchemaValidationError("memory artifact hash mismatch")
        return index


def build_index(entries, development_examples, *, encoder, dataset_id,
                held_out_ids=(), held_out_question_signatures=()):
    entries = tuple(entries)
    development_examples = tuple(development_examples)
    if not entries or not development_examples:
        raise SchemaValidationError("index needs training entries and development calibration")
    if any(e.source_id in held_out_ids or e.question_signature in held_out_question_signatures for e in entries):
        raise SchemaValidationError("memory index overlaps held-out data")
    cache = {}
    vectors = []
    for entry in entries:
        if entry.context not in cache:
            cache[entry.context] = embed_context(entry.context, encoder)
        vectors.append(cache[entry.context])
    positives, negatives = [], []
    known = {entry.entry_id for entry in entries}
    for example in development_examples:
        if not isinstance(example, DevelopmentExample) or example.source_id in held_out_ids:
            raise SchemaValidationError("calibration overlaps held-out data")
        if set(example.relevant_entry_ids) - known:
            raise SchemaValidationError("development labels reference unknown entries")
        vector = embed_context(example.context, encoder)
        for entry, stored in zip(entries, vectors):
            score = _cosine(vector, stored)
            (positives if entry.entry_id in example.relevant_entry_ids else negatives).append(score)
    if not positives or not negatives:
        raise SchemaValidationError("calibration needs positive and negative examples")
    threshold = max(0.0, (min(positives) + max(negatives)) / 2)
    calibration = Calibration(threshold, min(positives), max(negatives),
                              tuple(e.source_id for e in development_examples), dataset_id)
    return MemoryIndex(entries, tuple(vectors), calibration, encoder)


def validate_output_path(path):
    path = Path(path).resolve()
    if set(part.casefold() for part in path.parts) & {"gold", "test", "tests", "heldout", "held-out", "evaluation"}:
        raise SchemaValidationError("cannot write experiment artifacts into held-out/test/gold paths")
    return path


def publish_index(index, path):
    """Offline publication only; inference MemoryIndex deliberately has no writer."""
    path = validate_output_path(path)
    payload = canonical_json({"artifact_id": index.artifact_id, **index.to_dict()}) + "\n"
    if len(payload.encode()) > MAX_INDEX_BYTES:
        raise SchemaValidationError("memory artifact exceeds size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(payload)
