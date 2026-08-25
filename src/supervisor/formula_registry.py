"""Canonical formula definitions and deterministic implementation metadata."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Optional, Sequence

from src.formulas.schemas import (
    FORMULA_IMPLEMENTATION_SCHEMA_VERSION,
    FormulaImplementation,
    FormulaImplementationKind,
)
from src.supervisor.schemas import (
    EvidenceSource,
    FormulaDefinition,
    FormulaPeriodRule,
    QuestionType,
    ReasoningMode,
)
from src.understanding.schemas import Operation


FORMULA_REGISTRY: tuple[FormulaDefinition, ...] = (
    FormulaDefinition(
        formula_id="GROWTH_RATE",
        operation=Operation.GROWTH,
        question_type=QuestionType.MULTI_PERIOD,
        derived_target="GROWTH",
        required_metrics=[],
        period_rule=FormulaPeriodRule.EXACT_TWO,
        reasoning_mode=ReasoningMode.PROGRAM,
        evidence_sources=[EvidenceSource.TABLE],
    ),
    FormulaDefinition(
        formula_id="AVERAGE",
        operation=Operation.AGGREGATE,
        question_type=QuestionType.AGGREGATE,
        derived_target="AVERAGE",
        required_metrics=[],
        period_rule=FormulaPeriodRule.AT_LEAST_TWO,
        reasoning_mode=ReasoningMode.PROGRAM,
        evidence_sources=[EvidenceSource.TABLE],
    ),
)


FORMULA_IMPLEMENTATIONS: tuple[FormulaImplementation, ...] = (
    FormulaImplementation(
        schema_version=FORMULA_IMPLEMENTATION_SCHEMA_VERSION,
        formula_id="GROWTH_RATE",
        implementation_kind=FormulaImplementationKind.PERCENT_GROWTH,
        input_order=["previous", "current"],
        min_arity=2,
        max_arity=2,
        constants=["100"],
    ),
    FormulaImplementation(
        schema_version=FORMULA_IMPLEMENTATION_SCHEMA_VERSION,
        formula_id="AVERAGE",
        implementation_kind=FormulaImplementationKind.ARITHMETIC_MEAN,
        input_order=["values"],
        min_arity=2,
        max_arity=None,
        constants=[],
    ),
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def make_formula_registry_fingerprint(
    definitions: Sequence[FormulaDefinition] = FORMULA_REGISTRY,
    implementations: Sequence[FormulaImplementation] = FORMULA_IMPLEMENTATIONS,
) -> str:
    definition_ids = [definition.formula_id for definition in definitions]
    implementation_ids = [implementation.formula_id for implementation in implementations]
    if len(definition_ids) != len(set(definition_ids)):
        raise ValueError("formula definitions must use unique formula IDs")
    if len(implementation_ids) != len(set(implementation_ids)):
        raise ValueError("formula implementations must use unique formula IDs")
    if set(definition_ids) != set(implementation_ids):
        raise ValueError("formula definitions and implementations must match exactly")
    implementations_by_id = {
        implementation.formula_id: implementation for implementation in implementations
    }
    payload = {
        "schema_version": FORMULA_IMPLEMENTATION_SCHEMA_VERSION,
        "formulas": [
            {
                "definition": definition.to_dict(),
                "implementation": implementations_by_id[
                    definition.formula_id
                ].to_dict(),
            }
            for definition in definitions
        ],
    }
    return sha256(_canonical_json(payload)).hexdigest()


FORMULA_REGISTRY_FINGERPRINT = make_formula_registry_fingerprint()


def get_formula(
    formula_id: str, registry: Sequence[FormulaDefinition] = FORMULA_REGISTRY
) -> Optional[FormulaDefinition]:
    return next((formula for formula in registry if formula.formula_id == formula_id), None)


def formulas_for_operation(
    operation: Operation, registry: Sequence[FormulaDefinition] = FORMULA_REGISTRY
) -> list[FormulaDefinition]:
    return [formula for formula in registry if formula.operation is operation]


def get_formula_implementation(
    formula_id: str,
    implementations: Sequence[FormulaImplementation] = FORMULA_IMPLEMENTATIONS,
) -> Optional[FormulaImplementation]:
    return next(
        (
            implementation
            for implementation in implementations
            if implementation.formula_id == formula_id
        ),
        None,
    )
