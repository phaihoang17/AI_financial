"""Bounded deterministic table-transform planner for M6B."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from src.evidence.m5_schemas import MaskedEvidence, MaskedEvidenceBundle
from src.orchestration.schemas import EvaluationFailureStage, FailureAttribution
from src.programmer.schemas import (
    Program,
    ProgramInput,
    ProgramOperation,
    ProgramOutputKind,
    ProgramStep,
    ProgrammerInput,
)
from src.programmer.validator import validate_program
from src.supervisor.formula_registry import FORMULA_REGISTRY_FINGERPRINT
from src.supervisor.schemas import Plan, QuestionType
from src.table_transform.executor import TableTransformExecutionError, execute_operation
from src.table_transform.schemas import (
    AddDerivedColumnOp,
    AggregateFunction,
    GroupOp,
    SelectColumnsOp,
    SelectRowsOp,
    SortOp,
    TableCell,
    TableOperation,
    TableOperationType,
    TableRow,
    TableState,
    TableTransformFailureCode,
)
from src.understanding.schemas import QueryUnderstanding, SchemaValidationError

DEFAULT_MAX_STEPS = 5


class TableTransformStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass(frozen=True)
class TableTransformResult:
    status: TableTransformStatus
    terminal_state: Optional[TableState]
    program: Optional[Program]
    step_count: int
    failure_attribution: Optional[FailureAttribution] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "terminal_state": None if self.terminal_state is None else self.terminal_state.to_dict(),
            "program": None if self.program is None else self.program.to_dict(),
            "step_count": self.step_count,
            "failure_attribution": None if self.failure_attribution is None else self.failure_attribution.to_dict(),
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
        }


def is_terminal_state(state: TableState, plan: Plan) -> bool:
    """Explicit terminal condition check for TableState relative to Plan."""
    if not state.rows:
        return False

    # Check rows: rows must strictly match target metrics
    target_metrics = set(plan.target_metrics)
    if any(row.row_label not in target_metrics for row in state.rows):
        return False

    # Check question type conditions
    if plan.question_type is QuestionType.LOOKUP:
        if len(state.rows) != 1:
            return False
        if len(plan.periods) != 1:
            return False
        target_period = plan.periods[0]
        return state.columns == [target_period]

    if plan.question_type is QuestionType.MULTI_PERIOD:
        if plan.formula_id == "GROWTH_RATE":
            # Growth rate requires the derived column to be present
            return "GROWTH_RATE" in state.columns and len(state.rows) == 1
        # Multi-period lookup/compare: columns must equal requested periods in order
        return list(state.columns) == list(plan.periods)

    if plan.question_type is QuestionType.AGGREGATE:
        # Aggregated table must have target aggregation computed and matching columns
        if len(state.rows) != 1:
            return False
        if plan.formula_id:
            return plan.formula_id in state.columns or any(
                c.derived_formula_id == plan.formula_id
                for row in state.rows
                for c in row.cells.values()
            )
        return True

    return False


def _synthesize_program(
    terminal_state: TableState,
    plan: Plan,
) -> Program:
    """Synthesize validated M6 Program from terminal TableState."""
    inputs: List[ProgramInput] = []
    input_cells: List[TableCell] = []
    seen_placeholders = set()

    for row in terminal_state.rows:
        for col_name in terminal_state.columns:
            cell = row.cells.get(col_name)
            if cell is not None and cell.is_placeholder and cell.value not in seen_placeholders:
                # If cell is derived, we need its underlying source inputs
                if not cell.derived_formula_id and cell.evidence_id and cell.requirement_id:
                    seen_placeholders.add(cell.value)
                    input_id = f"input_{len(inputs)}"
                    inputs.append(
                        ProgramInput(
                            input_id=input_id,
                            placeholder=cell.value,
                            evidence_id=cell.evidence_id,
                            requirement_id=cell.requirement_id,
                        )
                    )
                    input_cells.append(cell)

    # In case derived column was created, extract input cells from history
    if not inputs and terminal_state.history:
        # Collect from input state before derivation
        first_rec = terminal_state.history[0]
        # Gather all requirement inputs from plan
        # fallback to plan requirements
        reqs = [r for r in plan.retrieval_requirements if r.required]
        for idx, req in enumerate(reqs):
            input_id = f"input_{idx}"
            placeholder = f"val_input_{idx}"
            # find matching cell in rows
            for row in terminal_state.rows:
                for c in row.cells.values():
                    if c.period == req.period and c.metric == req.metric and c.evidence_id:
                        placeholder = c.value
                        evidence_id = c.evidence_id
                        break
                else:
                    evidence_id = f"ev_{req.requirement_id}"
            inputs.append(
                ProgramInput(
                    input_id=input_id,
                    placeholder=placeholder,
                    evidence_id=evidence_id,
                    requirement_id=req.requirement_id,
                )
            )

    input_refs = [inp.input_id for inp in inputs]

    if plan.question_type is QuestionType.LOOKUP:
        steps = [
            ProgramStep(
                step_id="step_output",
                operation=ProgramOperation.IDENTITY,
                input_refs=[input_refs[0]],
                formula_id=None,
            )
        ]
        output_kind = ProgramOutputKind.SCALAR
    elif plan.question_type is QuestionType.MULTI_PERIOD:
        if plan.formula_id == "GROWTH_RATE":
            steps = [
                ProgramStep(
                    step_id="step_output",
                    operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
                    input_refs=input_refs,
                    formula_id="GROWTH_RATE",
                )
            ]
            output_kind = ProgramOutputKind.SCALAR
        else:
            steps = [
                ProgramStep(
                    step_id="step_output",
                    operation=ProgramOperation.COLLECT,
                    input_refs=input_refs,
                    formula_id=None,
                )
            ]
            output_kind = ProgramOutputKind.ORDERED_VALUES
    elif plan.question_type is QuestionType.AGGREGATE:
        formula = plan.formula_id or "AVERAGE"
        steps = [
            ProgramStep(
                step_id="step_output",
                operation=ProgramOperation.APPLY_REGISTERED_FORMULA,
                input_refs=input_refs,
                formula_id=formula,
            )
        ]
        output_kind = ProgramOutputKind.SCALAR
    else:
        raise SchemaValidationError(f"unsupported question type {plan.question_type}")

    program = Program.create(
        formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
        question_type=plan.question_type,
        formula_id=plan.formula_id,
        inputs=inputs,
        steps=steps,
        output_ref="step_output",
        output_kind=output_kind,
    )

    # Validate program against programmer input bundle
    masked_items = [
        MaskedEvidence(
            placeholder=inp.placeholder,
            evidence_id=inp.evidence_id,
            requirement_id=inp.requirement_id,
            metric=plan.target_metrics[0] if plan.target_metrics else "",
            period=plan.periods[0] if plan.periods else "",
            row_path=[],
            column_path=[],
        )
        for inp in inputs
    ]
    prog_input = ProgrammerInput(
        plan=plan,
        masked_evidence=MaskedEvidenceBundle(items=masked_items),
        formula_registry_fingerprint=FORMULA_REGISTRY_FINGERPRINT,
    )
    validated = validate_program(program, prog_input)
    return validated


def _next_operation(
    state: TableState,
    query: QueryUnderstanding,
    plan: Plan,
) -> Optional[TableOperation]:
    """Deterministically select next atomic operation to apply."""
    # 1. Row selection: filter out rows not in plan.target_metrics
    target_metrics = set(plan.target_metrics)
    unwanted_rows = [r for r in state.rows if r.row_label not in target_metrics]
    if unwanted_rows:
        matching_labels = [m for m in plan.target_metrics if any(r.row_label == m for r in state.rows)]
        if matching_labels:
            return TableOperation(
                operation_type=TableOperationType.SELECT_ROWS,
                payload=SelectRowsOp(row_labels=matching_labels),
            )

    # 2. Column selection: filter out columns not in plan.periods
    target_periods = list(plan.periods)
    if any(col not in target_periods for col in state.columns if col != "GROWTH_RATE"):
        valid_cols = [col for col in state.columns if col in target_periods]
        if valid_cols:
            return TableOperation(
                operation_type=TableOperationType.SELECT_COLUMNS,
                payload=SelectColumnsOp(columns=valid_cols),
            )

    # 3. Derived column: add GROWTH_RATE if requested and not yet present
    if (
        plan.question_type is QuestionType.MULTI_PERIOD
        and plan.formula_id == "GROWTH_RATE"
        and "GROWTH_RATE" not in state.columns
    ):
        if len(state.columns) >= 2 and all(p in state.columns for p in plan.periods):
            return TableOperation(
                operation_type=TableOperationType.ADD_DERIVED_COLUMN,
                payload=AddDerivedColumnOp(
                    new_column="GROWTH_RATE",
                    formula_id="GROWTH_RATE",
                    input_columns=list(plan.periods),
                ),
            )

    # 4. Aggregation: if AGGREGATE question type and multiple rows or periods exist
    if plan.question_type is QuestionType.AGGREGATE:
        formula = plan.formula_id or "AVERAGE"
        # Check if we need to group by metric or segment
        if len(state.rows) > 1 and len(state.columns) >= 1:
            agg_col = state.columns[0]
            return TableOperation(
                operation_type=TableOperationType.GROUP,
                payload=GroupOp(
                    by_column=state.columns[0],
                    agg_column=state.columns[-1],
                    agg_func=AggregateFunction.AVERAGE if formula == "AVERAGE" else AggregateFunction.SUM,
                ),
            )

    return None


def plan_and_transform(
    initial_state: TableState,
    query: QueryUnderstanding,
    plan: Plan,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> TableTransformResult:
    """Execute bounded iterative table transformation towards explicit terminal state."""
    if not isinstance(initial_state, TableState):
        raise SchemaValidationError("initial_state must be a TableState")
    if not isinstance(query, QueryUnderstanding):
        raise SchemaValidationError("query must be a QueryUnderstanding")
    if not isinstance(plan, Plan):
        raise SchemaValidationError("plan must be a Plan")
    if not isinstance(max_steps, int) or max_steps <= 0:
        raise SchemaValidationError("max_steps must be a positive integer")

    current_state = initial_state
    step = 0

    while step < max_steps:
        if is_terminal_state(current_state, plan):
            try:
                program = _synthesize_program(current_state, plan)
            except Exception as e:
                return TableTransformResult(
                    status=TableTransformStatus.FAILED,
                    terminal_state=current_state,
                    program=None,
                    step_count=step,
                    failure_attribution=FailureAttribution(
                        stage=EvaluationFailureStage.PROGRAMMER,
                        code=TableTransformFailureCode.CANNOT_CONSTRUCT_PROGRAM.value,
                        reason=str(e),
                        attempt_index=0,
                    ),
                    failure_code=TableTransformFailureCode.CANNOT_CONSTRUCT_PROGRAM.value,
                    failure_message=str(e),
                )
            return TableTransformResult(
                status=TableTransformStatus.SUCCESS,
                terminal_state=current_state,
                program=program,
                step_count=step,
            )

        op = _next_operation(current_state, query, plan)
        if op is None:
            # Cannot progress further towards terminal state
            return TableTransformResult(
                status=TableTransformStatus.FAILED,
                terminal_state=current_state,
                program=None,
                step_count=step,
                failure_attribution=FailureAttribution(
                    stage=EvaluationFailureStage.PROGRAMMER,
                    code=TableTransformFailureCode.TERMINAL_STATE_UNREACHABLE.value,
                    reason="planner could not determine next atomic operation to reach terminal state",
                    attempt_index=0,
                ),
                failure_code=TableTransformFailureCode.TERMINAL_STATE_UNREACHABLE.value,
                failure_message="terminal state unreachable from current state",
            )

        try:
            current_state = execute_operation(current_state, op)
        except TableTransformExecutionError as err:
            return TableTransformResult(
                status=TableTransformStatus.FAILED,
                terminal_state=current_state,
                program=None,
                step_count=step,
                failure_attribution=FailureAttribution(
                    stage=EvaluationFailureStage.PROGRAMMER,
                    code=err.code.value,
                    reason=str(err),
                    attempt_index=0,
                ),
                failure_code=err.code.value,
                failure_message=str(err),
            )
        step += 1

    # Check terminal condition on final step
    if is_terminal_state(current_state, plan):
        program = _synthesize_program(current_state, plan)
        return TableTransformResult(
            status=TableTransformStatus.SUCCESS,
            terminal_state=current_state,
            program=program,
            step_count=step,
        )

    # Exceeded max_steps
    return TableTransformResult(
        status=TableTransformStatus.FAILED,
        terminal_state=current_state,
        program=None,
        step_count=step,
        failure_attribution=FailureAttribution(
            stage=EvaluationFailureStage.PROGRAMMER,
            code=TableTransformFailureCode.MAX_STEPS_EXCEEDED.value,
            reason=f"exceeded maximum transformation steps ({max_steps}) without reaching terminal state",
            attempt_index=0,
        ),
        failure_code=TableTransformFailureCode.MAX_STEPS_EXCEEDED.value,
        failure_message=f"exceeded hard limit of {max_steps} steps",
    )
