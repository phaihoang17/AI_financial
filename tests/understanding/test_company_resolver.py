from dataclasses import fields
import unittest

from src.understanding.company_resolver import (
    CompanyAlias,
    CompanyCandidate,
    CompanyResolution,
    CompanyResolutionStatus,
    resolve_company,
)
from src.understanding.schemas import CompanyUnderstanding, SchemaValidationError


def aaa_aliases():
    return [
        CompanyAlias(alias="Nhựa An Phát", name="An Phát Bioplastics", ticker="AAA"),
        CompanyAlias(alias="An Phát", name="An Phát Bioplastics", ticker="AAA"),
    ]


class CompanyResolverContractTests(unittest.TestCase):
    def test_fields_match_canonical_contracts(self):
        self.assertEqual(
            [field.name for field in fields(CompanyAlias)],
            ["alias", "name", "ticker"],
        )
        self.assertEqual(
            [field.name for field in fields(CompanyCandidate)], ["name", "ticker"]
        )
        self.assertEqual(
            [field.name for field in fields(CompanyResolution)],
            ["status", "company", "candidates"],
        )

    def test_alias_and_candidate_require_non_empty_exact_fields(self):
        with self.assertRaises(SchemaValidationError):
            CompanyAlias(alias=" ", name="Company", ticker="AAA")
        with self.assertRaises(SchemaValidationError):
            CompanyCandidate(name="Company", ticker="")
        with self.assertRaises(SchemaValidationError):
            CompanyAlias.from_dict(
                {"alias": "AAA", "name": "Company", "ticker": "AAA", "extra": 1}
            )

    def test_resolution_status_invariants_are_enforced(self):
        company = CompanyUnderstanding(
            raw="AAA", name=None, ticker=None, confidence=0.0
        )
        candidate = CompanyCandidate(name="Company", ticker="AAA")

        with self.assertRaises(SchemaValidationError):
            CompanyResolution(
                status=CompanyResolutionStatus.RESOLVED,
                company=company,
                candidates=[candidate],
            )
        with self.assertRaises(SchemaValidationError):
            CompanyResolution(
                status=CompanyResolutionStatus.AMBIGUOUS,
                company=company,
                candidates=[candidate],
            )
        with self.assertRaises(SchemaValidationError):
            CompanyResolution(
                status=CompanyResolutionStatus.UNRESOLVED,
                company=company,
                candidates=[candidate],
            )

    def test_invalid_status_and_unknown_fields_are_rejected(self):
        payload = {
            "status": "MISSING",
            "company": {
                "raw": "unknown",
                "name": None,
                "ticker": None,
                "confidence": 0.0,
            },
            "candidates": [],
        }
        with self.assertRaises(SchemaValidationError):
            CompanyResolution.from_dict(payload)

        payload["status"] = "UNRESOLVED"
        payload["score"] = 0.0
        with self.assertRaises(SchemaValidationError):
            CompanyResolution.from_dict(payload)


class CompanyResolverTests(unittest.TestCase):
    def test_alias_matching_trims_and_is_case_insensitive(self):
        result = resolve_company("  NHỰA AN PHÁT  ", aaa_aliases())

        self.assertEqual(result.status, CompanyResolutionStatus.RESOLVED)
        self.assertEqual(result.company.raw, "  NHỰA AN PHÁT  ")
        self.assertEqual(result.company.name, "An Phát Bioplastics")
        self.assertEqual(result.company.ticker, "AAA")
        self.assertEqual(result.company.confidence, 1.0)

    def test_ticker_matching_is_case_insensitive(self):
        result = resolve_company(" aaa ", aaa_aliases())

        self.assertEqual(result.status, CompanyResolutionStatus.RESOLVED)
        self.assertEqual(result.company.ticker, "AAA")

    def test_duplicate_alias_records_for_one_company_are_not_ambiguous(self):
        aliases = aaa_aliases() + [
            CompanyAlias(alias="AAA", name="An Phát Bioplastics", ticker="AAA")
        ]

        result = resolve_company("AAA", aliases)

        self.assertEqual(result.status, CompanyResolutionStatus.RESOLVED)
        self.assertEqual(len(result.candidates), 1)

    def test_multiple_canonical_matches_are_ambiguous(self):
        aliases = [
            CompanyAlias(alias="ABC", name="Company One", ticker="ONE"),
            CompanyAlias(alias="abc", name="Company Two", ticker="TWO"),
        ]

        result = resolve_company(" AbC ", aliases)

        self.assertEqual(result.status, CompanyResolutionStatus.AMBIGUOUS)
        self.assertIsNone(result.company.name)
        self.assertIsNone(result.company.ticker)
        self.assertEqual(result.company.confidence, 0.0)
        self.assertEqual(
            [candidate.ticker for candidate in result.candidates], ["ONE", "TWO"]
        )

    def test_no_match_is_unresolved(self):
        result = resolve_company("Unknown", aaa_aliases())

        self.assertEqual(result.status, CompanyResolutionStatus.UNRESOLVED)
        self.assertEqual(result.candidates, [])
        self.assertIsNone(result.company.name)
        self.assertIsNone(result.company.ticker)
        self.assertEqual(result.company.confidence, 0.0)

    def test_no_fuzzy_typo_or_substring_matching(self):
        for query in ("AA", "AAAA", "Nhua An Phat", "An Phá"):
            with self.subTest(query=query):
                self.assertEqual(
                    resolve_company(query, aaa_aliases()).status,
                    CompanyResolutionStatus.UNRESOLVED,
                )

    def test_resolution_is_deterministic_and_serializable(self):
        aliases = [
            CompanyAlias(alias="shared", name="Second", ticker="ZZZ"),
            CompanyAlias(alias="SHARED", name="First", ticker="AAA"),
        ]

        first = resolve_company("shared", aliases).to_dict()
        second = resolve_company("shared", list(reversed(aliases))).to_dict()

        self.assertEqual(first, second)
        self.assertEqual(CompanyResolution.from_dict(first).to_dict(), first)
        self.assertEqual(list(first), ["status", "company", "candidates"])

    def test_resolver_rejects_invalid_input_types(self):
        with self.assertRaises(SchemaValidationError):
            resolve_company(True, aaa_aliases())
        with self.assertRaises(SchemaValidationError):
            resolve_company("AAA", [aaa_aliases()[0].to_dict()])


if __name__ == "__main__":
    unittest.main()
