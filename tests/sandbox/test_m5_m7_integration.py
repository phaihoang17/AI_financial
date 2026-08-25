import unittest

from src.evidence.numeric_masking import mask_numeric_evidence
from src.evidence.schema_linker import link_schema
from src.evidence.schemas import Scale
from src.evidence.value_binder import build_binding_map
from src.programmer.generator import generate_program
from src.programmer.schemas import ProgrammerInput, ProgrammerStatus
from src.sandbox.executor import execute_sandboxed
from src.sandbox.schemas import (
    EXECUTION_LIMITS_PROFILE_ID,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    SandboxExecutionRequest,
)
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from tests.evidence.m5_helpers import (
    grounded_cell,
    multi_period_plan,
    resolved_scale,
)


class M5ToIsolatedM7IntegrationTests(unittest.TestCase):
    def test_grounded_values_bind_and_execute_only_inside_worker(self):
        plan = multi_period_plan()
        previous, previous_location = grounded_cell(
            "2014",
            column_labels=("2014",),
            decimal_value="100",
        )
        current, current_location = grounded_cell(
            "2015",
            column_labels=("2015",),
            decimal_value="200",
        )
        evidence = [current, previous]
        locations = {
            previous.evidence_id: previous_location,
            current.evidence_id: current_location,
        }
        scales = [
            resolved_scale(current, source_scale=Scale.MILLION),
            resolved_scale(previous, source_scale=Scale.MILLION),
        ]
        links = link_schema(plan, evidence, locations)
        masked = mask_numeric_evidence(plan, evidence, locations, links, scales)
        binding_map = build_binding_map(masked, evidence, locations, scales)
        programmer_input = ProgrammerInput(
            plan=plan,
            masked_evidence=masked,
            formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
        )
        generated = generate_program(programmer_input)
        self.assertIs(generated.status, ProgrammerStatus.GENERATED)
        request = SandboxExecutionRequest(
            schema_version=EXECUTION_REQUEST_SCHEMA_VERSION,
            program=generated.program,
            programmer_input=programmer_input,
            binding_map=binding_map,
            limits_profile_id=EXECUTION_LIMITS_PROFILE_ID,
        )

        result = execute_sandboxed(request)

        self.assertTrue(result.success)
        self.assertEqual(result.output.values[0].value, "100")
        self.assertIs(result.output.values[0].scale, Scale.PERCENT)
        serialized_result = result.to_dict()
        self.assertNotIn("100.0", str(serialized_result))
        self.assertNotIn("200.0", str(serialized_result))


if __name__ == "__main__":
    unittest.main()
