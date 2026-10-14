"""LLM paraphrases of a gold split's seed questions (ADR-072 third holdout).

The first two holdouts were worded by the same author who then fixed the
understanding, so each one exposed new wording gaps only once. Here a chat model
rewrites every neutral seed question the way an investor or analyst would ask
it, so the holdout's wording does not come from the person fixing the system.

A rewrite is kept only when it still names the same ticker, every year and
the report scope, and adds no number of its own; otherwise the model gets one
corrective retry, then the seed question is kept (counted). Answers, sources
and audits are unchanged: only the question text moves. The command prints
counts only, so the rewritten questions can be frozen without being read.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from src.text_to_pandas.metrics import fold

PROMPT = """Bạn là một nhà đầu tư/chuyên viên phân tích người Việt đang hỏi một trợ lý dữ liệu báo cáo tài chính.
Hãy viết lại câu hỏi dưới đây theo cách tự nhiên mà bạn thực sự sẽ hỏi: thay đổi từ ngữ và cấu trúc câu,
có thể dùng tên gọi khác thông dụng của chỉ tiêu hoặc chỉ số, viết tắt quen thuộc, văn nói hoặc văn viết.
Bắt buộc giữ nguyên: mã cổ phiếu, mọi năm, loại báo cáo (hợp nhất hoặc riêng), đúng chỉ tiêu/chỉ số tài chính
và đúng phép tính (tra cứu, tăng trưởng %, bình quân hai năm, hoặc chỉ số). Không thêm số liệu, giả định hay
thông tin mới. Chỉ trả về một đối tượng JSON {"question": "..."}."""
_NUMBER = re.compile(r"\d+")


def check(seed: str, rewrite: Any, ticker: str, years: Sequence[int], scope_word: str) -> str | None:
    """Why a rewrite is unusable, or None when it keeps every required element."""
    if not isinstance(rewrite, str) or not rewrite.strip() or len(rewrite) > 400:
        return "empty or overlong"
    if not re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", rewrite):
        return f"must keep the ticker {ticker}"
    missing = [str(year) for year in years if str(year) not in rewrite]
    if missing:
        return f"must keep the years {', '.join(missing)}"
    if fold(scope_word) not in fold(rewrite):
        return f"must keep the report scope '{scope_word}'"
    allowed = set(_NUMBER.findall(seed))
    added = [number for number in _NUMBER.findall(rewrite) if number not in allowed]
    if added:
        return f"must not add numbers ({', '.join(added)})"
    if fold(rewrite) == fold(seed):
        return "must change the wording"
    return None


def paraphrase_one(client: Any, seed: str, ticker: str, years: Sequence[int], scope_word: str) -> tuple[str, str]:
    """(question, source) where source is LLM, LLM_RETRY or SEED_FALLBACK."""
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": seed}]
    for attempt in range(2):
        try:
            content = client.generate(messages, response_format={"type": "json_object"}, max_tokens=300,
                                      thinking={"type": "disabled"}, temperature=0.9)
            rewrite = json.loads(content).get("question")
        except Exception as error:  # a provider failure keeps the seed, never stops the build
            return seed, f"SEED_FALLBACK:{type(error).__name__}"
        problem = check(seed, rewrite, ticker, years, scope_word)
        if problem is None:
            return rewrite.strip(), "LLM" if attempt == 0 else "LLM_RETRY"
        messages += [{"role": "assistant", "content": content},
                     {"role": "user", "content": f"Chưa đạt: {problem}. Viết lại, chỉ trả về JSON."}]
    return seed, "SEED_FALLBACK:VALIDATION"


def paraphrase_split(directory: Path, client: Any, *, concurrency: int = 8) -> dict[str, Any]:
    gold_path, audit_path = directory / "gold.jsonl", directory / "audit.json"
    cases = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    by_id = {row["question_id"]: row for row in audit["cases"]}
    scope_words = {"HOP_NHAT": "hợp nhất", "RIENG": "riêng"}

    def work(case: Mapping[str, Any]) -> tuple[str, str]:
        return paraphrase_one(client, case["question"], case["company"]["ticker"], by_id[case["question_id"]]["years"],
                              scope_words[case["statement_scope"]])

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(work, cases))
    sources: dict[str, int] = {}
    for case, (question, source) in zip(cases, results):
        by_id[case["question_id"]]["seed_question"] = case["question"]
        by_id[case["question_id"]]["phrasing_source"] = source
        case["question"] = question
        sources[source.split(":")[0]] = sources.get(source.split(":")[0], 0) + 1
    gold_bytes = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases).encode()
    gold_path.write_bytes(gold_bytes)
    negatives = (directory / "negative.jsonl").read_bytes()
    (directory / "all-questions.jsonl").write_bytes(gold_bytes + negatives)
    audit["gold_sha256"] = sha256(gold_bytes).hexdigest()
    audit["phrasing"] = {"method": "LLM paraphrase of neutral seed questions", "model": client.endpoint.model_id,
                         "prompt_sha256": sha256(PROMPT.encode()).hexdigest(), "sources": sources}
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"cases": len(cases), "sources": sources, "gold_sha256": audit["gold_sha256"]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split-dir", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    from src.serving.providers import provider_client

    client = provider_client(args.provider, env_file=args.env_file)
    print(json.dumps(paraphrase_split(Path(args.split_dir), client)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
