import json

import pytest

from src.evaluation.run_live_e2e_eval import load_questions


def test_input_passes_only_question_and_id_to_inference(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text(json.dumps({"question_id": "q", "question": "LNST AAA năm 2023?",
                                "gold_answer": "hidden", "gold_table_id": "hidden"}))
    assert load_questions(path) == [("q", "LNST AAA năm 2023?")]


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text('{"id":1,"question":"a"}\n{"id":1,"question":"b"}\n')
    with pytest.raises(ValueError):
        load_questions(path)
