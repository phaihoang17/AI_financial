from src.evidence.m5_schemas import (
    MaskedEvidence,
    MaskedEvidenceBundle,
    make_value_placeholder,
)
from src.programmer.schemas import (
    Program,
    ProgramInput,
    ProgramOperation,
    ProgramOutputKind,
    ProgrammerInput,
    ProgramStep,
)
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from src.supervisor.schemas import (
    EvidenceSource,
    Plan,
    QuestionType,
    RetrievalRequirement,
    TableClass,
)
from tests.evidence.m5_helpers import multi_period_plan, plan as lookup_plan


def _plan(
    question_type,
    periods,
    *,
    derived_target,
    formula_id,
    reasoning_mode,
    model_tier,
):
    requirements = [
        RetrievalRequirement.create(
            EvidenceSource.TABLE,
            TableClass.INCOME_STATEMENT,
            "LNST",
            period,
        )
        for period in periods
    ]
    return Plan.from_dict(
        {
            "question_type": question_type,
            "company": {"name": "Test Company", "ticker": "AAA"},
            "periods": list(periods),
            "period_kind": "NAM",
            "statement_scope": "HOP_NHAT",
            "target_metrics": ["LNST"],
            "derived_target": derived_target,
            "formula_id": formula_id,
            "tables_needed": ["INCOME_STATEMENT"],
            "retrieval_requirements": [item.to_dict() for item in requirements],
            "evidence_sources": ["TABLE"],
            "reasoning_mode": reasoning_mode,
            "requires_scale_resolution": True,
            "model_tier": model_tier,
            "verify_profile": "LIGHT" if question_type == "LOOKUP" else "STRICT",
            "max_retries": 1 if question_type == "LOOKUP" else 2,
            "confidence": 1.0,
            "abstain": False,
            "abstain_reason": None,
        }
    )


def programmer_input_for_plan(current_plan, *, reverse_masked=False):
    items = [
        MaskedEvidence(
            placeholder=make_value_placeholder(f"evidence-{requirement.period}"),
            evidence_id=f"evidence-{requirement.period}",
            requirement_id=requirement.requirement_id,
            metric=requirement.metric,
            period=requirement.period,
            row_path=[],
            column_path=[],
        )
        for requirement in current_plan.retrieval_requirements
        if requirement.required
    ]
    if reverse_masked:
        items.reverse()
    return ProgrammerInput(
        plan=current_plan,
        masked_evidence=MaskedEvidenceBundle(items=items),
        formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
    )


def lookup_programmer_input():
    return programmer_input_for_plan(lookup_plan())


def compare_programmer_input(*, reverse_masked=False):
    return programmer_input_for_plan(
        _plan(
            "MULTI_PERIOD",
            ("2014", "2015"),
            derived_target=None,
            formula_id=None,
            reasoning_mode="DIRECT",
            model_tier="CHEAP",
        ),
        reverse_masked=reverse_masked,
    )


def average_programmer_input(*, reverse_masked=False):
    return programmer_input_for_plan(
        _plan(
            "AGGREGATE",
            ("2013", "2014", "2015"),
            derived_target="AVERAGE",
            formula_id="AVERAGE",
            reasoning_mode="PROGRAM",
            model_tier="STRONG",
        ),
        reverse_masked=reverse_masked,
    )


def ratio_programmer_input():
    programmer_input = lookup_programmer_input()
    programmer_input.plan.question_type = QuestionType.DERIVED_RATIO
    return programmer_input


def growth_programmer_input():
    return programmer_input_for_plan(multi_period_plan())


def valid_growth_program(programmer_input=None):
    programmer_input = (
        growth_programmer_input() if programmer_input is None else programmer_input
    )
    inputs = [
        ProgramInput(
            input_id=f"input_{index}",
            placeholder=item.placeholder,
            evidence_id=item.evidence_id,
            requirement_id=item.requirement_id,
        )
        for index, item in enumerate(programmer_input.masked_evidence.items)
    ]
    steps = [
        ProgramStep(
            step_id="step_growth",
            operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
            input_refs=[item.input_id for item in inputs],
            formula_id="GROWTH_RATE",
        )
    ]
    return Program.create(
        formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
        question_type=programmer_input.plan.question_type,
        formula_id="GROWTH_RATE",
        inputs=inputs,
        steps=steps,
        output_ref="step_growth",
        output_kind=ProgramOutputKind.SCALAR,
    )


def rebuild_program(program, **changes):
    values = {
        "formula_registry_fingerprint": program.formula_registry_fingerprint,
        "question_type": program.question_type,
        "formula_id": program.formula_id,
        "inputs": list(program.inputs),
        "steps": list(program.steps),
        "output_ref": program.output_ref,
        "output_kind": program.output_kind,
    }
    values.update(changes)
    return Program.create(**values)
