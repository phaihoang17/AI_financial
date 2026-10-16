"""Bounded editorial suggestions for a statement-set PDF."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping


SYSTEM_PROMPT = Path(__file__).with_name("system_prompt.md").read_text(encoding="utf-8")
_FORBIDDEN = re.compile(r"\d|kiểm toán|lợi nhuận|doanh thu|tăng trưởng|chữ ký|đã công bố", re.IGNORECASE)
_FORM_CODES = re.compile(r"\bB0[1239](?:-DN)?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Draft:
    preface: str
    heading_indexes: frozenset[int]


def draft_statement(statement_set: Mapping[str, Any], client: Any) -> Draft:
    """Ask the model for prose and source-block indexes, never statement values."""
    # shortcut: classify the first 100 short OCR blocks; batch later if long B09 needs full heading coverage.
    candidates = [
        {"index": index, "text": block["text"]}
        for index, block in enumerate(statement_set["notes"])
        if block["kind"] == "text" and 3 <= len(block["text"].strip()) <= 120
    ][:100]
    payload = {
        "company_name": statement_set["company_name"],
        "ticker": statement_set["ticker"],
        "fiscal_year": statement_set["fiscal_year"],
        "statement_scope": statement_set["statement_scope"],
        "reconciliation_status": statement_set["status"],
        "candidates": candidates,
    }
    content = client.generate(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        response_format={"type": "json_object"}, max_tokens=1024,
        thinking={"type": "disabled"}, temperature=0.0,
    )
    try:
        result = json.loads(content)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("AI_DRAFT_INVALID") from error
    if not isinstance(result, dict) or set(result) != {"preface", "heading_indexes"}:
        raise ValueError("AI_DRAFT_INVALID")
    preface, indexes = result["preface"], result["heading_indexes"]
    allowed = {item["index"] for item in candidates}
    if (not isinstance(preface, str) or not 20 <= len(preface.strip()) <= 300
            or "\n" in preface or _FORBIDDEN.search(_FORM_CODES.sub("", preface))
            or not isinstance(indexes, list) or len(indexes) > len(allowed)
            or any(type(index) is not int or index not in allowed for index in indexes)
            or len(indexes) != len(set(indexes))):
        raise ValueError("AI_DRAFT_INVALID")
    return Draft(preface.strip(), frozenset(indexes))
