import json
import threading
from types import SimpleNamespace

import pytest

from src.text_to_pandas import run
from src.text_to_pandas.generator import ScriptedClient
from tests.text_to_pandas.test_pipeline import FILTER, QUESTION, catalog, envelope  # noqa: F401 (fixture)


def test_concurrent_run_answers_every_question_and_records_load(catalog, tmp_path, monkeypatch):  # noqa: F811
    program = envelope(FILTER + "result = float(evidence['value'].iloc[0])")
    client = ScriptedClient([program] * 6)
    client.endpoint = SimpleNamespace(model_id="scripted")
    monkeypatch.setattr(run, "provider_client", lambda *args, **kwargs: client)
    monkeypatch.setattr(run, "provider_report", lambda _: {"name": "fake", "protocol": "openai-chat", "pricing": None})
    monkeypatch.setattr(run, "DatasetCatalog", lambda root: catalog)
    questions = tmp_path / "questions.jsonl"
    questions.write_text("".join(json.dumps({"question_id": f"q{i}", "question": QUESTION}) + "\n" for i in range(6)))
    output = tmp_path / "runs.json"

    assert run.main(["--catalog", "unused", "--questions", str(questions), "--history-root", str(tmp_path / "h"),
                     "--output", str(output), "--concurrency", "3"]) == 0

    report = json.loads(output.read_text())
    assert report["status"] == "COMPLETED" and report["concurrency"] == 3 and report["wall_clock_s"] >= 0
    assert sorted(report["responses"]) == [f"q{i}" for i in range(6)]
    assert {response["status"] for response in report["responses"].values()} == {"ANSWERED"}
    assert len({response["run_id"] for response in report["responses"].values()}) == 6


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "provider_client",
                        lambda *args, **kwargs: SimpleNamespace(endpoint=SimpleNamespace(model_id="fake")))
    monkeypatch.setattr(run, "provider_report", lambda _: {"name": "fake", "protocol": "openai-chat", "pricing": None})
    monkeypatch.setattr(run, "DatasetCatalog", lambda _: SimpleNamespace(catalog_id="fake"))
    monkeypatch.setattr(run, "PandasPipeline", lambda *args, **kwargs: SimpleNamespace(ask=lambda question, **kw: {
        "request_id": kw["request_id"], "question": question, "status": "ANSWERED", "reason_code": None}))
    monkeypatch.setattr(run, "source_fingerprint", lambda: "unchanged")
    questions = tmp_path / "questions.jsonl"
    questions.write_text("".join(json.dumps({"question_id": f"q{i}", "question": f"question {i}"}) + "\n"
                                 for i in range(3)))
    output = tmp_path / "runs.json"
    return ["--catalog", "unused", "--questions", str(questions), "--history-root", str(tmp_path / "h"),
            "--output", str(output)], output


def test_pool_overlaps_requests_without_mixing_results(runner, monkeypatch):
    args, output = runner
    barrier = threading.Barrier(3, timeout=10)

    def ask(question, *, request_id):
        barrier.wait()
        return {"request_id": request_id, "question": question, "status": "ANSWERED", "reason_code": None}

    monkeypatch.setattr(run, "PandasPipeline", lambda *args, **kwargs: SimpleNamespace(ask=ask))
    assert run.main(args + ["--concurrency", "3"]) == 0
    report = json.loads(output.read_text())
    assert not report["runner_errors"]
    for i in range(3):
        assert report["responses"][f"q{i}"]["request_id"] == f"q{i}"
        assert report["responses"][f"q{i}"]["question"] == f"question {i}"


def test_source_change_during_last_request_invalidates_run(runner, monkeypatch):
    args, output = runner
    fingerprints = iter(["unchanged"] * 4 + ["changed"])
    monkeypatch.setattr(run, "source_fingerprint", lambda: next(fingerprints))
    with pytest.raises(RuntimeError, match="source changed"):
        run.main(args)
    report = json.loads(output.read_text())
    assert report["status"] == "SOURCE_CHANGED"
    assert len(report["responses"]) == 3


def test_resume_skips_completed_requests_and_rejects_changed_concurrency(runner, monkeypatch):
    args, output = runner
    assert run.main(args) == 0
    assert json.loads(output.read_text())["concurrency"] == 1
    monkeypatch.setattr(run, "PandasPipeline", lambda *args, **kwargs: SimpleNamespace(ask=lambda *a, **kw: pytest.fail("already answered")))
    assert run.main(args + ["--resume"]) == 0
    before = output.read_bytes()
    with pytest.raises(ValueError, match="concurrency"):
        run.main(args + ["--resume", "--concurrency", "3"])
    assert output.read_bytes() == before


def test_concurrent_errors_keep_other_responses_and_hide_exception_message(runner, monkeypatch):
    args, output = runner

    def ask(question, *, request_id):
        if request_id == "q1":
            raise RuntimeError("secret provider message")
        return {"request_id": request_id, "question": question, "status": "ANSWERED", "reason_code": None}

    monkeypatch.setattr(run, "PandasPipeline", lambda *args, **kwargs: SimpleNamespace(ask=ask))
    assert run.main(args + ["--concurrency", "3"]) == 1
    report = json.loads(output.read_text())
    assert set(report["responses"]) == {"q0", "q2"}
    assert report["runner_errors"] == {"q1": "RuntimeError"}
    assert "secret" not in output.read_text()


@pytest.mark.parametrize("concurrency", ["0", "-1"])
def test_invalid_concurrency_rejected_before_loading_catalog(runner, concurrency):
    args, output = runner
    with pytest.raises(ValueError, match="at least 1"):
        run.main(args + ["--concurrency", concurrency])
    assert not output.exists()
