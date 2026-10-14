import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from src.service.alerting import AlertEvaluator, render_prometheus_rules
from src.service.app import Engine, QuestionService, serve
from src.service.metrics import ServiceMetrics, is_bad_event, load_slo, slo_report
from src.service.release import Release, ReleaseStore, deploy, smoke_matches, watch_and_rollback

ROOT = Path(__file__).resolve().parents[2]


def test_alert_rules_file_is_generated_from_the_single_definition():
    assert (ROOT / "deploy" / "service" / "alerts.yml").read_text() == render_prometheus_rules()


@pytest.mark.parametrize("http,status,reason,bad", [
    (200, "ANSWERED", None, False), (200, "NO_DATA", "REFERENCE_INPUT_UNAVAILABLE", False),
    (200, "FAILED", "SERVING_TRANSPORT_FAILED", True), (200, "FAILED", "SOURCE_ROWS", False),
    (503, "FAILED", "OVERLOADED", True), (500, "FAILED", "INTERNAL_ERROR", True), (400, "INVALID_REQUEST", None, False),
])
def test_availability_counts_only_service_faults(http, status, reason, bad):
    assert is_bad_event(http, status, reason) is bad


def test_metrics_exposition_and_slo_report():
    metrics = ServiceMetrics(clock=lambda: 0.0)
    for _ in range(3):
        metrics.started()
        metrics.finished(200, "ANSWERED", None, 1.2, latency_threshold_s=8)
    metrics.started()
    metrics.finished(200, "FAILED", "SERVING_TRANSPORT_FAILED", 0.1, latency_threshold_s=8)
    text = metrics.render()
    assert 'aifin_slo_events_total{kind="bad"} 1' in text and "aifin_answered_total 3" in text
    assert 'aifin_request_duration_seconds_bucket{le="2.0"} 4' in text and "aifin_in_flight 0" in text
    events = [{"http_status": 200, "status": "ANSWERED", "seconds": 1.0}] * 999 + \
             [{"http_status": 503, "status": "FAILED", "reason": "OVERLOADED", "seconds": 0.0}]
    report = slo_report(events, load_slo())
    assert report["availability"] == 0.999 and report["met"] and report["error_budget_spent"] == pytest.approx(0.2)


def _samples(minutes_bad, minutes_total=90, per_minute=10):
    samples, total, bad = [], 0, 0
    for minute in range(minutes_total + 1):
        samples.append({"t": minute * 60.0, "total": total, "bad": bad, "answered": total - bad, "slow_answers": 0,
                        "ready": 1.0, "rejected_overload": 0, "security_events": 0})
        total += per_minute
        bad += per_minute if minute >= minutes_total - minutes_bad else 0
    return samples


def test_fast_burn_pages_only_when_both_windows_burn_for_two_minutes():
    evaluator = AlertEvaluator()
    fired = []
    samples = _samples(minutes_bad=10)
    for index in range(1, len(samples) + 1):
        fired.append(evaluator.evaluate(samples[:index]))
    first = next(i for i, names in enumerate(fired) if "AifinAvailabilityBudgetBurnFast" in names)
    assert 80 <= first <= 90  # bad traffic starts at minute 80; the 1h ratio crosses 7.2%, then for: 2m
    assert not any("AifinAvailabilityBudgetBurnFast" in names for names in fired[:first])
    healthy = AlertEvaluator()
    assert not any(healthy.evaluate(_samples(minutes_bad=0)[:i]) for i in range(1, 92))


def test_not_ready_and_security_events_alert():
    evaluator = AlertEvaluator()
    base = {"total": 0, "bad": 0, "answered": 0, "slow_answers": 0, "rejected_overload": 0}
    samples = [{"t": 0.0, "ready": 0.0, "security_events": 0, **base},
               {"t": 61.0, "ready": 0.0, "security_events": 1, **base}]
    evaluator.evaluate(samples[:1])
    assert {"AifinNotReady", "AifinSandboxSecurityEvent"} <= set(evaluator.evaluate(samples))


def _release(tmp_path, name, catalog_id="c" * 64):
    catalog = tmp_path / f"catalog-{name}"
    catalog.mkdir(exist_ok=True)
    (catalog / "manifest.json").write_text(json.dumps({"catalog_id": "c" * 64}))
    history = tmp_path / "history"
    history.mkdir(exist_ok=True)
    return Release(name, str(catalog), catalog_id, f"replay:{history}", "fp")


def test_deploy_promotes_only_after_preflight_and_canary_then_rolls_back(tmp_path):
    store = ReleaseStore(tmp_path / "store")
    passing = lambda release: [{"passed": True}]
    assert deploy(store, _release(tmp_path, "a"), passing, source_fingerprint="fp")["promoted"]
    rejected = deploy(store, _release(tmp_path, "b", catalog_id="0" * 64), passing, source_fingerprint="fp")
    assert rejected["reason"] == "PREFLIGHT_FAILED" and store.current_id() == "a"
    stale = deploy(store, _release(tmp_path, "b2"), passing, source_fingerprint="other-code")
    assert stale["reason"] == "PREFLIGHT_FAILED"
    canary = deploy(store, _release(tmp_path, "c"), lambda release: [{"passed": False}], source_fingerprint="fp")
    assert canary["reason"] == "CANARY_FAILED" and store.current_id() == "a"
    assert deploy(store, _release(tmp_path, "d"), passing, source_fingerprint="fp")["promoted"]
    assert watch_and_rollback(store, ["AifinOverloaded"], page_alerts=["AifinAvailabilityBudgetBurnFast"]) is None
    assert watch_and_rollback(store, ["AifinAvailabilityBudgetBurnFast"],
                              page_alerts=["AifinAvailabilityBudgetBurnFast"]) == "a"
    assert store.current_id() == "a"
    events = [row["event"] for row in store.history()]
    assert events.count("REJECTED") == 3 and events[-1] == "ROLLED_BACK"
    with pytest.raises(ValueError):
        store.register(Release("a", "elsewhere", "c" * 64, "replay:x", "fp"))


def test_smoke_matching_requires_status_and_value():
    assert smoke_matches({"status": "ANSWERED", "result": {"value": 100.0000001}}, {"status": "ANSWERED", "value": "100"})
    assert not smoke_matches({"status": "ANSWERED", "result": {"value": 110}}, {"status": "ANSWERED", "value": "100"})
    assert smoke_matches({"status": "NO_DATA"}, {"status": "NO_DATA"})


class _Companies:
    def to_dict(self, orient):
        assert orient == "records"
        return [{"ticker": "AAA", "company_name": "CTCP Nhựa An Phát Xanh", "template": "CORPORATE",
                 "first_year": 2015, "last_year": 2025, "statement_scopes": "HOP_NHAT,RIENG"}]


class _Pipeline:
    def __init__(self, behaviour="answer", delay=0.0):
        self.catalog = SimpleNamespace(catalog_id="c" * 64, companies=_Companies(),
                                       manifest={"build_version": "t2p-catalog-build-v4", "report_count": 1887})
        self.behaviour, self.delay = behaviour, delay

    def ask(self, question, *, request_id=None):
        time.sleep(self.delay)
        if self.behaviour == "raise":
            raise RuntimeError("boom")
        return {"request_id": request_id, "status": "ANSWERED", "reason_code": None, "result": {"value": 1},
                "unit": "VND", "sources": [{"fact_id": "f", "source_cell_id": "cell", "statement": "BALANCE_SHEET",
                                            "secret": "x"}],
                "attempts": [{"outcome": "VERIFICATION", "program": {"code": "first"}},
                             {"outcome": "PASSED", "program": {"code": "hidden", "explanation": "why"}}]}


@pytest.fixture
def service(tmp_path):
    store = ReleaseStore(tmp_path / "store")
    release = _release(tmp_path, "a")
    store.register(release)
    store.promote("a", "test")
    pipelines = {"a": _Pipeline()}
    service = QuestionService(store, engine_factory=lambda rel: Engine(rel, pipelines[rel.release_id]),
                              max_in_flight=2, request_timeout_s=0.5, api_token="token")
    service.reload()
    server = serve(service, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield service, f"http://127.0.0.1:{server.server_address[1]}", pipelines, store, tmp_path
    server.shutdown()
    server.server_close()
    service.close()


def _call(url, body=None, token="token", with_headers=False):
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    request = Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            result = response.status, response.read(), response.headers
    except HTTPError as error:
        result = error.code, error.read(), error.headers
    return result if with_headers else result[:2]


def test_http_api_answers_and_hides_internals(service):
    svc, base, _, _, _ = service
    code, body = _call(f"{base}/v1/ask", {"question": "q", "request_id": "r1"})
    body = json.loads(body)
    assert code == 200 and body["status"] == "ANSWERED" and body["release"] == "a"
    assert body["sources"] == [{"fact_id": "f", "source_cell_id": "cell", "statement": "BALANCE_SHEET"}]
    assert "attempts" not in body and "program" not in body
    assert _call(f"{base}/v1/ask", {"question": "q"}, token=None)[0] == 401
    assert _call(f"{base}/v1/ask", {"nope": 1})[0] == 400
    assert _call(f"{base}/healthz")[0] == 200 and _call(f"{base}/readyz")[0] == 200
    assert b"aifin_requests_total" in _call(f"{base}/metrics")[1]


def test_timeouts_errors_overload_and_reload(service):
    svc, base, pipelines, store, tmp_path = service
    pipelines["a"].behaviour = "raise"
    code, body = _call(f"{base}/v1/ask", {"question": "q"})
    assert code == 500 and json.loads(body)["message"] == "RuntimeError"  # no internal details
    pipelines["a"].behaviour, pipelines["a"].delay = "answer", 1.0
    assert json.loads(_call(f"{base}/v1/ask", {"question": "q"})[1])["reason_code"] == "TIMEOUT"
    pipelines["a"].delay = 0.4
    results = []
    threads = [threading.Thread(target=lambda: results.append(_call(f"{base}/v1/ask", {"question": "q"})[0]))
               for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert 503 in results and 200 in results and svc.metrics.rejected_overload >= 1
    pipelines["a"].delay = 0.0
    release_b = _release(tmp_path, "b")
    store.register(release_b)
    store.promote("b", "test")
    pipelines["b"] = _Pipeline()
    assert json.loads(_call(f"{base}/admin/reload", {})[1]) == {"serving": "b"}
    assert json.loads(_call(f"{base}/v1/ask", {"question": "q"})[1])["release"] == "b"


def test_a_burst_gets_answers_or_503s_never_connection_resets(service):
    svc, base, pipelines, _, _ = service
    pipelines["a"].delay = 0.2
    codes = []
    lock = threading.Lock()

    def one():
        try:
            code = _call(f"{base}/v1/ask", {"question": "q"})[0]
        except OSError as error:  # a reset or refused connection
            code = type(error).__name__
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=one) for _ in range(48)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(codes) <= {200, 503} and 503 in codes


def test_program_is_exposed_only_for_an_analyst_deployment(service):
    svc, base, _, _, _ = service
    svc.expose_program = True
    body = json.loads(_call(f"{base}/v1/ask", {"question": "q"})[1])
    assert body["program"] == {"code": "hidden", "explanation": "why", "attempts": 2} and "attempts" not in body


def test_catalog_endpoint_lists_coverage_behind_the_token(service):
    _, base, _, _, _ = service
    assert _call(f"{base}/v1/catalog", token=None)[0] == 401
    code, body = _call(f"{base}/v1/catalog")
    body = json.loads(body)
    assert code == 200 and body["release"] == "a" and body["catalog_id"] == "c" * 64 and body["report_count"] == 1887
    assert body["companies"] == [{"ticker": "AAA", "company_name": "CTCP Nhựa An Phát Xanh", "template": "CORPORATE",
                                  "first_year": 2015, "last_year": 2025, "statement_scopes": ["HOP_NHAT", "RIENG"]}]
    assert body["metrics"]["NET_REVENUE"] == "Doanh thu thuần"


def test_web_ui_is_static_public_and_locked_down(service):
    _, base, _, _, _ = service
    code, page, headers = _call(f"{base}/?from=bookmark", token=None, with_headers=True)
    assert code == 200 and headers["Content-Type"].startswith("text/html") and b'src="/static/app.js"' in page
    assert "script-src 'self'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"
    for name, kind in (("app.js", "text/javascript"), ("app.css", "text/css")):
        code, data, headers = _call(f"{base}/static/{name}", token=None, with_headers=True)
        assert code == 200 and headers["Content-Type"].startswith(kind) and data
    # Only the fixed file map is served: no traversal, no other files.
    for path in ("/static/../app.py", "/static/%2e%2e/app.py", "/static/index.html", "/web/app.js"):
        assert _call(f"{base}{path}", token=None)[0] == 404
