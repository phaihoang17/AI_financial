"""Full-corpus retrieval production validation (TASK-109 / ADR-057).

This is the smallest real runner that decides whether the production retrieval
stack is validated. It consumes the committed artifacts and the pinned models
and reports two deliberately separate categories:

1. ``ARTIFACT_SERVING`` — corpus/index/BM25 compatibility, the exact vector
   count == canonical chunk count gate, shard + SQLite integrity, embedding
   fingerprint, and the two LIVE serving probes (BGE-M3, BGE-reranker-v2-m3),
   plus a deterministic retrieval-execution smoke. Every check here is either
   ``ARTIFACT`` evidence (static inspection of committed files) or ``LIVE``
   evidence (a real served-model call). No fixture metric is ever accepted.
   ``vector_root`` is the streaming builder's ``--output-root`` verbatim — the
   family root that holds ``manifest.json`` + ``CURRENT`` +
   ``artifacts/<build_id>/`` — not the inner ``artifacts/<build_id>`` directory.
   The retrieval-execution smoke is produced by
   ``run_retrieval_execution_smoke`` and passed in via ``retrieval_smoke``.

2. ``RETRIEVAL_QUALITY`` — Recall@k / MRR. Reported only when real gold
   retrieval-evidence annotations are supplied. The public ViFinQA
   ``questions.jsonl`` carries only ``id`` + ``question`` (no gold report /
   table / cell / paragraph), so with it this category is ``BLOCKED_GOLD_DATA``.
   Fixture retrieval metrics are never substituted.

Clearing ``GPU_PRODUCTION_VALIDATION_PENDING`` requires category 1 fully PASS
against the real committed artifacts on GPU hardware (including both LIVE
probes). Category 2 stays independently ``BLOCKED_GOLD_DATA`` and is not a
blocker for the GPU flag, but it *is* a blocker for any retrieval-quality claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from src.indexing.embedding_artifact_builder import APPROVED_EMBEDDING_FINGERPRINT
from src.indexing.vector_index_store import IndexStorageError, load_sharded_manifest
from src.retrieval.compatibility import (
    ArtifactCompatibilityError,
    validate_bm25_artifact_compatibility,
    validate_retrieval_artifact_compatibility,
)
from src.serving.live_probe import EmbeddingProbeResult, RerankerProbeResult


PRODUCTION_RETRIEVAL_VALIDATION_SCHEMA_VERSION = "m10-retrieval-production-validation-v1"

# The canonical committed M2 corpus has this many EmbeddingChunks; the vector
# index MUST contain exactly one vector per chunk. This is the vector-count
# acceptance value — it is NOT the 146,246 source-table count.
EXPECTED_CANONICAL_CHUNK_COUNT = 1_743_311
# A number that must never be accepted as the vector count (source tables).
KNOWN_SOURCE_TABLE_COUNT = 146_246
GPU_PENDING_FLAG = "GPU_PRODUCTION_VALIDATION_PENDING"


class ProductionValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ValidationCategory(str, Enum):
    ARTIFACT_SERVING = "ARTIFACT_SERVING"
    RETRIEVAL_QUALITY = "RETRIEVAL_QUALITY"


class CheckEvidence(str, Enum):
    ARTIFACT = "ARTIFACT"  # static inspection of committed files
    LIVE = "LIVE"  # a real served-model / real-backend call


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"  # a LIVE check that was not run
    BLOCKED = "BLOCKED"  # cannot run for a documented reason (e.g. no gold data)


@dataclass(frozen=True)
class CheckResult:
    name: str
    category: ValidationCategory
    evidence: CheckEvidence
    status: CheckStatus
    code: Optional[str]
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category.value,
            "evidence": self.evidence.value,
            "status": self.status.value,
            "code": self.code,
            "detail": dict(self.detail),
        }


def _artifact(name: str, status: CheckStatus, code: Optional[str], **detail: Any) -> CheckResult:
    return CheckResult(name, ValidationCategory.ARTIFACT_SERVING, CheckEvidence.ARTIFACT, status, code, dict(detail))


def _live(name: str, status: CheckStatus, code: Optional[str], **detail: Any) -> CheckResult:
    return CheckResult(name, ValidationCategory.ARTIFACT_SERVING, CheckEvidence.LIVE, status, code, dict(detail))


def _read_manifest(root: str | Path, code: str) -> Dict[str, Any]:
    path = Path(root) / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProductionValidationError(code, f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise ProductionValidationError(code, f"{path} is not a JSON object")
    return data


# --------------------------------------------------------------------------- #
# Category 1 — artifact / serving correctness
# --------------------------------------------------------------------------- #

def _compatibility_checks(
    corpus_root: str | Path, vector_root: str | Path, bm25_root: str | Path
) -> List[CheckResult]:
    results: List[CheckResult] = []
    bm25_manifest = _read_manifest(bm25_root, "BM25_MANIFEST_MISSING")
    try:
        vector_report = validate_retrieval_artifact_compatibility(
            vector_root, corpus_root, bm25_metadata=bm25_manifest
        )
        results.append(
            _artifact(
                "corpus_index_compatibility",
                CheckStatus.PASS,
                None,
                artifact_id=vector_report.artifact_id,
                corpus_fingerprint=vector_report.corpus_fingerprint,
                model_fingerprint=vector_report.model_fingerprint,
                vector_count=vector_report.vector_count,
            )
        )
    except ArtifactCompatibilityError as error:
        results.append(_artifact("corpus_index_compatibility", CheckStatus.FAIL, error.code, message=str(error)))
    try:
        bm25_report = validate_bm25_artifact_compatibility(bm25_root, corpus_root)
        results.append(
            _artifact(
                "bm25_compatibility",
                CheckStatus.PASS,
                None,
                artifact_id=bm25_report.artifact_id,
                corpus_fingerprint=bm25_report.corpus_fingerprint,
            )
        )
    except ArtifactCompatibilityError as error:
        results.append(_artifact("bm25_compatibility", CheckStatus.FAIL, error.code, message=str(error)))
    return results


def _vector_count_checks(
    corpus_root: str | Path,
    vector_root: str | Path,
    *,
    expected_canonical_chunk_count: int,
    operator_expected_vector_count: Optional[int],
) -> List[CheckResult]:
    corpus_manifest = _read_manifest(corpus_root, "M2_CORPUS_MANIFEST_MISSING")
    vector_manifest = _read_manifest(vector_root, "VECTOR_MANIFEST_MISSING")
    corpus_chunk_count = corpus_manifest.get("chunk_count")
    vector_count = vector_manifest.get("vector_count")
    table_repr = corpus_manifest.get("table_representation_count")
    repr_count = corpus_manifest.get("representation_count")

    results: List[CheckResult] = []

    # Anti-substitution: neither the operator's acceptance number nor the
    # expected-canonical number may be a representation/source-table count.
    substitutes = {
        value
        for value in (table_repr, repr_count, KNOWN_SOURCE_TABLE_COUNT)
        if isinstance(value, int)
    }
    for label, candidate in (
        ("expected_canonical_chunk_count", expected_canonical_chunk_count),
        ("operator_expected_vector_count", operator_expected_vector_count),
    ):
        if isinstance(candidate, int) and candidate in substitutes and candidate != corpus_chunk_count:
            results.append(
                _artifact(
                    "no_table_count_substitution",
                    CheckStatus.FAIL,
                    "TABLE_COUNT_SUBSTITUTED_FOR_VECTOR_COUNT",
                    field=label,
                    value=candidate,
                    source_table_count=KNOWN_SOURCE_TABLE_COUNT,
                    table_representation_count=table_repr,
                )
            )
    if not any(r.name == "no_table_count_substitution" for r in results):
        results.append(
            _artifact(
                "no_table_count_substitution",
                CheckStatus.PASS,
                None,
                source_table_count=KNOWN_SOURCE_TABLE_COUNT,
            )
        )

    # The canonical corpus really is the canonical corpus.
    if corpus_chunk_count == expected_canonical_chunk_count:
        results.append(
            _artifact("canonical_corpus_chunk_count", CheckStatus.PASS, None, chunk_count=corpus_chunk_count)
        )
    else:
        results.append(
            _artifact(
                "canonical_corpus_chunk_count",
                CheckStatus.FAIL,
                "CANONICAL_CHUNK_COUNT_MISMATCH",
                expected=expected_canonical_chunk_count,
                corpus_manifest_chunk_count=corpus_chunk_count,
            )
        )

    # Exact one-vector-per-chunk, cross-checked against the CANONICAL corpus.
    expected = corpus_chunk_count
    if operator_expected_vector_count is not None:
        expected = operator_expected_vector_count
    if (
        isinstance(vector_count, int)
        and isinstance(corpus_chunk_count, int)
        and vector_count == corpus_chunk_count
        and vector_count == expected
    ):
        results.append(
            _artifact(
                "vector_count_equals_canonical_chunk_count",
                CheckStatus.PASS,
                None,
                vector_count=vector_count,
                canonical_chunk_count=corpus_chunk_count,
            )
        )
    else:
        results.append(
            _artifact(
                "vector_count_equals_canonical_chunk_count",
                CheckStatus.FAIL,
                "VECTOR_COUNT_MISMATCH",
                vector_count=vector_count,
                canonical_chunk_count=corpus_chunk_count,
                operator_expected_vector_count=operator_expected_vector_count,
            )
        )
    return results


def _shard_integrity_check(vector_root: str | Path) -> CheckResult:
    # The chunking-config fingerprint is already gated by
    # ``validate_retrieval_artifact_compatibility`` (vector manifest
    # ``input_chunking_config_fingerprint`` + the SQLite metadata). Here we only
    # need shard hashes, SQLite integrity, and the model fingerprint.
    try:
        manifest = load_sharded_manifest(
            vector_root,
            verify=True,
            expected_model_fingerprint=APPROVED_EMBEDDING_FINGERPRINT,
        )
    except IndexStorageError as error:
        return _artifact("shard_and_sqlite_integrity", CheckStatus.FAIL, error.code, message=str(error))
    shards = manifest.get("shards") or []
    shard_sum = sum(int(entry.get("vector_count", 0)) for entry in shards)
    if shard_sum != manifest.get("vector_count"):
        return _artifact(
            "shard_and_sqlite_integrity",
            CheckStatus.FAIL,
            "SHARD_VECTOR_COUNT_SUM_MISMATCH",
            shard_vector_count_sum=shard_sum,
            manifest_vector_count=manifest.get("vector_count"),
        )
    return _artifact(
        "shard_and_sqlite_integrity",
        CheckStatus.PASS,
        None,
        shard_count=len(shards),
        vector_count=manifest.get("vector_count"),
    )


def _embedding_fingerprint_check(vector_root: str | Path) -> CheckResult:
    manifest = _read_manifest(vector_root, "VECTOR_MANIFEST_MISSING")
    got = manifest.get("embedding_fingerprint")
    if got == APPROVED_EMBEDDING_FINGERPRINT:
        return _artifact("embedding_fingerprint", CheckStatus.PASS, None, fingerprint=got)
    return _artifact(
        "embedding_fingerprint",
        CheckStatus.FAIL,
        "EMBEDDING_FINGERPRINT_MISMATCH",
        expected=APPROVED_EMBEDDING_FINGERPRINT,
        got=got,
    )


def _live_embedding_check(probe_result: Optional[EmbeddingProbeResult]) -> CheckResult:
    if probe_result is None:
        return _live("live_bge_m3_embedding_parity", CheckStatus.SKIPPED, "LIVE_SERVING_NOT_VALIDATED")
    if not isinstance(probe_result, EmbeddingProbeResult):
        raise ProductionValidationError(
            "LIVE_EVIDENCE_REQUIRES_PROBE_RESULT",
            "live embedding evidence must be an EmbeddingProbeResult from a real endpoint call",
        )
    status = CheckStatus.PASS if probe_result.passed else CheckStatus.FAIL
    return _live("live_bge_m3_embedding_parity", status, probe_result.code, probe=probe_result.to_dict())


def _live_reranker_check(probe_result: Optional[RerankerProbeResult]) -> CheckResult:
    if probe_result is None:
        return _live("live_bge_reranker_parity", CheckStatus.SKIPPED, "LIVE_SERVING_NOT_VALIDATED")
    if not isinstance(probe_result, RerankerProbeResult):
        raise ProductionValidationError(
            "LIVE_EVIDENCE_REQUIRES_PROBE_RESULT",
            "live reranker evidence must be a RerankerProbeResult from a real endpoint call",
        )
    status = CheckStatus.PASS if probe_result.passed else CheckStatus.FAIL
    return _live("live_bge_reranker_parity", status, probe_result.code, probe=probe_result.to_dict())


def _retrieval_smoke_check(smoke: Optional[Mapping[str, Any]]) -> CheckResult:
    if smoke is None:
        return _live("deterministic_retrieval_execution", CheckStatus.SKIPPED, "LIVE_BACKENDS_NOT_EXERCISED")
    ran = bool(smoke.get("ran"))
    byte_identical = bool(smoke.get("byte_identical"))
    crashes = int(smoke.get("crashes", 0))
    timeouts = int(smoke.get("timeouts", 0))
    detail = {
        "ran": ran,
        "byte_identical": byte_identical,
        "crashes": crashes,
        "timeouts": timeouts,
        "sample_size": int(smoke.get("sample_size", 0)),
    }
    if ran and byte_identical and crashes == 0 and timeouts == 0:
        return _live("deterministic_retrieval_execution", CheckStatus.PASS, None, **detail)
    return _live("deterministic_retrieval_execution", CheckStatus.FAIL, "RETRIEVAL_EXECUTION_NOT_DETERMINISTIC", **detail)


def validate_artifact_serving(
    corpus_root: str | Path,
    vector_root: str | Path,
    bm25_root: str | Path,
    *,
    embedding_probe_result: Optional[EmbeddingProbeResult] = None,
    reranker_probe_result: Optional[RerankerProbeResult] = None,
    retrieval_smoke: Optional[Mapping[str, Any]] = None,
    expected_canonical_chunk_count: int = EXPECTED_CANONICAL_CHUNK_COUNT,
    operator_expected_vector_count: Optional[int] = None,
) -> List[CheckResult]:
    """Run every category-1 check and return the ordered results."""
    checks: List[CheckResult] = []
    checks.extend(_compatibility_checks(corpus_root, vector_root, bm25_root))
    checks.extend(
        _vector_count_checks(
            corpus_root,
            vector_root,
            expected_canonical_chunk_count=expected_canonical_chunk_count,
            operator_expected_vector_count=operator_expected_vector_count,
        )
    )
    checks.append(_shard_integrity_check(vector_root))
    checks.append(_embedding_fingerprint_check(vector_root))
    checks.append(_live_embedding_check(embedding_probe_result))
    checks.append(_live_reranker_check(reranker_probe_result))
    checks.append(_retrieval_smoke_check(retrieval_smoke))
    return checks


# --------------------------------------------------------------------------- #
# Category 2 — retrieval quality (gold-gated)
# --------------------------------------------------------------------------- #

_REQUIRED_GOLD_FIELDS = frozenset({"question_id"})
_GOLD_EVIDENCE_FIELDS = ("gold_report_id", "gold_table_id", "gold_paragraph_id", "gold_source_cell_ids")


def validate_retrieval_quality(gold_annotations_path: Optional[str | Path] = None) -> CheckResult:
    """Return a quality check that is BLOCKED unless real gold evidence exists."""
    category = ValidationCategory.RETRIEVAL_QUALITY
    if gold_annotations_path is None:
        return CheckResult(
            "retrieval_quality_recall_mrr",
            category,
            CheckEvidence.ARTIFACT,
            CheckStatus.BLOCKED,
            "BLOCKED_GOLD_DATA",
            {"reason": "no gold retrieval-evidence annotations supplied"},
        )
    path = Path(gold_annotations_path)
    if not path.is_file():
        return CheckResult(
            "retrieval_quality_recall_mrr",
            category,
            CheckEvidence.ARTIFACT,
            CheckStatus.BLOCKED,
            "BLOCKED_GOLD_DATA",
            {"reason": "gold annotations file not found", "path": str(path)},
        )
    rows = _load_jsonl(path)
    has_gold_evidence = bool(rows) and all(
        isinstance(row, Mapping)
        and _REQUIRED_GOLD_FIELDS.issubset(row)
        and any(field_name in row for field_name in _GOLD_EVIDENCE_FIELDS)
        for row in rows
    )
    if not has_gold_evidence:
        return CheckResult(
            "retrieval_quality_recall_mrr",
            category,
            CheckEvidence.ARTIFACT,
            CheckStatus.BLOCKED,
            "BLOCKED_GOLD_DATA",
            {
                "reason": "file has no per-question gold retrieval-evidence fields",
                "row_count": len(rows),
                "required_any_of": list(_GOLD_EVIDENCE_FIELDS),
            },
        )
    # Real gold annotations exist. Computing Recall@k/MRR over the full corpus
    # is a separate deterministic evaluation; do not fabricate it here.
    return CheckResult(
        "retrieval_quality_recall_mrr",
        category,
        CheckEvidence.ARTIFACT,
        CheckStatus.BLOCKED,
        "GOLD_ANNOTATIONS_SUPPLIED_RUN_RETRIEVAL_EVAL",
        {"row_count": len(rows)},
    )


def _load_jsonl(path: Path) -> List[Any]:
    rows: List[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as error:
            raise ProductionValidationError("GOLD_ANNOTATIONS_INVALID", f"{path}: {error}") from error
    return rows


# --------------------------------------------------------------------------- #
# Combined report
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ProductionValidationReport:
    artifact_serving_checks: tuple[CheckResult, ...]
    retrieval_quality_check: CheckResult

    def _by_evidence(self, evidence: CheckEvidence) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.artifact_serving_checks if c.evidence is evidence)

    @property
    def artifact_status(self) -> str:
        static = self._by_evidence(CheckEvidence.ARTIFACT)
        return "PASS" if static and all(c.status is CheckStatus.PASS for c in static) else "FAIL"

    @property
    def live_serving_status(self) -> str:
        live = self._by_evidence(CheckEvidence.LIVE)
        if any(c.status is CheckStatus.FAIL for c in live):
            return "FAIL"
        if any(c.status is CheckStatus.SKIPPED for c in live):
            return "NOT_VALIDATED"
        return "PASS" if live and all(c.status is CheckStatus.PASS for c in live) else "NOT_VALIDATED"

    @property
    def retrieval_quality_status(self) -> str:
        return self.retrieval_quality_check.code or self.retrieval_quality_check.status.value

    @property
    def clears_gpu_pending(self) -> bool:
        """True only when category 1 is fully proven on real, live infrastructure."""
        return self.artifact_status == "PASS" and self.live_serving_status == "PASS"

    @property
    def overall_status(self) -> str:
        return "PASS" if self.artifact_status == "PASS" and self.live_serving_status == "PASS" else "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": PRODUCTION_RETRIEVAL_VALIDATION_SCHEMA_VERSION,
            "overall_status": self.overall_status,
            "artifact_status": self.artifact_status,
            "live_serving_status": self.live_serving_status,
            "retrieval_quality_status": self.retrieval_quality_status,
            "clears_gpu_pending": self.clears_gpu_pending,
            "gpu_pending_flag": GPU_PENDING_FLAG,
            "artifact_serving_checks": [c.to_dict() for c in self.artifact_serving_checks],
            "retrieval_quality_check": self.retrieval_quality_check.to_dict(),
        }


def run_full_corpus_retrieval_validation(
    corpus_root: str | Path,
    vector_root: str | Path,
    bm25_root: str | Path,
    *,
    embedding_probe_result: Optional[EmbeddingProbeResult] = None,
    reranker_probe_result: Optional[RerankerProbeResult] = None,
    retrieval_smoke: Optional[Mapping[str, Any]] = None,
    gold_annotations_path: Optional[str | Path] = None,
    expected_canonical_chunk_count: int = EXPECTED_CANONICAL_CHUNK_COUNT,
    operator_expected_vector_count: Optional[int] = None,
) -> ProductionValidationReport:
    checks = validate_artifact_serving(
        corpus_root,
        vector_root,
        bm25_root,
        embedding_probe_result=embedding_probe_result,
        reranker_probe_result=reranker_probe_result,
        retrieval_smoke=retrieval_smoke,
        expected_canonical_chunk_count=expected_canonical_chunk_count,
        operator_expected_vector_count=operator_expected_vector_count,
    )
    quality = validate_retrieval_quality(gold_annotations_path)
    return ProductionValidationReport(tuple(checks), quality)
