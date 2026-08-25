import unittest

from src.formulas.schemas import (
    FORMULA_IMPLEMENTATION_SCHEMA_VERSION,
    FormulaImplementation,
    FormulaImplementationKind,
)
from src.programmer.schemas import (
    PROGRAM_SCHEMA_VERSION,
    Program,
    ProgrammerInput,
    ProgrammerResult,
    ProgrammerStatus,
)
from src.supervisor.formula_registry import (
    FORMULA_IMPLEMENTATIONS,
    FORMULA_REGISTRY_FINGERPRINT,
    make_formula_registry_fingerprint,
)
from src.understanding.schemas import SchemaValidationError
from tests.programmer.helpers import growth_programmer_input, valid_growth_program


class ProgrammerContractTests(unittest.TestCase):
    def test_canonical_program_and_boundary_contracts_round_trip_exactly(self):
        programmer_input = growth_programmer_input()
        program = valid_growth_program(programmer_input)
        result = ProgrammerResult(
            ProgrammerStatus.GENERATED, program, None, None
        )

        self.assertEqual(program.schema_version, PROGRAM_SCHEMA_VERSION)
        self.assertEqual(
            Program.from_dict(program.to_dict()).to_dict(), program.to_dict()
        )
        self.assertEqual(
            ProgrammerInput.from_dict(programmer_input.to_dict()).to_dict(),
            programmer_input.to_dict(),
        )
        self.assertEqual(
            ProgrammerResult.from_dict(result.to_dict()).to_dict(),
            result.to_dict(),
        )

    def test_programmer_result_status_invariants(self):
        program = valid_growth_program()
        with self.assertRaises(SchemaValidationError):
            ProgrammerResult(
                ProgrammerStatus.GENERATED, program, "ERROR", "bad"
            )
        rejected = ProgrammerResult(
            ProgrammerStatus.REJECTED, None, "VALIDATION_FAILED", "bad program"
        )
        self.assertIsNone(rejected.program)
        with self.assertRaises(SchemaValidationError):
            ProgrammerResult(ProgrammerStatus.REJECTED, None, None, None)

    def test_binding_map_cannot_enter_programmer_input(self):
        payload = growth_programmer_input().to_dict()
        payload["binding_map"] = {"bindings": []}
        with self.assertRaises(SchemaValidationError):
            ProgrammerInput.from_dict(payload)

    def test_program_ids_and_serialization_are_deterministic(self):
        first = valid_growth_program()
        second = valid_growth_program()
        self.assertEqual(first.program_id, second.program_id)
        self.assertEqual(first.canonical_json(), second.canonical_json())
        self.assertEqual(len(first.program_id), 64)


class FormulaImplementationContractTests(unittest.TestCase):
    def test_registered_semantics_are_versioned_and_exact(self):
        growth, average = FORMULA_IMPLEMENTATIONS
        self.assertEqual(
            growth.schema_version, FORMULA_IMPLEMENTATION_SCHEMA_VERSION
        )
        self.assertIs(
            growth.implementation_kind,
            FormulaImplementationKind.PERCENT_GROWTH,
        )
        self.assertEqual(growth.input_order, ["previous", "current"])
        self.assertEqual(growth.constants, ["100"])
        self.assertTrue(growth.accepts_arity(2))
        self.assertFalse(growth.accepts_arity(1))
        self.assertIs(
            average.implementation_kind,
            FormulaImplementationKind.ARITHMETIC_MEAN,
        )
        self.assertEqual(average.input_order, ["values"])
        self.assertEqual(average.constants, [])
        self.assertTrue(average.accepts_arity(2))
        self.assertTrue(average.accepts_arity(5))
        self.assertEqual(
            FormulaImplementation.from_dict(growth.to_dict()), growth
        )

    def test_formula_registry_fingerprint_is_deterministic(self):
        self.assertEqual(
            make_formula_registry_fingerprint(), FORMULA_REGISTRY_FINGERPRINT
        )
        self.assertEqual(len(FORMULA_REGISTRY_FINGERPRINT), 64)


if __name__ == "__main__":
    unittest.main()
