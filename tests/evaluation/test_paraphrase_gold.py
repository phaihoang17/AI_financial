import json

from src.evaluation.paraphrase_gold import check, paraphrase_one

SEED = "Tổng tài sản của CTCP A (AAA) tại ngày 31/12/2023 theo báo cáo tài chính hợp nhất là bao nhiêu?"


def test_rewrites_must_keep_ticker_years_scope_and_add_no_numbers():
    assert check(SEED, "AAA có quy mô tài sản bao nhiêu cuối 2023 (BCTC hợp nhất)?", "AAA", [2023], "hợp nhất") is None
    assert "ticker" in check(SEED, "Công ty A có tổng tài sản bao nhiêu năm 2023, hợp nhất?", "AAA", [2023], "hợp nhất")
    assert "years" in check(SEED, "AAA có tổng tài sản bao nhiêu (hợp nhất)?", "AAA", [2023], "hợp nhất")
    assert "scope" in check(SEED, "AAA có tổng tài sản bao nhiêu năm 2023?", "AAA", [2023], "hợp nhất")
    assert "numbers" in check(SEED, "AAA năm 2023 có tổng tài sản trên 500 tỷ không (hợp nhất)?", "AAA", [2023],
                              "hợp nhất")
    assert "wording" in check(SEED, SEED, "AAA", [2023], "hợp nhất")


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def generate(self, messages, **params):
        self.calls.append(list(messages))
        return json.dumps({"question": self.replies.pop(0)})


def test_one_corrective_retry_then_seed_fallback():
    client = Scripted("Công ty A có tài sản bao nhiêu?", "AAA cuối năm 2023 có tổng tài sản bao nhiêu (hợp nhất)?")
    question, source = paraphrase_one(client, SEED, "AAA", [2023], "hợp nhất")
    assert source == "LLM_RETRY" and "AAA" in question and "Chưa đạt" in client.calls[1][-1]["content"]
    question, source = paraphrase_one(Scripted("x", "y"), SEED, "AAA", [2023], "hợp nhất")
    assert (question, source) == (SEED, "SEED_FALLBACK:VALIDATION")
