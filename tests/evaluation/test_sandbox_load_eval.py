import threading
import time
from types import SimpleNamespace

from src.evaluation import run_sandbox_load_eval as load
from src.sandbox.pandas_protocol import PandasFailureStage, failure_result


def test_m7_level_returns_each_requests_own_result_without_leaks():
    report = load.run_level(load.m7_cases(4, 0), 2)
    assert report["outcomes"] == {"BENIGN_CORRECT": 4}
    assert report["leak_free"]


def test_t2p_abuse_is_contained_while_benign_requests_pass(monkeypatch):
    monkeypatch.setattr(load, "ABUSE_PROGRAMS", {"file_read": load.ABUSE_PROGRAMS["file_read"]})
    report = load.run_level(load.t2p_cases(4, 0, rows=20, abuse_every=2), 2)
    assert report["outcomes"] == {"BENIGN_CORRECT": 2, "ABUSE_CONTAINED": 2}
    assert report["leak_free"]


def test_cross_talk_and_escapes_are_flagged():
    benign = load.LoadCase("t2p", "benign", lambda: None, expected=7)
    other = SimpleNamespace(success=True, failure=None, result={"kind": "scalar", "value": 8})
    assert load._judge(benign, other)["outcome"] == "BENIGN_WRONG"
    abuse = load.LoadCase("t2p", "network", lambda: None, allowed_stages=frozenset({"SECURITY"}))
    assert load._judge(abuse, other)["outcome"] == "ABUSE_ESCAPED"
    crashed = failure_result("r", PandasFailureStage.INFRASTRUCTURE, "WORKER_CRASHED", "exit 1")
    assert load._judge(abuse, crashed)["outcome"] == "ABUSE_MISCLASSIFIED"


def _thread_case(target):
    def start():
        threading.Thread(target=target, daemon=True).start()
        return SimpleNamespace(success=True, failure=None, result={"kind": "scalar", "value": 1})
    return load.LoadCase("t2p", "benign", start, expected=1)


def test_transient_threads_settle_but_persistent_ones_are_leaks(monkeypatch):
    monkeypatch.setattr(load, "LEAK_SETTLE_SECONDS", 1.0)
    report = load.run_level([_thread_case(lambda: time.sleep(0.3))], 1)
    assert report["leak_free"] and report["leaks_at_level_end"]["thread_delta"] == 1
    assert report["leak_settle_ms"] >= 100
    release = threading.Event()
    try:
        report = load.run_level([_thread_case(release.wait)], 1)
        assert not report["leak_free"] and report["leaks"]["thread_delta"] == 1
    finally:
        release.set()
