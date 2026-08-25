"""Deterministic TASK-068 validator for the symbolic M6 Program DSL."""

from __future__ import annotations

from enum import Enum
import json
import re
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from src.evidence.m5_schemas import MaskedEvidence
from src.formulas.schemas import FormulaImplementation
from src.programmer.schemas import (
    Program,
    ProgramInput,
    ProgramOperation,
    ProgramOutputKind,
    ProgrammerInput,
    canonical_json,
    make_program_id,
)
from src.supervisor.formula_registry import (
    FORMULA_IMPLEMENTATIONS,
    FORMULA_REGISTRY,
    FORMULA_REGISTRY_FINGERPRINT,
)
from src.supervisor.schemas import FormulaDefinition, QuestionType
from src.understanding.schemas import SchemaValidationError


_REFERENCE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_CANONICAL_DECIMAL = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")
_FORBIDDEN_CODE_KEYS = {
    "args",
    "attributes",
    "call",
    "code",
    "eval",
    "exec",
    "expression",
    "file",
    "function",
    "imports",
    "kwargs",
    "network",
    "source",
    "source_code",
}
_FORBIDDEN_LITERAL_KEYS = {"constant", "constants", "literal", "literals", "value"}


class ProgramValidationFailureCode(str, Enum):
    INVALID_SCHEMA = "INVALID_SCHEMA"
    FORBIDDEN_OPERATION = "FORBIDDEN_OPERATION"
    FORBIDDEN_LITERAL = "FORBIDDEN_LITERAL"
    FORBIDDEN_CODE = "FORBIDDEN_CODE"
    INVALID_REFERENCE_ID = "INVALID_REFERENCE_ID"
    DUPLICATE_ID = "DUPLICATE_ID"
    DUPLICATE_INPUT_BINDING = "DUPLICATE_INPUT_BINDING"
    UNKNOWN_PLACEHOLDER = "UNKNOWN_PLACEHOLDER"
    INPUT_BINDING_MISMATCH = "INPUT_BINDING_MISMATCH"
    MISSING_REQUIRED_EVIDENCE = "MISSING_REQUIRED_EVIDENCE"
    NON_TOPOLOGICAL_REFERENCE = "NON_TOPOLOGICAL_REFERENCE"
    UNKNOWN_REFERENCE = "UNKNOWN_REFERENCE"
    INVALID_OUTPUT_REF = "INVALID_OUTPUT_REF"
    OUTPUT_KIND_MISMATCH = "OUTPUT_KIND_MISMATCH"
    PLAN_QUESTION_TYPE_MISMATCH = "PLAN_QUESTION_TYPE_MISMATCH"
    PLAN_FORMULA_MISMATCH = "PLAN_FORMULA_MISMATCH"
    FORMULA_REGISTRY_FINGERPRINT_MISMATCH = (
        "FORMULA_REGISTRY_FINGERPRINT_MISMATCH"
    )
    FORMULA_NOT_REGISTERED = "FORMULA_NOT_REGISTERED"
    FORMULA_OPERATION_MISMATCH = "FORMULA_OPERATION_MISMATCH"
    FORMULA_ARITY_MISMATCH = "FORMULA_ARITY_MISMATCH"
    FORMULA_INPUT_ORDER_MISMATCH = "FORMULA_INPUT_ORDER_MISMATCH"
    PROGRAM_ID_MISMATCH = "PROGRAM_ID_MISMATCH"
    NON_DETERMINISTIC_SERIALIZATION = "NON_DETERMINISTIC_SERIALIZATION"


class ProgramValidationError(SchemaValidationError):
    def __init__(self, code: ProgramValidationFailureCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


def _fail(code: ProgramValidationFailureCode, message: str) -> None:
    raise ProgramValidationError(code, message)


def _scan_raw_safety(value: Any, path: str = "Program") -> None:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        _fail(
            ProgramValidationFailureCode.FORBIDDEN_LITERAL,
            f"{path} contains a numeric or boolean literal",
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).casefold()
            if normalized_key in _FORBIDDEN_LITERAL_KEYS:
                _fail(
                    ProgramValidationFailureCode.FORBIDDEN_LITERAL,
                    f"{path}.{key} is not part of the symbolic Program DSL",
                )
            if normalized_key in _FORBIDDEN_CODE_KEYS:
                _fail(
                    ProgramValidationFailureCode.FORBIDDEN_CODE,
                    f"{path}.{key} is not part of the symbolic Program DSL",
                )
            _scan_raw_safety(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_raw_safety(item, f"{path}[{index}]")


def _prevalidate_operations(value: Any) -> None:
    if not isinstance(value, Mapping):
        return
    steps = value.get("steps")
    if not isinstance(steps, list):
        return
    allowed = {operation.value for operation in ProgramOperation}
    for index, step in enumerate(steps):
        if isinstance(step, Mapping) and step.get("operation") not in allowed:
            _fail(
                ProgramValidationFailureCode.FORBIDDEN_OPERATION,
                f"steps[{index}].operation is not allowlisted",
            )


def _as_program(value: Program | Mapping[str, Any]) -> Program:
    raw = value.to_dict() if isinstance(value, Program) else value
    _scan_raw_safety(raw)
    _prevalidate_operations(raw)
    try:
        return value if isinstance(value, Program) else Program.from_dict(value)
    except SchemaValidationError as error:
        _fail(ProgramValidationFailureCode.INVALID_SCHEMA, str(error))


def _validate_reference_id(value: str, path: str) -> None:
    if _CANONICAL_DECIMAL.fullmatch(value):
        _fail(
            ProgramValidationFailureCode.FORBIDDEN_LITERAL,
            f"{path} cannot be a numeric literal",
        )
    if not _REFERENCE_ID.fullmatch(value):
        _fail(
            ProgramValidationFailureCode.INVALID_REFERENCE_ID,
            f"{path} is not a safe symbolic identifier",
        )


def _formula_indexes(
    definitions: Sequence[FormulaDefinition],
    implementations: Sequence[FormulaImplementation],
) -> Tuple[Dict[str, FormulaDefinition], Dict[str, FormulaImplementation]]:
    definitions_by_id = {item.formula_id: item for item in definitions}
    implementations_by_id = {item.formula_id: item for item in implementations}
    if (
        len(definitions_by_id) != len(definitions)
        or len(implementations_by_id) != len(implementations)
        or set(definitions_by_id) != set(implementations_by_id)
    ):
        _fail(
            ProgramValidationFailureCode.FORMULA_NOT_REGISTERED,
            "formula definitions and implementations must match uniquely",
        )
    return definitions_by_id, implementations_by_id


def _validate_program_inputs(
    program: Program, programmer_input: ProgrammerInput
) -> Dict[str, ProgramInput]:
    masked_items = programmer_input.masked_evidence.items
    plan_requirement_ids = {
        requirement.requirement_id
        for requirement in programmer_input.plan.retrieval_requirements
    }
    for item in masked_items:
        if item.requirement_id not in plan_requirement_ids:
            _fail(
                ProgramValidationFailureCode.INPUT_BINDING_MISMATCH,
                f"masked evidence uses unknown requirement {item.requirement_id}",
            )
    placeholders = {item.placeholder for item in masked_items}
    masked_bindings = {
        (item.placeholder, item.evidence_id, item.requirement_id)
        for item in masked_items
    }
    inputs_by_id: Dict[str, ProgramInput] = {}
    input_bindings: Set[Tuple[str, str, str]] = set()
    for index, item in enumerate(program.inputs):
        _validate_reference_id(item.input_id, f"inputs[{index}].input_id")
        if item.input_id in inputs_by_id:
            _fail(
                ProgramValidationFailureCode.DUPLICATE_ID,
                f"duplicate input ID: {item.input_id}",
            )
        if item.placeholder not in placeholders:
            _fail(
                ProgramValidationFailureCode.UNKNOWN_PLACEHOLDER,
                item.placeholder,
            )
        binding = (item.placeholder, item.evidence_id, item.requirement_id)
        if binding not in masked_bindings:
            _fail(
                ProgramValidationFailureCode.INPUT_BINDING_MISMATCH,
                item.input_id,
            )
        if binding in input_bindings:
            _fail(
                ProgramValidationFailureCode.DUPLICATE_INPUT_BINDING,
                item.input_id,
            )
        inputs_by_id[item.input_id] = item
        input_bindings.add(binding)
    return inputs_by_id


def _validate_steps(
    program: Program,
    inputs_by_id: Dict[str, ProgramInput],
    implementations_by_id: Dict[str, FormulaImplementation],
) -> Tuple[Dict[str, List[str]], Dict[str, ProgramOperation]]:
    step_ids = [step.step_id for step in program.steps]
    for index, step_id in enumerate(step_ids):
        _validate_reference_id(step_id, f"steps[{index}].step_id")
    if len(step_ids) != len(set(step_ids)):
        _fail(ProgramValidationFailureCode.DUPLICATE_ID, "duplicate step ID")
    if set(step_ids).intersection(inputs_by_id):
        _fail(
            ProgramValidationFailureCode.DUPLICATE_ID,
            "input and step IDs share one reference namespace",
        )

    available = set(inputs_by_id)
    all_step_ids = set(step_ids)
    dependencies: Dict[str, List[str]] = {}
    operations: Dict[str, ProgramOperation] = {}
    for index, step in enumerate(program.steps):
        for ref_index, ref in enumerate(step.input_refs):
            _validate_reference_id(
                ref, f"steps[{index}].input_refs[{ref_index}]"
            )
            if ref not in available:
                code = (
                    ProgramValidationFailureCode.NON_TOPOLOGICAL_REFERENCE
                    if ref in all_step_ids
                    else ProgramValidationFailureCode.UNKNOWN_REFERENCE
                )
                _fail(code, f"{step.step_id} references unavailable {ref}")

        if step.operation is ProgramOperation.IDENTITY:
            if len(step.input_refs) != 1 or step.formula_id is not None:
                _fail(
                    ProgramValidationFailureCode.FORMULA_OPERATION_MISMATCH,
                    "IDENTITY requires one ref and null formula_id",
                )
        elif step.operation is ProgramOperation.COLLECT:
            if step.formula_id is not None:
                _fail(
                    ProgramValidationFailureCode.FORMULA_OPERATION_MISMATCH,
                    "COLLECT requires null formula_id",
                )
        elif step.operation is ProgramOperation.APPLY_REGISTERED_FORMULA:
            if step.formula_id is None:
                _fail(
                    ProgramValidationFailureCode.FORMULA_OPERATION_MISMATCH,
                    "APPLY_REGISTERED_FORMULA requires formula_id",
                )
            implementation = implementations_by_id.get(step.formula_id)
            if implementation is None:
                _fail(
                    ProgramValidationFailureCode.FORMULA_NOT_REGISTERED,
                    step.formula_id,
                )
            if not implementation.accepts_arity(len(step.input_refs)):
                _fail(
                    ProgramValidationFailureCode.FORMULA_ARITY_MISMATCH,
                    f"{step.formula_id} received {len(step.input_refs)} refs",
                )
        else:  # defensive against mutated enum-like objects
            _fail(
                ProgramValidationFailureCode.FORBIDDEN_OPERATION,
                str(step.operation),
            )
        dependencies[step.step_id] = list(step.input_refs)
        operations[step.step_id] = step.operation
        available.add(step.step_id)
    return dependencies, operations


def _leaf_inputs(
    ref: str,
    inputs_by_id: Dict[str, ProgramInput],
    dependencies: Dict[str, List[str]],
) -> List[ProgramInput]:
    if ref in inputs_by_id:
        return [inputs_by_id[ref]]
    leaves: List[ProgramInput] = []
    for dependency in dependencies[ref]:
        leaves.extend(_leaf_inputs(dependency, inputs_by_id, dependencies))
    return leaves


def _required_masked_items(programmer_input: ProgrammerInput) -> List[MaskedEvidence]:
    required_ids = [
        requirement.requirement_id
        for requirement in programmer_input.plan.retrieval_requirements
        if requirement.required
    ]
    masked_by_requirement: Dict[str, List[MaskedEvidence]] = {}
    for item in programmer_input.masked_evidence.items:
        masked_by_requirement.setdefault(item.requirement_id, []).append(item)
    for requirement_id in required_ids:
        if not masked_by_requirement.get(requirement_id):
            _fail(
                ProgramValidationFailureCode.MISSING_REQUIRED_EVIDENCE,
                requirement_id,
            )
    return [
        item
        for requirement_id in required_ids
        for item in masked_by_requirement[requirement_id]
    ]


def _validate_evidence_coverage(
    programmer_input: ProgrammerInput,
    inputs_by_id: Dict[str, ProgramInput],
    consumed_inputs: List[ProgramInput],
) -> None:
    required_items = _required_masked_items(programmer_input)
    declared = {
        (item.placeholder, item.evidence_id, item.requirement_id): item.input_id
        for item in inputs_by_id.values()
    }
    consumed_ids = {item.input_id for item in consumed_inputs}
    for item in required_items:
        binding = (item.placeholder, item.evidence_id, item.requirement_id)
        input_id = declared.get(binding)
        if input_id is None or input_id not in consumed_ids:
            _fail(
                ProgramValidationFailureCode.MISSING_REQUIRED_EVIDENCE,
                item.requirement_id,
            )


def _validate_formula_order(
    program: Program,
    programmer_input: ProgrammerInput,
    inputs_by_id: Dict[str, ProgramInput],
    dependencies: Dict[str, List[str]],
) -> None:
    required = _required_masked_items(programmer_input)
    expected = [
        (item.placeholder, item.evidence_id, item.requirement_id) for item in required
    ]
    for step in program.steps:
        if step.operation is not ProgramOperation.APPLY_REGISTERED_FORMULA:
            continue
        leaves_by_argument = [
            _leaf_inputs(ref, inputs_by_id, dependencies) for ref in step.input_refs
        ]
        if any(len(leaves) != 1 for leaves in leaves_by_argument):
            _fail(
                ProgramValidationFailureCode.FORMULA_INPUT_ORDER_MISMATCH,
                f"{step.step_id} formula arguments must each resolve to one input",
            )
        actual = [
            (leaves[0].placeholder, leaves[0].evidence_id, leaves[0].requirement_id)
            for leaves in leaves_by_argument
        ]
        if actual != expected:
            _fail(
                ProgramValidationFailureCode.FORMULA_INPUT_ORDER_MISMATCH,
                f"{step.step_id} inputs do not follow Plan requirement order",
            )


def _validate_formula_boundary(
    program: Program,
    programmer_input: ProgrammerInput,
    definitions_by_id: Dict[str, FormulaDefinition],
) -> None:
    plan = programmer_input.plan
    if program.question_type is not plan.question_type:
        _fail(
            ProgramValidationFailureCode.PLAN_QUESTION_TYPE_MISMATCH,
            "Program.question_type differs from Plan.question_type",
        )
    if program.formula_id is not None and _CANONICAL_DECIMAL.fullmatch(
        program.formula_id
    ):
        _fail(
            ProgramValidationFailureCode.FORBIDDEN_LITERAL,
            "Program.formula_id cannot be a numeric literal",
        )
    if program.formula_id is not None and program.formula_id not in definitions_by_id:
        _fail(
            ProgramValidationFailureCode.FORMULA_NOT_REGISTERED,
            program.formula_id,
        )
    if program.formula_id != plan.formula_id:
        _fail(
            ProgramValidationFailureCode.PLAN_FORMULA_MISMATCH,
            "Program.formula_id differs from Plan.formula_id",
        )
    apply_steps = [
        step
        for step in program.steps
        if step.operation is ProgramOperation.APPLY_REGISTERED_FORMULA
    ]
    for step in apply_steps:
        if step.formula_id is not None and _CANONICAL_DECIMAL.fullmatch(
            step.formula_id
        ):
            _fail(
                ProgramValidationFailureCode.FORBIDDEN_LITERAL,
                f"{step.step_id}.formula_id cannot be a numeric literal",
            )
        if step.formula_id is not None and step.formula_id not in definitions_by_id:
            _fail(
                ProgramValidationFailureCode.FORMULA_NOT_REGISTERED,
                step.formula_id,
            )
    if program.formula_id is None:
        if apply_steps:
            _fail(
                ProgramValidationFailureCode.PLAN_FORMULA_MISMATCH,
                "formula-free Plan cannot apply a formula",
            )
    else:
        definition = definitions_by_id[program.formula_id]
        if definition.question_type is not program.question_type:
            _fail(
                ProgramValidationFailureCode.PLAN_QUESTION_TYPE_MISMATCH,
                "registered formula question_type differs from Program",
            )
        if not apply_steps or any(
            step.formula_id != program.formula_id for step in apply_steps
        ):
            _fail(
                ProgramValidationFailureCode.PLAN_FORMULA_MISMATCH,
                "Program must apply only its declared registered formula",
            )


def _validate_output(
    program: Program,
    inputs_by_id: Dict[str, ProgramInput],
    dependencies: Dict[str, List[str]],
    operations: Dict[str, ProgramOperation],
) -> List[ProgramInput]:
    _validate_reference_id(program.output_ref, "output_ref")
    if program.output_ref not in inputs_by_id and program.output_ref not in dependencies:
        _fail(
            ProgramValidationFailureCode.INVALID_OUTPUT_REF,
            program.output_ref,
        )
    expected_kind = (
        ProgramOutputKind.ORDERED_VALUES
        if program.question_type is QuestionType.MULTI_PERIOD
        and program.formula_id is None
        else ProgramOutputKind.SCALAR
    )
    if program.output_kind is not expected_kind:
        _fail(
            ProgramValidationFailureCode.OUTPUT_KIND_MISMATCH,
            f"expected {expected_kind.value}",
        )
    if (
        program.output_kind is ProgramOutputKind.ORDERED_VALUES
        and operations.get(program.output_ref) is not ProgramOperation.COLLECT
    ):
        _fail(
            ProgramValidationFailureCode.OUTPUT_KIND_MISMATCH,
            "ORDERED_VALUES output_ref must identify a COLLECT step",
        )
    return _leaf_inputs(program.output_ref, inputs_by_id, dependencies)


def _validate_identity(program: Program) -> None:
    expected_id = make_program_id(
        formula_registry_fingerprint=program.formula_registry_fingerprint,
        question_type=program.question_type,
        formula_id=program.formula_id,
        inputs=program.inputs,
        steps=program.steps,
        output_ref=program.output_ref,
        output_kind=program.output_kind,
    )
    if program.program_id != expected_id:
        _fail(
            ProgramValidationFailureCode.PROGRAM_ID_MISMATCH,
            "program_id does not match the symbolic Program payload",
        )


def _validate_serialization(program: Program) -> None:
    serialized = program.canonical_json()
    try:
        round_trip = Program.from_dict(json.loads(serialized))
    except (SchemaValidationError, TypeError, ValueError) as error:
        _fail(
            ProgramValidationFailureCode.NON_DETERMINISTIC_SERIALIZATION,
            str(error),
        )
    if round_trip.to_dict() != program.to_dict() or round_trip.canonical_json() != serialized:
        _fail(
            ProgramValidationFailureCode.NON_DETERMINISTIC_SERIALIZATION,
            "Program does not round-trip to one canonical serialization",
        )
    if serialized != canonical_json(program.to_dict()):
        _fail(
            ProgramValidationFailureCode.NON_DETERMINISTIC_SERIALIZATION,
            "Program serialization is not canonical JSON",
        )


def validate_program(
    value: Program | Mapping[str, Any],
    programmer_input: ProgrammerInput,
    *,
    formula_definitions: Sequence[FormulaDefinition] = FORMULA_REGISTRY,
    formula_implementations: Sequence[FormulaImplementation] = FORMULA_IMPLEMENTATIONS,
    formula_registry_fingerprint: str = FORMULA_REGISTRY_FINGERPRINT,
) -> Program:
    """Validate and return one Program without generating or executing it."""
    if not isinstance(programmer_input, ProgrammerInput):
        _fail(
            ProgramValidationFailureCode.INVALID_SCHEMA,
            "programmer_input must be a ProgrammerInput",
        )
    program = _as_program(value)
    if (
        programmer_input.formula_registry_fingerprint
        != formula_registry_fingerprint
        or program.formula_registry_fingerprint != formula_registry_fingerprint
    ):
        _fail(
            ProgramValidationFailureCode.FORMULA_REGISTRY_FINGERPRINT_MISMATCH,
            "ProgrammerInput, Program, and FormulaRegistry fingerprints must match",
        )

    definitions_by_id, implementations_by_id = _formula_indexes(
        formula_definitions, formula_implementations
    )
    _validate_formula_boundary(program, programmer_input, definitions_by_id)
    _required_masked_items(programmer_input)
    inputs_by_id = _validate_program_inputs(program, programmer_input)
    dependencies, operations = _validate_steps(
        program, inputs_by_id, implementations_by_id
    )
    consumed_inputs = _validate_output(
        program, inputs_by_id, dependencies, operations
    )
    _validate_evidence_coverage(programmer_input, inputs_by_id, consumed_inputs)
    _validate_formula_order(
        program, programmer_input, inputs_by_id, dependencies
    )
    _validate_identity(program)
    _validate_serialization(program)
    return program
