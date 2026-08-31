"""Gold retrieval / answer-correctness evaluation set (TASK-100, RETRIEVAL_QUALITY).

This is the schema + tooling for a *real* per-question gold set, kept strictly
separate from fixtures and smoke queries. Nothing here invents an annotation:
a case only counts as gold when its evidence pointers resolve against the
committed canonical corpus (``corpus_reference_index``) and its
``annotation_status`` says so.

Contents:

* ``GoldRetrievalCase`` — one annotated question. Minimal fields:
  ``question_id``, ``question``, ``company`` (ticker required), ``period``,
  ``metric`` / ``operation`` (optional), ``gold_report_id``, ``gold_table_id``,
  ``gold_paragraph_id``, ``gold_chunk_ids``, ``gold_source_cell_ids``,
  ``gold_answer``, ``provenance``, ``annotation_status``.
* ``load_gold_file`` / ``dump_gold_cases`` — JSONL round-trip.
* ``validate_gold_cases`` — schema, duplicates, missing evidence, and
  referential integrity against the corpus.
* ``classify_question`` — AUTO_DERIVABLE / NEEDS_MANUAL_ANNOTATION / UNSUPPORTED.
* ``to_required_evidence`` + ``compute_gold_retrieval_metrics`` — bridge to the
  existing ``src/evaluation/retrieval.py`` metric layer (Recall@k, MRR). Metrics
  are computed *only* over cases with valid gold evidence.
* ``score_answer_correctness`` — exact/normalised answer match, VERIFIED-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from src.evaluation.corpus_reference_index import CorpusReferenceIndex
from src.evaluation.retrieval import RequiredEvidence, mrr, recall_at_k
from src.supervisor.schemas import EvidenceSource

GOLD_RETRIEVAL_SCHEMA_VERSION = "m10-gold-retrieval-v1"


class GoldContractError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class GoldAnnotationStatus(str, Enum):
    #: source carries a deterministic, corpus-resolvable evidence pointer
    AUTO_DERIVABLE = "AUTO_DERIVABLE"
    #: company/report resolve but evidence/answer still needs a human
    NEEDS_MANUAL_ANNOTATION = "NEEDS_MANUAL_ANNOTATION"
    #: cannot be grounded in this corpus at all
    UNSUPPORTED = "UNSUPPORTED"
    #: a human confirmed the evidence (and answer, if present)
    VERIFIED = "VERIFIED"


class GoldProvenanceMethod(str, Enum):
    AUTO_DERIVED = "AUTO_DERIVED"
    MANUAL = "MANUAL"
    #: a heuristic guess that is explicitly NOT gold until verified
    BOOTSTRAP_UNVERIFIED = "BOOTSTRAP_UNVERIFIED"


_GOLD_CASE_KEYS = frozenset(
    {
        "question_id",
        "question",
        "company",
        "period",
        "period_kind",
        "statement_scope",
        "metric",
        "operation",
        "gold_report_id",
        "gold_table_id",
        "gold_paragraph_id",
        "gold_chunk_ids",
        "gold_source_cell_ids",
        "gold_answer",
        "provenance",
        "annotation_status",
    }
)

#: statuses that assert the case is usable as retrieval gold
_GOLD_BEARING = frozenset({GoldAnnotationStatus.AUTO_DERIVABLE, GoldAnnotationStatus.VERIFIED})


def _clean_str(value: Any, name: str, *, required: bool = True) -> Optional[str]:
    if value is None:
        if required:
            raise GoldContractError("GOLD_CASE_INVALID", f"{name} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise GoldContractError("GOLD_CASE_INVALID", f"{name} must be a non-empty string")
    return value


def _clean_str_list(value: Any, name: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise GoldContractError("GOLD_CASE_INVALID", f"{name} must be a list of non-empty strings")
    return tuple(value)


@dataclass(frozen=True)
class GoldAnswer:
    value: str
    unit: Optional[str] = None
    scale: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "unit": self.unit, "scale": self.scale}

    @classmethod
    def from_dict(cls, value: Any) -> "GoldAnswer":
        if not isinstance(value, Mapping) or "value" not in value:
            raise GoldContractError("GOLD_CASE_INVALID", "gold_answer must have a 'value'")
        raw = value["value"]
        if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
            raise GoldContractError("GOLD_CASE_INVALID", "gold_answer.value must be str or number")
        return cls(
            value=str(raw),
            unit=value.get("unit"),
            scale=value.get("scale"),
        )


@dataclass(frozen=True)
class GoldProvenance:
    source: str
    method: GoldProvenanceMethod
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "method": self.method.value, "notes": self.notes}

    @classmethod
    def from_dict(cls, value: Any) -> "GoldProvenance":
        if not isinstance(value, Mapping) or "source" not in value or "method" not in value:
            raise GoldContractError(
                "GOLD_CASE_INVALID", "provenance needs 'source' and 'method'"
            )
        return cls(
            source=_clean_str(value["source"], "provenance.source"),
            method=GoldProvenanceMethod(value["method"]),
            notes=value.get("notes"),
        )


@dataclass(frozen=True)
class GoldRetrievalCase:
    question_id: str
    question: str
    company_ticker: str
    company_name: Optional[str]
    period: Optional[str]
    period_kind: Optional[str]
    statement_scope: Optional[str]
    metric: Optional[str]
    operation: Optional[str]
    gold_report_id: Optional[str]
    gold_table_id: Optional[str]
    gold_paragraph_id: Optional[str]
    gold_chunk_ids: Tuple[str, ...]
    gold_source_cell_ids: Tuple[str, ...]
    gold_answer: Optional[GoldAnswer]
    provenance: GoldProvenance
    annotation_status: GoldAnnotationStatus

    def has_evidence_pointer(self) -> bool:
        return bool(
            self.gold_report_id
            or self.gold_table_id
            or self.gold_paragraph_id
            or self.gold_chunk_ids
            or self.gold_source_cell_ids
        )

    def is_gold_bearing(self) -> bool:
        return self.annotation_status in _GOLD_BEARING and self.has_evidence_pointer()

    def evidence_signature(self) -> Tuple[Any, ...]:
        return (
            self.company_ticker,
            self.period,
            self.statement_scope,
            self.gold_report_id,
            self.gold_table_id,
            self.gold_paragraph_id,
            tuple(sorted(self.gold_chunk_ids)),
            tuple(sorted(self.gold_source_cell_ids)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "company": {"name": self.company_name, "ticker": self.company_ticker},
            "period": self.period,
            "period_kind": self.period_kind,
            "statement_scope": self.statement_scope,
            "metric": self.metric,
            "operation": self.operation,
            "gold_report_id": self.gold_report_id,
            "gold_table_id": self.gold_table_id,
            "gold_paragraph_id": self.gold_paragraph_id,
            "gold_chunk_ids": list(self.gold_chunk_ids),
            "gold_source_cell_ids": list(self.gold_source_cell_ids),
            "gold_answer": None if self.gold_answer is None else self.gold_answer.to_dict(),
            "provenance": self.provenance.to_dict(),
            "annotation_status": self.annotation_status.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "GoldRetrievalCase":
        if not isinstance(value, Mapping):
            raise GoldContractError("GOLD_CASE_INVALID", "case must be an object")
        unknown = set(value) - _GOLD_CASE_KEYS
        if unknown:
            raise GoldContractError(
                "GOLD_CASE_INVALID", f"unknown gold-case keys: {sorted(unknown)}"
            )
        company = value.get("company")
        if not isinstance(company, Mapping) or not company.get("ticker"):
            raise GoldContractError("GOLD_CASE_INVALID", "company.ticker is required")
        answer = value.get("gold_answer")
        return cls(
            question_id=_clean_str(value.get("question_id"), "question_id"),
            question=_clean_str(value.get("question"), "question"),
            company_ticker=_clean_str(company.get("ticker"), "company.ticker"),
            company_name=company.get("name"),
            period=_clean_str(value.get("period"), "period", required=False),
            period_kind=_clean_str(value.get("period_kind"), "period_kind", required=False),
            statement_scope=_clean_str(
                value.get("statement_scope"), "statement_scope", required=False
            ),
            metric=_clean_str(value.get("metric"), "metric", required=False),
            operation=_clean_str(value.get("operation"), "operation", required=False),
            gold_report_id=_clean_str(value.get("gold_report_id"), "gold_report_id", required=False),
            gold_table_id=_clean_str(value.get("gold_table_id"), "gold_table_id", required=False),
            gold_paragraph_id=_clean_str(
                value.get("gold_paragraph_id"), "gold_paragraph_id", required=False
            ),
            gold_chunk_ids=_clean_str_list(value.get("gold_chunk_ids"), "gold_chunk_ids"),
            gold_source_cell_ids=_clean_str_list(
                value.get("gold_source_cell_ids"), "gold_source_cell_ids"
            ),
            gold_answer=None if answer is None else GoldAnswer.from_dict(answer),
            provenance=GoldProvenance.from_dict(value.get("provenance")),
            annotation_status=GoldAnnotationStatus(value.get("annotation_status")),
        )


# --------------------------------------------------------------------------- #
# JSONL round-trip
# --------------------------------------------------------------------------- #
def load_gold_file(path: str | Path) -> List[GoldRetrievalCase]:
    text = Path(path).read_text(encoding="utf-8")
    cases: List[GoldRetrievalCase] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            cases.append(GoldRetrievalCase.from_dict(json.loads(line)))
        except (ValueError, GoldContractError) as error:
            raise GoldContractError("GOLD_FILE_INVALID", f"line {line_number}: {error}") from error
    return cases


def dump_gold_cases(cases: Sequence[GoldRetrievalCase]) -> str:
    return "".join(
        json.dumps(case.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for case in cases
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GoldValidationIssue:
    question_id: Optional[str]
    severity: str  # "ERROR" | "WARNING"
    code: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class GoldValidationReport:
    issues: Tuple[GoldValidationIssue, ...]
    case_count: int
    gold_bearing_count: int
    status_counts: Mapping[str, int]

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "WARNING")

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": GOLD_RETRIEVAL_SCHEMA_VERSION,
            "ok": self.ok,
            "case_count": self.case_count,
            "gold_bearing_count": self.gold_bearing_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "status_counts": dict(self.status_counts),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_gold_cases(
    cases: Sequence[GoldRetrievalCase], index: Optional[CorpusReferenceIndex] = None
) -> GoldValidationReport:
    """Schema is already enforced by ``from_dict``; this adds cross-case and
    corpus-referential checks. Referential checks are skipped when ``index`` is
    ``None`` (a warning is emitted instead)."""
    issues: List[GoldValidationIssue] = []
    seen_ids: Dict[str, int] = {}
    seen_signatures: Dict[Tuple[Any, ...], str] = {}
    status_counts: Dict[str, int] = {}

    for case in cases:
        status_counts[case.annotation_status.value] = (
            status_counts.get(case.annotation_status.value, 0) + 1
        )

        if case.question_id in seen_ids:
            issues.append(
                GoldValidationIssue(
                    case.question_id, "ERROR", "DUPLICATE_QUESTION_ID",
                    f"question_id repeated (also line index {seen_ids[case.question_id]})",
                )
            )
        seen_ids[case.question_id] = len(seen_ids)

        signature = case.evidence_signature()
        if case.has_evidence_pointer() and signature in seen_signatures:
            issues.append(
                GoldValidationIssue(
                    case.question_id, "WARNING", "DUPLICATE_EVIDENCE",
                    f"identical company/period/evidence as {seen_signatures[signature]!r}",
                )
            )
        elif case.has_evidence_pointer():
            seen_signatures[signature] = case.question_id

        if case.annotation_status in _GOLD_BEARING and not case.has_evidence_pointer():
            issues.append(
                GoldValidationIssue(
                    case.question_id, "ERROR", "MISSING_EVIDENCE",
                    f"status {case.annotation_status.value} requires >=1 gold evidence pointer",
                )
            )

        if (
            case.provenance.method is GoldProvenanceMethod.BOOTSTRAP_UNVERIFIED
            and case.annotation_status in _GOLD_BEARING
        ):
            issues.append(
                GoldValidationIssue(
                    case.question_id, "ERROR", "BOOTSTRAP_NOT_GOLD",
                    "BOOTSTRAP_UNVERIFIED provenance cannot back an AUTO_DERIVABLE/VERIFIED status",
                )
            )

        if case.gold_answer is not None and case.annotation_status is not GoldAnnotationStatus.VERIFIED:
            issues.append(
                GoldValidationIssue(
                    case.question_id, "WARNING", "UNVERIFIED_ANSWER",
                    "gold_answer present but annotation_status is not VERIFIED",
                )
            )

        if index is None:
            continue
        issues.extend(_referential_issues(case, index))

    if index is None and cases:
        issues.append(
            GoldValidationIssue(
                None, "WARNING", "NO_CORPUS_INDEX",
                "referential integrity not checked (no corpus reference index supplied)",
            )
        )

    gold_bearing = sum(1 for case in cases if case.is_gold_bearing())
    return GoldValidationReport(tuple(issues), len(cases), gold_bearing, status_counts)


def _referential_issues(
    case: GoldRetrievalCase, index: CorpusReferenceIndex
) -> List[GoldValidationIssue]:
    out: List[GoldValidationIssue] = []
    report_meta = None
    if case.gold_report_id is not None:
        report_meta = index.report_meta(case.gold_report_id)
        if report_meta is None:
            out.append(
                GoldValidationIssue(
                    case.question_id, "ERROR", "REPORT_NOT_IN_CORPUS",
                    f"gold_report_id {case.gold_report_id} not in canonical corpus",
                )
            )

    if case.gold_table_id is not None:
        owner = index.table_report(case.gold_table_id)
        if owner is None:
            out.append(
                GoldValidationIssue(
                    case.question_id, "ERROR", "TABLE_NOT_IN_CORPUS",
                    f"gold_table_id {case.gold_table_id} not in canonical corpus",
                )
            )
        elif case.gold_report_id is not None and owner != case.gold_report_id:
            out.append(
                GoldValidationIssue(
                    case.question_id, "ERROR", "TABLE_REPORT_MISMATCH",
                    f"gold_table_id belongs to {owner}, not {case.gold_report_id}",
                )
            )

    if case.gold_paragraph_id is not None and not index.has_paragraph(case.gold_paragraph_id):
        out.append(
            GoldValidationIssue(
                case.question_id, "ERROR", "PARAGRAPH_NOT_IN_CORPUS",
                f"gold_paragraph_id {case.gold_paragraph_id} not in canonical corpus",
            )
        )

    for chunk_id in case.gold_chunk_ids:
        if not index.has_chunk(chunk_id):
            out.append(
                GoldValidationIssue(
                    case.question_id, "ERROR", "CHUNK_NOT_IN_CORPUS",
                    f"gold_chunk_id {chunk_id} not in canonical corpus",
                )
            )

    for cell_id in case.gold_source_cell_ids:
        if not index.has_source_cell(cell_id):
            out.append(
                GoldValidationIssue(
                    case.question_id, "ERROR", "SOURCE_CELL_NOT_IN_CORPUS",
                    f"gold_source_cell_id {cell_id} not in canonical corpus",
                )
            )

    if report_meta is not None:
        if case.company_ticker and report_meta.ticker != case.company_ticker:
            out.append(
                GoldValidationIssue(
                    case.question_id, "WARNING", "TICKER_MISMATCH",
                    f"case ticker {case.company_ticker} != report ticker {report_meta.ticker}",
                )
            )
        if (
            case.statement_scope
            and report_meta.statement_scope
            and report_meta.statement_scope != case.statement_scope
        ):
            out.append(
                GoldValidationIssue(
                    case.question_id, "WARNING", "SCOPE_MISMATCH",
                    f"case scope {case.statement_scope} != report scope {report_meta.statement_scope}",
                )
            )
        if case.period and case.period.isdigit() and int(case.period) != report_meta.report_year:
            out.append(
                GoldValidationIssue(
                    case.question_id, "WARNING", "PERIOD_MISMATCH",
                    f"case period {case.period} != report year {report_meta.report_year}",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Classification of a raw questions source
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ClassificationResult:
    question_id: str
    status: GoldAnnotationStatus
    reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


def classify_question(record: Mapping[str, Any], index: CorpusReferenceIndex) -> ClassificationResult:
    """Deterministically bucket one raw questions-source record.

    * ``UNSUPPORTED`` — no resolvable company ticker, or no matching report.
    * ``AUTO_DERIVABLE`` — the source already carries a corpus-resolvable
      evidence pointer (report + table, or paragraph).
    * ``NEEDS_MANUAL_ANNOTATION`` — company/report resolve, but the evidence
      pointer must be produced by a human.
    """
    qid = str(record.get("question_id") or record.get("id") or record.get("qid") or "")
    company = record.get("company") or {}
    ticker = (company.get("ticker") if isinstance(company, Mapping) else None) or record.get("ticker")
    reasons: List[str] = []

    if not ticker:
        return ClassificationResult(qid, GoldAnnotationStatus.UNSUPPORTED, ("no company ticker",))
    if not index.known_ticker(ticker):
        return ClassificationResult(
            qid, GoldAnnotationStatus.UNSUPPORTED, (f"ticker {ticker} not in corpus",)
        )

    period = record.get("period")
    scope = record.get("statement_scope")
    year = int(period) if isinstance(period, str) and period.isdigit() else None
    if year is not None:
        matching = index.reports_for(ticker, year, scope if isinstance(scope, str) else None)
        if not matching:
            return ClassificationResult(
                qid,
                GoldAnnotationStatus.UNSUPPORTED,
                (f"no report for {ticker} {year} scope={scope}",),
            )
        reasons.append(f"{len(matching)} candidate report(s)")

    report_id = record.get("gold_report_id") or record.get("report_id")
    table_id = record.get("gold_table_id") or record.get("table_id")
    paragraph_id = record.get("gold_paragraph_id") or record.get("paragraph_id")

    report_ok = bool(report_id) and index.has_report(report_id)
    table_ok = bool(table_id) and index.has_table(table_id)
    paragraph_ok = bool(paragraph_id) and index.has_paragraph(paragraph_id)

    if report_ok and (table_ok or paragraph_ok):
        reasons.append("source evidence pointer resolves in corpus")
        return ClassificationResult(qid, GoldAnnotationStatus.AUTO_DERIVABLE, tuple(reasons))

    reasons.append("no deterministic evidence pointer in source")
    return ClassificationResult(qid, GoldAnnotationStatus.NEEDS_MANUAL_ANNOTATION, tuple(reasons))


# --------------------------------------------------------------------------- #
# Bridge to the existing Recall@k / MRR metric layer
# --------------------------------------------------------------------------- #
def to_required_evidence(case: GoldRetrievalCase) -> Tuple[RequiredEvidence, ...]:
    out: List[RequiredEvidence] = []
    if case.gold_table_id is not None or (
        case.gold_report_id is not None and case.gold_paragraph_id is None
    ):
        out.append(
            RequiredEvidence(
                source_type=EvidenceSource.TABLE,
                table_id=case.gold_table_id,
                source_cell_ids=case.gold_source_cell_ids,
                metric=case.metric,
                period=case.period,
                report_id=case.gold_report_id,
            )
        )
    if case.gold_paragraph_id is not None:
        out.append(
            RequiredEvidence(
                source_type=EvidenceSource.TEXT,
                paragraph_id=case.gold_paragraph_id,
                metric=case.metric,
                period=case.period,
                report_id=case.gold_report_id,
            )
        )
    if not out:
        raise GoldContractError(
            "GOLD_CASE_INVALID", f"{case.question_id}: no evidence pointer to build RequiredEvidence"
        )
    return tuple(out)


@dataclass(frozen=True)
class GoldRetrievalMetrics:
    evaluated_case_count: int
    skipped_case_count: int
    k_values: Tuple[int, ...]
    recall_at_k: Mapping[int, float]
    mean_reciprocal_rank: float
    mrr_hit_count: int
    per_case: Tuple[Mapping[str, Any], ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": GOLD_RETRIEVAL_SCHEMA_VERSION,
            "evaluated_case_count": self.evaluated_case_count,
            "skipped_case_count": self.skipped_case_count,
            "k_values": list(self.k_values),
            "recall_at_k": {str(k): v for k, v in self.recall_at_k.items()},
            "mrr": self.mean_reciprocal_rank,
            "mrr_hit_count": self.mrr_hit_count,
            "per_case": list(self.per_case),
        }


def compute_gold_retrieval_metrics(
    cases: Sequence[GoldRetrievalCase],
    retrieve_fn: Callable[[GoldRetrievalCase], Sequence[Any]],
    *,
    k_values: Sequence[int] = (1, 5, 10),
) -> GoldRetrievalMetrics:
    """Compute Recall@k and MRR over the gold-bearing cases only.

    ``retrieve_fn`` returns the *final reranked* candidate list for a case
    (``RetrievalCandidate`` objects). No candidate ordering is done here — this
    only scores what the real retrieval stack produced.
    """
    ks = tuple(sorted({int(k) for k in k_values}))
    evaluated = 0
    skipped = 0
    recall_sums: Dict[int, float] = {k: 0.0 for k in ks}
    reciprocal_sum = 0.0
    mrr_hits = 0
    per_case: List[Mapping[str, Any]] = []

    for case in cases:
        if not case.is_gold_bearing():
            skipped += 1
            continue
        required = to_required_evidence(case)
        candidates = list(retrieve_fn(case))
        case_recall = {k: recall_at_k(candidates, required, k) for k in ks}
        case_mrr = mrr(candidates, required)
        evaluated += 1
        for k in ks:
            recall_sums[k] += case_recall[k]
        if case_mrr is not None:
            reciprocal_sum += case_mrr
            mrr_hits += 1
        per_case.append(
            {
                "question_id": case.question_id,
                "candidate_count": len(candidates),
                "recall_at_k": {str(k): case_recall[k] for k in ks},
                "reciprocal_rank": case_mrr,
            }
        )

    recall = {k: (recall_sums[k] / evaluated if evaluated else 0.0) for k in ks}
    mean_rr = reciprocal_sum / evaluated if evaluated else 0.0
    return GoldRetrievalMetrics(
        evaluated, skipped, ks, recall, mean_rr, mrr_hits, tuple(per_case)
    )


# --------------------------------------------------------------------------- #
# Answer correctness (VERIFIED gold_answer only)
# --------------------------------------------------------------------------- #
def _normalise_answer(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch in ".,-").replace(",", "")


def score_answer_correctness(
    cases: Sequence[GoldRetrievalCase],
    predicted: Mapping[str, str],
    *,
    relative_tolerance: float = 1e-4,
) -> Dict[str, Any]:
    """Score predicted answers against VERIFIED gold answers only."""
    scored = 0
    correct = 0
    missing_prediction = 0
    per_case: List[Mapping[str, Any]] = []
    for case in cases:
        if case.annotation_status is not GoldAnnotationStatus.VERIFIED or case.gold_answer is None:
            continue
        scored += 1
        pred = predicted.get(case.question_id)
        if pred is None:
            missing_prediction += 1
            per_case.append({"question_id": case.question_id, "correct": False, "reason": "no prediction"})
            continue
        gold_norm = _normalise_answer(case.gold_answer.value)
        pred_norm = _normalise_answer(str(pred))
        is_correct = gold_norm == pred_norm
        if not is_correct:
            try:
                gold_num = float(gold_norm)
                pred_num = float(pred_norm)
                denom = abs(gold_num) or 1.0
                is_correct = abs(gold_num - pred_num) / denom <= relative_tolerance
            except ValueError:
                pass
        correct += int(is_correct)
        per_case.append({"question_id": case.question_id, "correct": is_correct})
    return {
        "schema_version": GOLD_RETRIEVAL_SCHEMA_VERSION,
        "scored_case_count": scored,
        "correct_count": correct,
        "missing_prediction_count": missing_prediction,
        "accuracy": (correct / scored) if scored else None,
        "per_case": per_case,
    }
