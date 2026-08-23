from dataclasses import fields
import unittest

from src.understanding.metric_normalizer import (
    CanonicalMetric,
    MetricResolution,
    MetricResolutionStatus,
    MetricSynonym,
    initial_metric_registry,
    resolve_metric,
)
from src.understanding.schemas import MetricUnderstanding, SchemaValidationError


class MetricNormalizerContractTests(unittest.TestCase):
    def test_fields_match_canonical_contracts(self):
        self.assertEqual(
            [field.name for field in fields(CanonicalMetric)], ["canonical"]
        )
        self.assertEqual(
            [field.name for field in fields(MetricSynonym)],
            ["synonym", "canonical"],
        )
        self.assertEqual(
            [field.name for field in fields(MetricResolution)],
            ["status", "raw", "metric", "candidates", "confidence"],
        )

    def test_registry_records_require_non_empty_exact_fields(self):
        with self.assertRaises(SchemaValidationError):
            CanonicalMetric(canonical=" ")
        with self.assertRaises(SchemaValidationError):
            MetricSynonym(synonym="", canonical="LNST")
        with self.assertRaises(SchemaValidationError):
            MetricSynonym.from_dict(
                {"synonym": "LNST", "canonical": "LNST", "extra": True}
            )

    def test_resolution_invariants_are_enforced(self):
        candidate = CanonicalMetric(canonical="LNST")
        with self.assertRaises(SchemaValidationError):
            MetricResolution(
                status=MetricResolutionStatus.RESOLVED,
                raw="LNST",
                metric=None,
                candidates=[candidate],
                confidence=1.0,
            )
        with self.assertRaises(SchemaValidationError):
            MetricResolution(
                status=MetricResolutionStatus.UNRESOLVED,
                raw="unknown",
                metric=MetricUnderstanding(
                    raw="unknown", canonical="LNST", confidence=1.0
                ),
                candidates=[],
                confidence=0.0,
            )


class MetricNormalizerTests(unittest.TestCase):
    def test_initial_registry_contains_only_documented_examples(self):
        self.assertEqual(
            [record.to_dict() for record in initial_metric_registry()],
            [
                {"synonym": "LNST", "canonical": "LNST"},
                {"synonym": "lãi ròng", "canonical": "LNST"},
                {"synonym": "lợi nhuận sau thuế", "canonical": "LNST"},
            ],
        )

    def test_all_documented_synonyms_resolve_to_lnst(self):
        for raw in ("LNST", "lãi ròng", "lợi nhuận sau thuế"):
            with self.subTest(raw=raw):
                result = resolve_metric(raw, initial_metric_registry())
                self.assertEqual(result.status, MetricResolutionStatus.RESOLVED)
                self.assertEqual(result.metric.canonical, "LNST")
                self.assertEqual(result.metric.raw, raw)
                self.assertEqual(result.metric.confidence, 1.0)
                self.assertEqual(result.confidence, 1.0)

    def test_unicode_whitespace_and_case_normalization(self):
        for raw in ("  lÃi\u00a0rÒng  ", "ＬＮＳＴ"):
            with self.subTest(raw=raw):
                result = resolve_metric(raw, initial_metric_registry())
                self.assertEqual(result.status, MetricResolutionStatus.RESOLVED)
                self.assertEqual(result.metric.canonical, "LNST")
                self.assertEqual(result.metric.raw, raw)

    def test_multiple_records_for_same_canonical_metric_remain_resolved(self):
        registry = [
            MetricSynonym(synonym="LNST", canonical="LNST"),
            MetricSynonym(synonym="lnst", canonical="lnst"),
        ]

        result = resolve_metric("LNST", registry)

        self.assertEqual(result.status, MetricResolutionStatus.RESOLVED)
        self.assertEqual(len(result.candidates), 1)

    def test_same_normalized_synonym_for_distinct_metrics_is_ambiguous(self):
        registry = [
            MetricSynonym(synonym="lãi", canonical="LNST"),
            MetricSynonym(synonym="LÃI", canonical="LOI_NHUAN_GOP"),
        ]

        result = resolve_metric("lãi", registry)

        self.assertEqual(result.status, MetricResolutionStatus.AMBIGUOUS)
        self.assertIsNone(result.metric)
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(
            [candidate.canonical for candidate in result.candidates],
            ["LNST", "LOI_NHUAN_GOP"],
        )

    def test_unknown_metric_is_unresolved(self):
        result = resolve_metric("ROE", initial_metric_registry())

        self.assertEqual(result.status, MetricResolutionStatus.UNRESOLVED)
        self.assertIsNone(result.metric)
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.confidence, 0.0)

    def test_no_fuzzy_typo_substring_or_diacritic_removal(self):
        for raw in ("LNSTT", "LNST điều chỉnh", "lai rong", "lãi"):
            with self.subTest(raw=raw):
                self.assertEqual(
                    resolve_metric(raw, initial_metric_registry()).status,
                    MetricResolutionStatus.UNRESOLVED,
                )

    def test_candidate_deduplication_is_deterministic(self):
        registry = [
            MetricSynonym(synonym="x", canonical="ZZZ"),
            MetricSynonym(synonym="X", canonical="AAA"),
            MetricSynonym(synonym="x", canonical="aaa"),
        ]

        first = resolve_metric("x", registry).to_dict()
        second = resolve_metric("x", list(reversed(registry))).to_dict()

        self.assertEqual(first, second)
        self.assertEqual(
            [candidate["canonical"] for candidate in first["candidates"]],
            ["AAA", "ZZZ"],
        )

    def test_serialization_round_trip_is_deterministic(self):
        result = resolve_metric("lãi ròng", initial_metric_registry())
        payload = result.to_dict()

        self.assertEqual(
            list(payload), ["status", "raw", "metric", "candidates", "confidence"]
        )
        self.assertEqual(MetricResolution.from_dict(payload).to_dict(), payload)

    def test_resolver_rejects_invalid_input_types(self):
        with self.assertRaises(SchemaValidationError):
            resolve_metric(True, initial_metric_registry())
        with self.assertRaises(SchemaValidationError):
            resolve_metric("LNST", [initial_metric_registry()[0].to_dict()])


if __name__ == "__main__":
    unittest.main()
