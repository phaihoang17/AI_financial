"""Deterministic real-corpus audit for source-backed TableClass provenance.

The audit does not classify from metrics or table content.  It validates the
committed M2 corpus and TableClass sidecar, replays the existing source-only
classifier over the exact raw reports, and exercises the production
``EvidenceProvenanceRepository -> locate_cells`` path.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.indexing.embedding_artifact_builder import (
    EmbeddingArtifactBuilderError,
    _iter_input_records,
    _validate_input_manifest,
)
from src.indexing.document_parser import parse_document
from src.indexing.provenance_sidecar import (
    ProvenanceSidecarError,
    _canonical_json,
    _source_from_inventory,
)
from src.indexing.schemas import TableParseStatus
from src.indexing.table_class_sidecar import (
    TABLE_CLASS_REGISTRY,
    TABLE_CLASS_SIDECAR_SQLITE_FILE,
    TableClassHint,
    TableClassSidecar,
    TableClassSidecarError,
    _match_entries,
    _nfkc,
    _preceding_window_bounds,
    classify_table,
    validate_table_class_sidecar,
)
from src.retrieval.corpus_candidates import candidate_from_representation_chunk
from src.retrieval.evidence import EvidenceProvenanceRepository, locate_cells
from src.retrieval.schemas import RetrievalCandidate, RetrievalCompany, RetrievalQuery
from src.supervisor.schemas import EvidenceSource, TableClass
from src.understanding.schemas import SchemaValidationError


TABLE_CLASS_PROVENANCE_AUDIT_SCHEMA_VERSION = "m2-table-class-provenance-audit-v1"
PRODUCTION_TABLE_CLASS_PROVENANCE_FLAG = "PRODUCTION_TABLE_CLASS_PROVENANCE_PENDING"

TableKey = Tuple[str, str, str]


class TableClassProvenanceAuditError(RuntimeError):
    """Hard input/compatibility failure before a trustworthy audit can run."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _identity(key: TableKey) -> Dict[str, str]:
    return {"report_id": key[0], "page_id": key[1], "table_id": key[2]}


def _sorted_identities(keys: Iterable[TableKey]) -> List[Dict[str, str]]:
    return [_identity(key) for key in sorted(set(keys))]


@dataclass(frozen=True)
class _StoredRow:
    hint_id: Any
    report_id: Any
    page_id: Any
    table_id: Any
    table_class: Any
    registry_entry_id: Any
    span_start: Any
    span_end: Any
    matched_text: Any
    canonical_json: Any
    hint: Optional[TableClassHint]

    @property
    def key(self) -> Tuple[Any, Any, Any]:
        return self.report_id, self.page_id, self.table_id


def _load_sidecar_rows(database: Path) -> List[Tuple[Any, ...]]:
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            return connection.execute(
                """SELECT hint_id, report_id, page_id, table_id, table_class,
                          registry_entry_id, matched_span_start, matched_span_end,
                          matched_text, canonical_json
                   FROM table_class_hints
                   ORDER BY report_id, page_id, table_id, hint_id"""
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise TableClassProvenanceAuditError(
            "SIDECAR_DATABASE_CORRUPT", str(database)
        ) from error


def _parse_sidecar_rows(
    rows: Sequence[Tuple[Any, ...]],
) -> Tuple[List[_StoredRow], List[str], List[str]]:
    stored: List[_StoredRow] = []
    invalid_records: List[str] = []
    canonical_failures: List[str] = []
    for row_index, row in enumerate(rows):
        label = str(row[0]) if row else f"row-{row_index}"
        hint: Optional[TableClassHint] = None
        try:
            payload = json.loads(row[9])
            hint = TableClassHint.from_dict(payload)
        except (IndexError, TypeError, ValueError, json.JSONDecodeError, SchemaValidationError):
            invalid_records.append(label)
        if hint is not None:
            expected_columns = (
                hint.hint_id,
                hint.report_id,
                hint.page_id,
                hint.table_id,
                hint.table_class.value,
                hint.registry_entry_id,
                hint.matched_source_span.start,
                hint.matched_source_span.end,
                hint.matched_text,
            )
            if tuple(row[:9]) != expected_columns:
                canonical_failures.append(label)
            elif row[9] != _canonical_json(hint.to_dict()).decode("utf-8"):
                canonical_failures.append(label)
        stored.append(_StoredRow(*row, hint=hint))
    return stored, sorted(set(invalid_records)), sorted(set(canonical_failures))


def _candidate_query(candidate: RetrievalCandidate) -> RetrievalQuery:
    return RetrievalQuery(
        raw_question="table class provenance audit",
        company=RetrievalCompany(candidate.company_name or candidate.ticker, candidate.ticker),
        periods=[],
        period_kind=None,
        statement_scope=candidate.statement_scope,
        target_metrics=[],
        derived_target=None,
        evidence_sources=[EvidenceSource.TABLE],
        requested_scale=None,
        requested_unit=None,
        query_texts=["table class provenance audit"],
    )


def _integration_audit(
    *,
    corpus_artifact_root: str | Path,
    raw_corpus_root: str | Path,
    sidecar_root: str | Path,
    candidates_by_table: Mapping[TableKey, RetrievalCandidate],
    classified_hints: Mapping[TableKey, TableClassHint],
    unmatched_keys: Sequence[TableKey],
) -> Dict[str, Any]:
    sidecar = TableClassSidecar(sidecar_root, corpus_artifact_root)
    repository = EvidenceProvenanceRepository(
        corpus_artifact_root,
        raw_corpus_root,
        table_class_sidecar=sidecar,
    )
    representative_by_class: Dict[TableClass, TableKey] = {}
    for key, hint in sorted(classified_hints.items()):
        representative_by_class.setdefault(hint.table_class, key)

    checks: List[Dict[str, Any]] = []
    failures: List[TableKey] = []
    for table_class in TableClass:
        key = representative_by_class.get(table_class)
        if key is None:
            continue
        candidate = candidates_by_table.get(key)
        passed = False
        location_count = 0
        if candidate is not None:
            try:
                locations = locate_cells(_candidate_query(candidate), [candidate], repository)
                location_count = len(locations)
                passed = bool(locations) and all(
                    location.table_class is table_class for location in locations
                )
            except (ProvenanceSidecarError, TableClassSidecarError, SchemaValidationError):
                passed = False
        checks.append(
            {
                **_identity(key),
                "expected_table_class": table_class.value,
                "location_count": location_count,
                "passed": passed,
            }
        )
        if not passed:
            failures.append(key)

    none_check: Dict[str, Any] = {"performed": False, "passed": None}
    if unmatched_keys:
        key = sorted(unmatched_keys)[0]
        candidate = candidates_by_table.get(key)
        passed = False
        location_count = 0
        if candidate is not None:
            try:
                locations = locate_cells(_candidate_query(candidate), [candidate], repository)
                location_count = len(locations)
                passed = bool(locations) and all(
                    location.table_class is None for location in locations
                )
            except (ProvenanceSidecarError, TableClassSidecarError, SchemaValidationError):
                passed = False
        none_check = {
            "performed": True,
            **_identity(key),
            "location_count": location_count,
            "passed": passed,
        }
        if not passed:
            failures.append(key)

    return {
        "representative_class_check_count": len(checks),
        "representative_class_checks": checks,
        "unclassified_none_check": none_check,
        "failure_count": len(set(failures)),
        "failures": _sorted_identities(failures),
    }


def audit_table_class_provenance(
    corpus_artifact_root: str | Path,
    table_class_sidecar_root: str | Path,
    raw_corpus_root: str | Path,
) -> Dict[str, Any]:
    """Audit one exact corpus/sidecar/source triplet and return canonical JSON data."""

    try:
        corpus = _validate_input_manifest(corpus_artifact_root, verify_hashes=True)
    except EmbeddingArtifactBuilderError as error:
        raise TableClassProvenanceAuditError("CORPUS_ARTIFACT_INVALID", str(error)) from error
    try:
        validate_table_class_sidecar(table_class_sidecar_root, corpus_artifact_root)
    except TableClassSidecarError as error:
        raise TableClassProvenanceAuditError(error.code, str(error)) from error

    sidecar_root = Path(table_class_sidecar_root)
    try:
        sidecar_manifest = json.loads((sidecar_root / "manifest.json").read_text("utf-8"))
        database = (
            sidecar_root
            / sidecar_manifest["artifact_directory"]
            / sidecar_manifest["database"]["file"]
        )
    except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError) as error:
        raise TableClassProvenanceAuditError(
            "SIDECAR_MANIFEST_INVALID", str(sidecar_root)
        ) from error
    if database.name != TABLE_CLASS_SIDECAR_SQLITE_FILE:
        raise TableClassProvenanceAuditError(
            "SIDECAR_MANIFEST_INVALID", "unexpected TableClass database filename"
        )

    raw_rows = _load_sidecar_rows(database)
    stored_rows, invalid_records, canonical_failures = _parse_sidecar_rows(raw_rows)

    corpus_table_keys: set[TableKey] = set()
    corpus_table_id_keys: Dict[str, set[TableKey]] = defaultdict(set)
    candidates_by_table: Dict[TableKey, RetrievalCandidate] = {}
    invalid_corpus_table_records: List[str] = []
    try:
        for record in _iter_input_records(corpus):
            representation = record.representation
            if representation.source_type is not EvidenceSource.TABLE:
                continue
            if representation.table_id is None or len(representation.page_ids) != 1:
                invalid_corpus_table_records.append(representation.representation_id)
                continue
            key = (
                representation.report_id,
                representation.page_ids[0],
                representation.table_id,
            )
            corpus_table_keys.add(key)
            corpus_table_id_keys[key[2]].add(key)
            candidates_by_table.setdefault(
                key,
                candidate_from_representation_chunk(representation, record.chunk),
            )
    except (EmbeddingArtifactBuilderError, SchemaValidationError) as error:
        raise TableClassProvenanceAuditError("CORPUS_ARTIFACT_INVALID", str(error)) from error

    rows_by_exact_key: Dict[Tuple[Any, Any, Any], List[_StoredRow]] = defaultdict(list)
    rows_by_table_id: Dict[Any, List[_StoredRow]] = defaultdict(list)
    for row in stored_rows:
        rows_by_exact_key[row.key].append(row)
        rows_by_table_id[row.table_id].append(row)

    duplicate_exact_keys = {
        key: values for key, values in rows_by_exact_key.items() if len(values) > 1
    }
    duplicate_table_ids = {
        table_id: values for table_id, values in rows_by_table_id.items() if len(values) > 1
    }
    conflicting_table_ids = {
        table_id: values
        for table_id, values in rows_by_table_id.items()
        if len({row.table_class for row in values}) > 1
    }
    corpus_duplicate_table_ids = {
        table_id: keys for table_id, keys in corpus_table_id_keys.items() if len(keys) > 1
    }

    canonical_failure_ids = set(canonical_failures)
    valid_hints: Dict[TableKey, TableClassHint] = {}
    for row in stored_rows:
        if row.hint is None or not all(isinstance(item, str) for item in row.key):
            continue
        key = (row.report_id, row.page_id, row.table_id)
        if len(rows_by_exact_key[row.key]) == 1 and str(row.hint_id) not in canonical_failure_ids:
            valid_hints[key] = row.hint

    rows_by_report: Dict[str, List[_StoredRow]] = defaultdict(list)
    for row in stored_rows:
        if isinstance(row.report_id, str):
            rows_by_report[row.report_id].append(row)

    source_table_keys: set[TableKey] = set()
    expected_hints: Dict[TableKey, TableClassHint] = {}
    invalid_spans: List[str] = []
    span_round_trip_failures: List[str] = []
    registry_source_failures: List[str] = []
    ambiguous_conflict_keys: List[TableKey] = []
    source_no_match_keys: List[TableKey] = []
    unparseable_table_count = 0
    registry_by_id = {entry.entry_id: entry for entry in TABLE_CLASS_REGISTRY}

    for report in corpus.manifest["reports"]:
        try:
            source = _source_from_inventory(Path(raw_corpus_root), corpus.corpus_id, report)
            document = parse_document(source)
        except ProvenanceSidecarError as error:
            raise TableClassProvenanceAuditError(error.code, str(error)) from error
        except SchemaValidationError as error:
            raise TableClassProvenanceAuditError(
                "SOURCE_PARSE_FAILED", str(report.get("source_ref"))
            ) from error
        for page in document.pages:
            for table in page.tables:
                if table.parse_status is TableParseStatus.UNPARSEABLE:
                    unparseable_table_count += 1
                    continue
                key = (source.report_id, page.page_id, table.table_id)
                source_table_keys.add(key)
                start, end = _preceding_window_bounds(page, table)
                matches = _match_entries(source.raw_text, start, end) if end > start else []
                distinct_classes = {match.entry.table_class for match in matches}
                if not matches:
                    source_no_match_keys.append(key)
                elif len(distinct_classes) > 1:
                    ambiguous_conflict_keys.append(key)
                else:
                    hint = classify_table(source.report_id, source.raw_text, page, table)
                    if hint is None:
                        raise TableClassProvenanceAuditError(
                            "CLASSIFIER_AUDIT_INCONSISTENT", repr(key)
                        )
                    expected_hints[key] = hint

        for row in rows_by_report.get(source.report_id, []):
            label = str(row.hint_id)
            if (
                isinstance(row.span_start, bool)
                or not isinstance(row.span_start, int)
                or isinstance(row.span_end, bool)
                or not isinstance(row.span_end, int)
                or row.span_start < 0
                or row.span_end <= row.span_start
                or row.span_end > len(source.raw_text)
            ):
                invalid_spans.append(label)
                continue
            raw_slice = source.raw_text[row.span_start : row.span_end]
            if not isinstance(row.matched_text, str) or raw_slice != row.matched_text:
                span_round_trip_failures.append(label)
            hint = row.hint
            if hint is None:
                continue
            entry = registry_by_id.get(hint.registry_entry_id)
            if (
                entry is None
                or entry.table_class is not hint.table_class
                or entry.search(_nfkc(raw_slice)) is None
            ):
                registry_source_failures.append(hint.hint_id)

    stored_string_keys = {
        (row.report_id, row.page_id, row.table_id)
        for row in stored_rows
        if all(isinstance(item, str) for item in row.key)
    }
    orphan_keys = stored_string_keys - corpus_table_keys
    source_hint_mismatches = {
        key
        for key in set(expected_hints) | set(valid_hints)
        if key not in expected_hints
        or key not in valid_hints
        or expected_hints[key].to_dict() != valid_hints[key].to_dict()
    }
    corpus_source_missing = corpus_table_keys - source_table_keys
    source_corpus_missing = source_table_keys - corpus_table_keys
    classified_hints = {
        key: hint
        for key, hint in valid_hints.items()
        if key in corpus_table_keys
    }
    unmatched_keys = sorted(corpus_table_keys - set(classified_hints))

    try:
        integration = _integration_audit(
            corpus_artifact_root=corpus_artifact_root,
            raw_corpus_root=raw_corpus_root,
            sidecar_root=table_class_sidecar_root,
            candidates_by_table=candidates_by_table,
            classified_hints=classified_hints,
            unmatched_keys=unmatched_keys,
        )
    except (ProvenanceSidecarError, TableClassSidecarError, SchemaValidationError) as error:
        raise TableClassProvenanceAuditError(
            "LOCATE_CELLS_AUDIT_SETUP_FAILED", str(error)
        ) from error

    counts = Counter(hint.table_class.value for hint in classified_hints.values())
    blockers: List[str] = []
    checks = (
        (not corpus_table_keys, "NO_CANONICAL_TABLES"),
        (not classified_hints, "NO_SOURCE_BACKED_CLASSIFICATIONS"),
        (bool(invalid_corpus_table_records), "INVALID_CORPUS_TABLE_PROVENANCE"),
        (bool(corpus_duplicate_table_ids), "DUPLICATE_CORPUS_TABLE_ID_MAPPING"),
        (bool(corpus_source_missing or source_corpus_missing), "CORPUS_SOURCE_TABLE_MISMATCH"),
        (bool(invalid_records), "INVALID_SIDECAR_HINT_RECORD"),
        (bool(canonical_failures), "SIDECAR_CANONICAL_ROUND_TRIP_FAILED"),
        (bool(duplicate_exact_keys or duplicate_table_ids), "DUPLICATE_SIDECAR_TABLE_ID_MAPPING"),
        (bool(conflicting_table_ids), "CONFLICTING_SIDECAR_TABLE_ID_MAPPING"),
        (bool(orphan_keys), "ORPHAN_SIDECAR_HINT"),
        (bool(invalid_spans), "INVALID_SOURCE_SPAN"),
        (bool(span_round_trip_failures), "SOURCE_SPAN_ROUND_TRIP_FAILED"),
        (bool(registry_source_failures), "REGISTRY_SOURCE_MATCH_FAILED"),
        (bool(source_hint_mismatches), "SOURCE_SUPPORTED_HINT_MISMATCH"),
        (integration["failure_count"] > 0, "LOCATE_CELLS_INTEGRATION_FAILED"),
    )
    blockers.extend(code for failed, code in checks if failed)

    total_tables = len(corpus_table_keys)
    total_classified = len(classified_hints)
    return {
        "schema_version": TABLE_CLASS_PROVENANCE_AUDIT_SCHEMA_VERSION,
        "status": "PASS" if not blockers else "BLOCKED",
        "production_flag": PRODUCTION_TABLE_CLASS_PROVENANCE_FLAG,
        "production_flag_clearance": (
            "ELIGIBLE_IF_INPUTS_ARE_APPROVED_CANONICAL_FULL_M2"
            if not blockers
            else "BLOCKED"
        ),
        "blockers": blockers,
        "compatibility": {
            "status": "PASS",
            "corpus_artifact_id": corpus.artifact_id,
            "corpus_fingerprint": corpus.corpus_fingerprint,
            "corpus_manifest_sha256": corpus.manifest_sha256,
            "sidecar_artifact_id": sidecar_manifest["artifact_id"],
            "sidecar_schema_version": sidecar_manifest["sidecar_schema_version"],
            "sidecar_build_version": sidecar_manifest["build_version"],
            "registry_version": sidecar_manifest["registry_version"],
            "sidecar_corpus_artifact_id": sidecar_manifest["corpus_artifact_id"],
            "sidecar_corpus_fingerprint": sidecar_manifest["corpus_fingerprint"],
            "sidecar_corpus_manifest_sha256": sidecar_manifest["corpus_manifest_sha256"],
            "registry_fingerprint": sidecar_manifest["registry_fingerprint"],
        },
        "total_reports": len(corpus.manifest["reports"]),
        "total_tables": total_tables,
        "source_unparseable_tables": unparseable_table_count,
        "total_classified_tables": total_classified,
        "classification_coverage": (
            total_classified / total_tables if total_tables else 0.0
        ),
        "counts_per_table_class": {
            table_class.value: counts.get(table_class.value, 0)
            for table_class in TableClass
        },
        "unmatched_table_count": len(unmatched_keys),
        "unmatched_tables": _sorted_identities(unmatched_keys),
        "source_no_match_count": len(source_no_match_keys),
        "ambiguous_conflict_count_available": True,
        "ambiguous_conflict_count": len(ambiguous_conflict_keys),
        "ambiguous_conflict_tables": _sorted_identities(ambiguous_conflict_keys),
        "orphan_sidecar_hint_count": len(orphan_keys),
        "orphan_sidecar_hints": _sorted_identities(orphan_keys),
        "duplicate_table_id_mapping_count": (
            len(duplicate_table_ids) + len(corpus_duplicate_table_ids)
        ),
        "duplicate_exact_table_mapping_count": len(duplicate_exact_keys),
        "conflicting_table_id_mapping_count": len(conflicting_table_ids),
        "duplicate_conflicting_table_ids": sorted(
            {str(key) for key in duplicate_table_ids}
            | {str(key) for key in conflicting_table_ids}
            | set(corpus_duplicate_table_ids)
        ),
        "invalid_sidecar_hint_record_count": len(invalid_records),
        "invalid_sidecar_hint_records": invalid_records,
        "sidecar_canonical_round_trip_failure_count": len(canonical_failures),
        "sidecar_canonical_round_trip_failures": canonical_failures,
        "invalid_source_span_count": len(set(invalid_spans)),
        "invalid_source_spans": sorted(set(invalid_spans)),
        "exact_source_span_round_trip_failure_count": len(
            set(span_round_trip_failures)
        ),
        "exact_source_span_round_trip_failures": sorted(
            set(span_round_trip_failures)
        ),
        "registry_source_match_failure_count": len(set(registry_source_failures)),
        "registry_source_match_failures": sorted(set(registry_source_failures)),
        "source_supported_hint_mismatch_count": len(source_hint_mismatches),
        "source_supported_hint_mismatches": _sorted_identities(source_hint_mismatches),
        "corpus_source_table_mismatch_count": len(corpus_source_missing)
        + len(source_corpus_missing),
        "corpus_tables_missing_from_source": _sorted_identities(corpus_source_missing),
        "source_tables_missing_from_corpus": _sorted_identities(source_corpus_missing),
        "production_evidence_path": integration,
        "acceptance": {
            "requires_exact_compatibility": True,
            "requires_zero_integrity_and_mapping_failures": True,
            "requires_source_supported_hints_to_round_trip_exactly": True,
            "requires_locate_cells_integration": True,
            "requires_full_classification_coverage": False,
            "unmatched_or_ambiguous_tables_may_remain_none": True,
            "clear_only_after_pass_on_approved_canonical_full_m2_corpus": True,
        },
    }


def _hard_failure_payload(error: TableClassProvenanceAuditError) -> Dict[str, Any]:
    return {
        "schema_version": TABLE_CLASS_PROVENANCE_AUDIT_SCHEMA_VERSION,
        "status": "BLOCKED",
        "production_flag": PRODUCTION_TABLE_CLASS_PROVENANCE_FLAG,
        "production_flag_clearance": "BLOCKED",
        "blockers": [error.code],
        "error": {"code": error.code, "message": str(error)},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit production TableClass provenance against exact M2 source"
    )
    parser.add_argument("--corpus-artifact", required=True)
    parser.add_argument("--table-class-sidecar", required=True)
    parser.add_argument("--raw-corpus-root", required=True)
    arguments = parser.parse_args(argv)
    try:
        report = audit_table_class_provenance(
            arguments.corpus_artifact,
            arguments.table_class_sidecar,
            arguments.raw_corpus_root,
        )
    except TableClassProvenanceAuditError as error:
        print(json.dumps(_hard_failure_payload(error), ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
