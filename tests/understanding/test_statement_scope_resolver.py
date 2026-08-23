from dataclasses import fields
import unittest

from src.understanding.schemas import (
    SchemaValidationError,
    StatementScope,
    StatementScopeUnderstanding,
)
from src.understanding.statement_scope_resolver import (
    StatementScopeResolution,
    StatementScopeResolutionStatus,
    StatementScopeResolverInput,
    resolve_statement_scope,
)


class StatementScopeContractTests(unittest.TestCase):
    def test_fields_match_canonical_contracts(self):
        self.assertEqual(
            [field.name for field in fields(StatementScopeResolverInput)],
            ["text", "context_scope"],
        )
        self.assertEqual(
            [field.name for field in fields(StatementScopeResolution)],
            ["status", "statement_scope"],
        )

    def test_invalid_context_and_unknown_fields_are_rejected(self):
        with self.assertRaises(SchemaValidationError):
            StatementScopeResolverInput.from_dict(
                {"text": "", "context_scope": "AGGREGATED"}
            )
        with self.assertRaises(SchemaValidationError):
            StatementScopeResolverInput.from_dict(
                {"text": "", "context_scope": None, "default": "RIENG"}
            )

    def test_resolution_invariants_are_enforced(self):
        with self.assertRaises(SchemaValidationError):
            StatementScopeResolution(
                status=StatementScopeResolutionStatus.RESOLVED,
                statement_scope=StatementScopeUnderstanding(
                    value=None, inferred=False, confidence=0.0
                ),
            )
        with self.assertRaises(SchemaValidationError):
            StatementScopeResolution(
                status=StatementScopeResolutionStatus.UNRESOLVED,
                statement_scope=StatementScopeUnderstanding(
                    value=StatementScope.RIENG,
                    inferred=False,
                    confidence=1.0,
                ),
            )


class StatementScopeResolverTests(unittest.TestCase):
    def test_hop_nhat_indicators(self):
        for text in (
            "hợp nhất",
            "Báo cáo tài chính HỢP NHẤT",
            "BCTC hợp nhất",
        ):
            with self.subTest(text=text):
                result = resolve_statement_scope(
                    StatementScopeResolverInput(text=text, context_scope=None)
                )
                self.assertEqual(result.status, StatementScopeResolutionStatus.RESOLVED)
                self.assertEqual(result.statement_scope.value, StatementScope.HOP_NHAT)
                self.assertFalse(result.statement_scope.inferred)

    def test_rieng_indicators(self):
        for text in (
            "riêng",
            "báo cáo tài chính riêng lẻ",
            "BCTC riêng",
            "số liệu công ty mẹ",
        ):
            with self.subTest(text=text):
                result = resolve_statement_scope(
                    StatementScopeResolverInput(text=text, context_scope=None)
                )
                self.assertEqual(result.status, StatementScopeResolutionStatus.RESOLVED)
                self.assertEqual(result.statement_scope.value, StatementScope.RIENG)

    def test_multiple_same_scope_indicators_are_not_ambiguous(self):
        result = resolve_statement_scope(
            StatementScopeResolverInput(
                text="BCTC hợp nhất, báo cáo tài chính hợp nhất",
                context_scope=None,
            )
        )

        self.assertEqual(result.status, StatementScopeResolutionStatus.RESOLVED)
        self.assertEqual(result.statement_scope.value, StatementScope.HOP_NHAT)

    def test_both_scope_classes_are_ambiguous(self):
        result = resolve_statement_scope(
            StatementScopeResolverInput(
                text="so sánh BCTC hợp nhất và công ty mẹ",
                context_scope=StatementScope.HOP_NHAT,
            )
        )

        self.assertEqual(result.status, StatementScopeResolutionStatus.AMBIGUOUS)
        self.assertIsNone(result.statement_scope.value)
        self.assertFalse(result.statement_scope.inferred)
        self.assertEqual(result.statement_scope.confidence, 0.0)

    def test_explicit_scope_overrides_context(self):
        result = resolve_statement_scope(
            StatementScopeResolverInput(
                text="BCTC riêng", context_scope=StatementScope.HOP_NHAT
            )
        )

        self.assertEqual(result.statement_scope.value, StatementScope.RIENG)
        self.assertFalse(result.statement_scope.inferred)
        self.assertEqual(result.statement_scope.confidence, 1.0)

    def test_structured_context_is_marked_inferred(self):
        result = resolve_statement_scope(
            StatementScopeResolverInput(
                text="LNST năm 2015", context_scope=StatementScope.HOP_NHAT
            )
        )

        self.assertEqual(result.status, StatementScopeResolutionStatus.RESOLVED)
        self.assertEqual(result.statement_scope.value, StatementScope.HOP_NHAT)
        self.assertTrue(result.statement_scope.inferred)
        self.assertEqual(result.statement_scope.confidence, 1.0)

    def test_no_indicator_or_context_is_unresolved(self):
        result = resolve_statement_scope(
            StatementScopeResolverInput(text="LNST năm 2015", context_scope=None)
        )

        self.assertEqual(result.status, StatementScopeResolutionStatus.UNRESOLVED)
        self.assertIsNone(result.statement_scope.value)
        self.assertEqual(result.statement_scope.confidence, 0.0)

    def test_aggregated_and_unlabeled_reports_are_not_inferred(self):
        for text in ("báo cáo tổng hợp", "báo cáo tài chính"):
            with self.subTest(text=text):
                result = resolve_statement_scope(
                    StatementScopeResolverInput(text=text, context_scope=None)
                )
                self.assertEqual(
                    result.status, StatementScopeResolutionStatus.UNRESOLVED
                )
                self.assertIsNone(result.statement_scope.value)

    def test_unicode_whitespace_and_case_normalization(self):
        result = resolve_statement_scope(
            StatementScopeResolverInput(
                text="  BÁO CÁO\u00a0TÀI CHÍNH   HỢP NHẤT  ", context_scope=None
            )
        )
        self.assertEqual(result.statement_scope.value, StatementScope.HOP_NHAT)

    def test_serialization_round_trip_is_deterministic(self):
        result = resolve_statement_scope(
            StatementScopeResolverInput(
                text="", context_scope=StatementScope.RIENG
            )
        )
        payload = result.to_dict()

        self.assertEqual(list(payload), ["status", "statement_scope"])
        self.assertEqual(
            StatementScopeResolution.from_dict(payload).to_dict(), payload
        )


if __name__ == "__main__":
    unittest.main()
