import unittest

from src.evidence.numeric_masking import mask_numeric_evidence
from src.evidence.schema_linker import link_schema
from src.programmer.generator import generate_program
from src.programmer.schemas import (
    ProgramOperation,
    ProgrammerInput,
    ProgrammerStatus,
)
from src.programmer.validator import validate_program
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from tests.evidence.m5_helpers import (
    grounded_cell,
    multi_period_plan,
    resolved_scale,
)


class M5ToProgrammerIntegrationTests(unittest.TestCase):
    def test_real_numeric_masking_output_generates_value_free_growth_program(self):
        plan = multi_period_plan()
        item_2015, location_2015 = grounded_cell(
            "2015",
            column_labels=("2015",),
            raw_value="99.999.999",
            decimal_value="99999999",
        )
        item_2014, location_2014 = grounded_cell(
            "2014",
            column_labels=("2014",),
            raw_value="12.345.678",
            decimal_value="12345678",
        )
        evidence = [item_2015, item_2014]
        locations = {
            item_2015.evidence_id: location_2015,
            item_2014.evidence_id: location_2014,
        }
        scales = [resolved_scale(item_2015), resolved_scale(item_2014)]
        links = link_schema(plan, evidence, locations)
        masked = mask_numeric_evidence(
            plan, evidence, locations, links, scales
        )
        programmer_input = ProgrammerInput(
            plan=plan,
            masked_evidence=masked,
            formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
        )

        result = generate_program(programmer_input)
        self.assertIs(result.status, ProgrammerStatus.GENERATED)
        self.assertIsNotNone(result.program)
        program = result.program
        self.assertIs(validate_program(program, programmer_input), program)
        self.assertIs(
            program.steps[0].operation,
            ProgramOperation.APPLY_REGISTERED_FORMULA,
        )
        self.assertEqual(
            [item.evidence_id for item in program.inputs],
            [item_2014.evidence_id, item_2015.evidence_id],
        )
        serialized = program.canonical_json()
        for value in (
            "99.999.999",
            "99999999",
            "12.345.678",
            "12345678",
        ):
            self.assertNotIn(value, serialized)


if __name__ == "__main__":
    unittest.main()
