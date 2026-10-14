"""LLM Pandas code generation and bounded repair prompts (ADR-069).

One served chat model (the existing ``OpenAICompatibleClient`` boundary, e.g.
the ADR-060 DeepSeek deployment) proposes a ``PandasProgram`` envelope. Repair
calls carry only the previous code and a sanitized, typed failure; they never
carry hidden answers. The model never computes values itself: it writes code,
and the isolated worker executes it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, is_dataclass, replace
import json
import re
from time import monotonic_ns
from typing import Any

from src.serving.schemas import ServingError
from src.text_to_pandas.catalog import DATASET_NAMES
from src.text_to_pandas.schemas import PandasProgram
from src.text_to_pandas.understanding import Discovery, T2PUnderstanding
from src.understanding.schemas import SchemaValidationError

SYSTEM_PROMPT = """You write Python/pandas code that answers a question about Vietnamese financial statements \
using ONLY the pandas DataFrames described in `schema`. Return ONLY one JSON object with keys:
status, selected_datasets, language, execution_engine, code, expected_result_type, result_unit, explanation.

- status: "OK" when the DataFrames can answer; "UNSUPPORTED" when a needed metric, column or period does not \
exist in them; "NEEDS_CLARIFICATION" when the question is ambiguous. For non-OK statuses set code to "" and \
explain why in `explanation`.
- language "python", execution_engine "pandas". selected_datasets: the DataFrame names the code reads.
- The DataFrames are already loaded as variables named exactly as in `schema.dataframes`; `pd`, `np` and \
`math` are available. Do not import anything else, read or write files, use the network, print, or call \
eval/exec/query/getattr. Do not use names starting with "_".
- Assign the final answer to `result`: a scalar, a pandas Series, or a pandas DataFrame.
- Also assign `evidence`: a DataFrame of the exact, unmodified source rows used (filter the provided \
DataFrames; keep ALL source columns including fact_id, ticker, fiscal_year, statement_scope, metric, \
line_item, value, unit and source_scale). Do not add computed rows to evidence.
- Filter explicitly by ticker, fiscal_year, statement_scope and metric. Use metric ids exactly as in \
`schema.metrics`; use line_item text only when no metric exists. Ratios are NOT metrics: compute them from \
their component metrics exactly as in `schema.ratios`; growth and averages exactly as in `schema.formulas`.
- Every financial value must come from the DataFrames. Never type values from your knowledge or the question; \
constants are allowed only for formula constants, unit conversion and thresholds stated in the question.
- If the required rows are missing, set result = None and evidence to the (possibly empty) rows found. \
Never fill missing values with 0.
- Keep monetary results in VND as stored (no rescaling to million/billion); growth and percentage ratios in \
percent.
- Rankings/top-N: return a DataFrame sorted as asked with ticker, fiscal_year and the value column(s).
- For multi-value results return a DataFrame with ticker, fiscal_year and columns named by metric/ratio ids, \
or long-format ticker, fiscal_year, metric, value. For a growth/aggregate row use the latest requested year.
- A Series must use fiscal_year as index for one company, or (ticker, fiscal_year) for several companies.
- expected_result_type: "scalar", "series" or "dataframe". result_unit: one of "VND", "VND/share", \
"PERCENT", "TIMES", "COUNT", "TEXT", "YEAR", "MIXED", "NONE".
- Prefer vectorized pandas operations."""

REPAIR_INSTRUCTION = (
    "Your previous code failed. Fix it using the failure below and the same schema. Return the full JSON "
    "object again. Do not work around validation rules or invent data."
)


@dataclass(frozen=True)
class GenerationOutcome:
    program: PandasProgram | None
    error_code: str | None
    error_message: str | None
    elapsed_ms: int
    usage: list = field(default_factory=list)


def build_payload(
    understanding: T2PUnderstanding,
    discovery: Discovery,
    *,
    previous: Mapping[str, Any] | None = None,
    feedback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question": understanding.question,
        "understanding": {
            **understanding.to_dict(),
            "required_metrics": list(understanding.required_metrics),
            "data_mode": discovery.mode,
            "note": "Understanding is deterministic and may be incomplete; the question is authoritative.",
        },
        "schema": discovery.context,
    }
    if previous is not None:
        payload["previous_attempt"] = {"code": previous.get("code"), "failure": dict(feedback or {})}
        payload["instruction"] = REPAIR_INSTRUCTION
    return payload


_FENCED = re.compile(r"\A\s*```(?:json)?\s*\n(.*)\n\s*```\s*\Z", re.DOTALL)


def _json_text(content: str) -> str:
    """The JSON object itself when a provider without JSON mode wraps it in one code fence."""
    match = _FENCED.match(content)
    return match.group(1) if match else content


class PandasCodeGenerator:
    """Propose one ``PandasProgram`` per call through an injected chat client."""

    def __init__(self, client: Any, *, max_tokens: int = 2048, thinking: bool = False) -> None:
        self.client = client
        self.max_tokens = max_tokens
        self.thinking = thinking

    def generate(
        self,
        understanding: T2PUnderstanding,
        discovery: Discovery,
        *,
        previous: Mapping[str, Any] | None = None,
        feedback: Mapping[str, Any] | None = None,
    ) -> GenerationOutcome:
        start = monotonic_ns()
        usage = []
        client = self.client
        # Every configured provider client (src/serving/providers.py) and the
        # shared OpenAI-compatible client report token usage via ``observer``.
        if is_dataclass(client) and hasattr(client, "observer"):
            def observe(event):
                usage.append(dict(event))
                if self.client.observer:
                    self.client.observer(event)
            client = replace(client, observer=observe)
        payload = build_payload(understanding, discovery, previous=previous, feedback=feedback)
        params: dict[str, Any] = {
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            "thinking": {"type": "enabled" if self.thinking else "disabled"},
        }
        if not self.thinking:
            params["temperature"] = 0.0

        def done(program=None, code=None, message=None) -> GenerationOutcome:
            return GenerationOutcome(program, code, message, (monotonic_ns() - start) // 1_000_000, usage)

        try:
            content = client.generate(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
                ],
                **params,
            )
        except ServingError as error:
            return done(code=error.code, message=str(error))
        try:
            proposal = json.loads(_json_text(content))
        except json.JSONDecodeError as error:
            return done(code="GENERATION_JSON_INVALID", message=str(error))
        try:
            program = PandasProgram.from_dict(proposal, dataset_names=tuple(DATASET_NAMES))
        except SchemaValidationError as error:
            return done(code="GENERATION_ENVELOPE_INVALID", message=str(error))
        return done(program=program)


class ScriptedClient:
    """Deterministic chat-client stand-in for tests and labelled fixture runs.

    It replays pre-written envelopes in order and records every request. It is
    never used by the live path and contains no answers, only code.
    """

    def __init__(self, responses: Sequence[Any]) -> None:
        self.responses = list(responses)
        self.requests: list = []

    def generate(self, messages, **params) -> str:
        self.requests.append({"messages": list(messages), "params": dict(params)})
        if not self.responses:
            raise ServingError("SCRIPT_EXHAUSTED", "no scripted response left")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
