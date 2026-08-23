import unittest

from src.understanding.operation_detector import (
    OperationDetection,
    OperationDetectorInput,
    detect_operation,
)
from src.understanding.schemas import Operation, SchemaValidationError


class OperationDetectorTests(unittest.TestCase):
    def operation_for(self, text):
        return detect_operation(OperationDetectorInput(text=text)).operation

    def test_contract_fields_and_round_trip(self):
        detector_input = OperationDetectorInput.from_dict({"text": "ROE của AAA"})
        self.assertEqual(detector_input.to_dict(), {"text": "ROE của AAA"})

        detection = OperationDetection.from_dict({"operation": "ratio"})
        self.assertEqual(detection.operation, Operation.RATIO)
        self.assertEqual(detection.to_dict(), {"operation": "ratio"})

        with self.assertRaises(SchemaValidationError):
            OperationDetectorInput.from_dict({"text": "ROE", "extra": True})
        with self.assertRaises(SchemaValidationError):
            OperationDetection.from_dict({"operation": "unsupported"})

    def test_empty_input_is_unknown(self):
        self.assertEqual(self.operation_for(""), Operation.UNKNOWN)
        self.assertEqual(self.operation_for(" \t\n "), Operation.UNKNOWN)

    def test_no_indicator_is_none(self):
        self.assertEqual(
            self.operation_for("LNST của AAA năm 2015 là bao nhiêu?"),
            Operation.NONE,
        )

    def test_minimal_vocabulary_detects_each_operation(self):
        for text in ("ROE của AAA", "Tỷ lệ nợ", "tỷ suất sinh lời"):
            self.assertEqual(self.operation_for(text), Operation.RATIO)
        self.assertEqual(
            self.operation_for("Doanh thu tăng bao nhiêu %?"), Operation.GROWTH
        )
        self.assertEqual(
            self.operation_for("Trung bình LNST ba năm"), Operation.AGGREGATE
        )
        for text in (
            "So sánh doanh thu hai năm",
            "Doanh thu so với năm trước",
            "Doanh thu từ 2014 sang 2015",
        ):
            self.assertEqual(self.operation_for(text), Operation.COMPARE)

    def test_documented_growth_form_overrides_period_compare_structure(self):
        self.assertEqual(
            self.operation_for(
                "Doanh thu tăng bao nhiêu % từ 2014 sang 2015?"
            ),
            Operation.GROWTH,
        )

    def test_every_other_cross_operation_conflict_is_unknown(self):
        conflicting = (
            "ROE so với năm trước",
            "Trung bình ROE",
            "Tăng bao nhiêu % so với năm trước",
            "Trung bình doanh thu từ 2014 sang 2015",
        )
        for text in conflicting:
            with self.subTest(text=text):
                self.assertEqual(self.operation_for(text), Operation.UNKNOWN)

    def test_multiple_indicators_for_one_operation_remain_resolved(self):
        self.assertEqual(
            self.operation_for("ROE và tỷ suất sinh lời"), Operation.RATIO
        )
        self.assertEqual(
            self.operation_for("So sánh doanh thu so với năm trước"),
            Operation.COMPARE,
        )

    def test_unicode_whitespace_and_case_are_normalized(self):
        self.assertEqual(
            self.operation_for("  TỶ\u3000SUẤT  sinh lời "), Operation.RATIO
        )

    def test_no_fuzzy_typo_or_substring_matching(self):
        for text in ("ROEE của AAA", "tỉ suất sinh lời", "trungbình LNST"):
            with self.subTest(text=text):
                self.assertEqual(self.operation_for(text), Operation.NONE)

    def test_detector_rejects_invalid_input_type(self):
        with self.assertRaises(SchemaValidationError):
            detect_operation("ROE")


if __name__ == "__main__":
    unittest.main()
