import unittest

from src.evaluation.program_trace import (
    compare_program_traces,
    normalized_program_equivalence,
)
from src.evaluation.reasoning import TraceComparison
from src.numeric.m2_adapter import parse_canonical_decimal
from src.numeric.scale_conversion import ScaleConversionStatus, convert_canonical_scale
from src.evidence.schemas import Scale
from src.programmer.generator import generate_program
from src.programmer.schemas import ProgrammerStatus
from tests.programmer.helpers import growth_programmer_input, valid_growth_program


class M6Batch3IntegrationTests(unittest.TestCase):
    def test_numeric_boundaries_remain_separate_from_symbolic_program(self):
        programmer_input = growth_programmer_input()
        before = generate_program(programmer_input)
        self.assertIs(before.status, ProgrammerStatus.GENERATED)

        parsed = parse_canonical_decimal("1.234.567,89")
        converted = convert_canonical_scale(
            parsed,
            source_scale=Scale.THOUSAND,
            requested_output_scale=Scale.MILLION,
        )
        self.assertIs(converted.status, ScaleConversionStatus.SUCCESS)
        self.assertEqual(converted.canonical_value, "1234.56789")

        after = generate_program(programmer_input)
        self.assertEqual(before.program, after.program)
        self.assertNotIn(str(parsed), after.program.canonical_json())
        self.assertNotIn(str(converted.canonical_value), after.program.canonical_json())
        self.assertIs(
            compare_program_traces(
                valid_growth_program(programmer_input),
                after.program,
                normalized_program_equivalence,
            ),
            TraceComparison.NORMALIZED_EQUIVALENT,
        )


if __name__ == "__main__":
    unittest.main()
