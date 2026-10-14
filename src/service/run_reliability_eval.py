"""API reliability drill: SLO, monitoring, alerting and rollback, end to end (ADR-072).

Runs the real HTTP service on loopback over the real pipeline, catalog and
sandbox. The generator replays programs a live model produced for the same
questions (``src/service/replay.py``), so the drill is deterministic and free;
releases differ only in what they pin. Simulated time advances one minute per
traffic batch so the multi-hour alert windows can be exercised in minutes.

Steps, each recorded as a check:

1. deploy release A (good) → canary passes → promoted, service ready;
2. normal traffic → every request typed, metrics consistent, SLO met, no alert;
3. seven minutes above the admission limit → 503 OVERLOADED; the overload alert
   stays silent for one burst and fires once the overload lasts five minutes;
4. deploy B with a wrong catalog id → PREFLIGHT_FAILED, A keeps serving;
5. deploy C whose programs compute wrong values → CANARY_FAILED, A keeps serving;
6. deploy D (passes its canary, but the provider is down for live traffic) →
   promoted, failures burn the error budget, the page alert fires, automatic
   rollback restores A and the service reloads it, traffic recovers and the
   alert clears;
7. manual rollback / re-promotion through the store CLI path.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.service.alerting import AlertEvaluator
from src.service.app import Engine, QuestionService, build_engine, serve
from src.service.metrics import load_slo, slo_report
from src.service.release import Release, ReleaseStore, deploy, smoke_matches, watch_and_rollback
from src.service.replay import FailingClient
from src.text_to_pandas.run import source_fingerprint

SCHEMA_VERSION = "api-reliability-drill-v1"
PAGE_ALERTS = ("AifinAvailabilityBudgetBurnFast", "AifinNotReady")


class SimClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _post(url: str, body: Mapping[str, Any]) -> tuple:
    request = Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")
    except (URLError, ConnectionError) as error:  # the client saw no HTTP answer at all
        return 599, {"status": "CONNECTION_ERROR", "reason_code": type(error).__name__}


def _get(url: str) -> tuple:
    try:
        with urlopen(url, timeout=30) as response:
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode()


def smoke_set(gold_dir: Path, runs: Mapping[str, Any], count: int = 8) -> List[Dict[str, Any]]:
    """Positives across operations with gold values, plus two refusals, from the dev split."""
    cases = [json.loads(line) for line in (gold_dir / "gold.jsonl").read_text(encoding="utf-8").splitlines() if line]
    picked, seen = [], set()
    for case in cases:
        key = (case["operation"], case["metric"])
        if key not in seen and len(picked) < count:
            seen.add(key)
            picked.append({"question": case["question"], "expected": {"status": "ANSWERED",
                                                                      "value": case["gold_answer"]["value"]}})
    negatives = [json.loads(line) for line in (gold_dir / "negative.jsonl").read_text(encoding="utf-8").splitlines()]
    for negative in negatives[:2]:
        status = runs["responses"][negative["question_id"]]["status"]
        picked.append({"question": negative["question"], "expected": {"status": status}})
    return picked


def make_checker(smoke: Sequence[Mapping[str, Any]], history_root: Path):
    def checker(release: Release) -> List[Dict[str, Any]]:
        engine = build_engine(release, history_root)
        results = []
        for item in smoke:
            response = engine.pipeline.ask(item["question"])
            results.append({"question": item["question"][:80], "expected": item["expected"],
                            "status": response["status"], "passed": smoke_matches(response, item["expected"])})
        return results
    return checker


def corrupt_history(source: Path, target: Path) -> None:
    """Copy a run's history with every program's result scaled: a bad prompt/model release."""
    shutil.copytree(source, target)
    for response_file in target.glob("*/response.json"):
        response = json.loads(response_file.read_text(encoding="utf-8"))
        for attempt in response.get("attempts", []):
            if attempt.get("program") and attempt["program"].get("code"):
                attempt["program"]["code"] += "\nresult = result * 1.1 if result is not None else result\n"
        response_file.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")


def drill(*, catalog: Path, history: Path, gold_dir: Path, dev_runs: Path, work: Path, batch: int = 20,
          concurrency: int = 8) -> Dict[str, Any]:
    runs = json.loads(dev_runs.read_text(encoding="utf-8"))
    questions = [response["question"] for response in runs["responses"].values()]
    catalog_id = json.loads((catalog / "manifest.json").read_text(encoding="utf-8"))["catalog_id"]
    fingerprint = source_fingerprint()
    store = ReleaseStore(work / "releases")
    pipeline_history = work / "history"
    clock = SimClock()
    smoke = smoke_set(gold_dir, runs)
    checker = make_checker(smoke, pipeline_history)

    def engine_factory(release: Release) -> Engine:
        engine = build_engine(release, pipeline_history)
        if release.notes == "provider-outage-in-production":
            # Canary used recorded programs; live traffic meets a provider outage.
            from src.text_to_pandas.generator import PandasCodeGenerator

            engine.pipeline.generator = PandasCodeGenerator(FailingClient())
        return engine

    service = QuestionService(store, engine_factory=engine_factory, max_in_flight=concurrency,
                              request_timeout_s=120, clock=clock)
    evaluator = AlertEvaluator(load_slo())
    checks: Dict[str, Any] = {}
    timeline: List[Dict[str, Any]] = []

    def release(release_id: str, generator: str, *, catalog_id_override: Optional[str] = None,
                notes: str = "") -> Release:
        return Release(release_id, str(catalog), catalog_id_override or catalog_id, generator, fingerprint,
                       notes=notes)

    a = release("rel-a", f"replay:{history}")
    result_a = deploy(store, a, checker, source_fingerprint=fingerprint)
    service.reload()
    server = serve(service, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        checks["A_canary_promoted"] = result_a["promoted"] and store.current_id() == "rel-a"
        checks["readyz_ok"] = _get(f"{base}/readyz")[0] == 200 and _get(f"{base}/healthz")[0] == 200

        def traffic(count: int, *, offset: int = 0, clients: int = concurrency) -> List[tuple]:
            items = [questions[(offset + i) % len(questions)] for i in range(count)]
            with ThreadPoolExecutor(max_workers=clients) as pool:
                return list(pool.map(lambda q: _post(f"{base}/v1/ask", {"question": q}), items))

        def tick(label: str, replies: List[tuple]) -> List[str]:
            clock.now += 60
            snapshot = service.metrics.snapshot()
            firing = evaluator.evaluate(samples + [snapshot]) if samples else []
            samples.append(snapshot)
            codes: Dict[str, int] = {}
            for code, body in replies:
                key = f"{code}:{body.get('status')}"
                codes[key] = codes.get(key, 0) + 1
            timeline.append({"t_min": clock.now / 60, "phase": label, "serving": store.current_id(),
                             "codes": codes, "firing": firing})
            return firing

        samples: List[Dict[str, float]] = [service.metrics.snapshot()]
        normal_alerts = set()
        for index in range(6):
            replies = traffic(batch, offset=index * batch)
            normal_alerts.update(tick("normal", replies))
        normal_events = list(service.events)
        normal_slo = slo_report(normal_events, service.slo)
        typed = all(event["http_status"] == 200 and event["status"] != "FAILED" for event in normal_events)
        metrics_text = _get(f"{base}/metrics")[1]
        checks["normal_traffic_typed"] = typed
        checks["normal_slo_met"] = normal_slo["met"]
        checks["no_alert_in_normal_traffic"] = not normal_alerts
        checks["metrics_exposed"] = (f'aifin_slo_events_total{{kind="total"}} {len(normal_events)}' in metrics_text
                                     and 'aifin_release_info{release="rel-a"} 1' in metrics_text)

        # Sustained overload: more simultaneous requests than slots for seven minutes
        # (the ticket alert waits five minutes, so a single burst stays silent).
        overloaded, typed_burst, overload_alerts, first_tick_alerts = 0, True, set(), None
        for minute in range(7):
            burst = traffic(concurrency * 4, offset=7 + minute, clients=concurrency * 4)
            overloaded += sum(code == 503 and body.get("reason_code") == "OVERLOADED" for code, body in burst)
            typed_burst &= all(code in (200, 503) for code, _ in burst)
            firing = tick("overload", burst)
            first_tick_alerts = firing if first_tick_alerts is None else first_tick_alerts
            overload_alerts.update(firing)
        checks["overload_rejected_with_503"] = overloaded > 0
        checks["overload_alert_waits_for_duration"] = "AifinOverloaded" not in first_tick_alerts
        checks["overload_alert_fired"] = "AifinOverloaded" in overload_alerts
        checks["overload_is_typed_not_wrong"] = typed_burst
        for _ in range(65):  # an hour of quiet lets every window forget the burst
            tick("quiet", [])
        checks["alerts_clear_after_overload"] = not timeline[-1]["firing"]

        b = release("rel-b", f"replay:{history}", catalog_id_override="0" * 64)
        result_b = deploy(store, b, checker, source_fingerprint=fingerprint)
        checks["B_preflight_rejected"] = (not result_b["promoted"] and result_b["reason"] == "PREFLIGHT_FAILED"
                                          and store.current_id() == "rel-a")

        corrupt = work / "history-corrupt"
        corrupt_history(history, corrupt)
        c = release("rel-c", f"replay:{corrupt}")
        result_c = deploy(store, c, checker, source_fingerprint=fingerprint)
        checks["C_canary_rejected"] = (not result_c["promoted"] and result_c["reason"] == "CANARY_FAILED"
                                       and store.current_id() == "rel-a")
        checks["serving_unchanged_after_rejections"] = service.metrics.release == "rel-a"

        d = release("rel-d", f"replay:{history}", notes="provider-outage-in-production")
        result_d = deploy(store, d, checker, source_fingerprint=fingerprint)
        service.reload()
        checks["D_canary_promoted"] = result_d["promoted"] and service.metrics.release == "rel-d"
        rolled_back_to, paged_at = None, None
        for index in range(8):
            replies = traffic(batch, offset=index * batch)
            firing = tick("bake rel-d" if rolled_back_to is None else "after rollback", replies)
            if rolled_back_to is None:
                rolled_back_to = watch_and_rollback(store, firing, page_alerts=PAGE_ALERTS)
                if rolled_back_to:
                    paged_at = clock.now / 60
                    service.reload()
        checks["page_alert_fired_on_failed_release"] = paged_at is not None
        checks["automatic_rollback_to_A"] = rolled_back_to == "rel-a" and service.metrics.release == "rel-a"
        recovery = traffic(batch, offset=3)
        checks["traffic_recovered_after_rollback"] = all(code == 200 and body.get("status") != "FAILED"
                                                         for code, body in recovery)
        for _ in range(70):  # an hour of healthy traffic lets the burn-rate windows clear
            tick("recovered", [])
        clock.now += 60
        checks["alert_cleared_after_recovery"] = not evaluator.evaluate(samples + [service.metrics.snapshot()])

        # Manual rollback path (operator CLI): A -> previous served release, then re-promote A.
        store.promote("rel-a", "operator re-promote")
        manual = store.rollback("operator drill")
        service.reload()
        store.promote("rel-a", "operator restore")
        service.reload()
        checks["manual_rollback_and_restore"] = manual in ("rel-d", "rel-a") and service.metrics.release == "rel-a"
    finally:
        server.shutdown()
        server.server_close()
        service.close()
    slo = load_slo()
    return {
        "schema_version": SCHEMA_VERSION, "passed": all(checks.values()), "checks": checks,
        "slo": {"availability_target": slo.availability_target, "latency_threshold_s": slo.latency_threshold_s,
                "latency_target": slo.latency_target, "window_days": slo.window_days,
                "normal_traffic": normal_slo},
        "alerts": [rule.name for rule in evaluator.rules],
        "rollback": {"history": store.history()},
        "deploys": {"A": result_a, "B": result_b, "C": {k: v for k, v in result_c.items() if k != "smoke"},
                    "D": {k: v for k, v in result_d.items() if k != "smoke"}},
        "timeline": timeline,
        "summary": {"checks_passed": sum(bool(v) for v in checks.values()), "checks": len(checks)},
        "measurement_scope": ("in-process HTTP service on loopback, real pipeline/catalog/sandbox, replayed model "
                              "programs, simulated clock for alert windows; no Prometheus/Alertmanager deployment"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--history", required=True, help="history root of a completed live run")
    parser.add_argument("--dev-runs", required=True)
    parser.add_argument("--gold-dir", default="gold/production-v1/dev")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="aifin-drill-") as directory:
        report = drill(catalog=Path(args.catalog), history=Path(args.history), gold_dir=Path(args.gold_dir),
                       dev_runs=Path(args.dev_runs), work=Path(directory))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
