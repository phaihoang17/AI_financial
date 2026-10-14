"""HTTP question API over the Text-to-Pandas pipeline (ADR-072).

Endpoints (JSON):

- ``POST /v1/ask`` ``{"question": "...", "request_id": "..."}`` → the typed answer
  or refusal with its sources; ``503 OVERLOADED`` when every slot is busy,
  ``504 TIMEOUT`` past the request deadline, ``401`` without the bearer token
  (when ``AIFIN_API_TOKEN`` is set);
- ``GET /v1/catalog`` → the serving catalog's coverage (companies, years, statement
  scopes, metric names) for clients; same bearer token as ``/v1/ask``;
- ``GET /v1/statements?ticker=AAA&year=2015&scope=HOP_NHAT`` → the reconstructed
  statement set (B01/B02/B03/B09 + reconciliation appendix) as a PDF download
  (ADR-075); ``404`` no such report, ``422 UNSUPPORTED_TEMPLATE`` for bank and
  securities forms, ``503`` when the release pins no statement set or renders are busy;
- ``GET /`` and ``GET /static/<file>`` → the browser UI (``src/service/web/``); it
  holds no data and calls the JSON endpoints above;
- ``GET /healthz`` (process alive), ``GET /readyz`` (a release is loaded and its
  catalog id matches), ``GET /metrics`` (Prometheus text, see
  ``src/service/metrics.py``), ``GET /slo`` (SLO attainment over recent requests);
- ``POST /admin/reload`` → load the store's current release (after a promotion
  or rollback) and swap it in atomically; in-flight requests finish on the old one.

Run: ``python -m src.service.app --release-root deploy/releases --port 8080``.
Bind to loopback and put TLS/auth in front of it for anything public.
Answers never carry the generated program unless ``AIFIN_EXPOSE_PROGRAM=1`` (an
analyst deployment): then an answered body adds ``program`` (code, explanation).
"""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from src.service.metrics import SLO, ServiceMetrics, load_slo, slo_report
from src.service.release import Release, ReleaseStore

MAX_BODY_BYTES = 16 * 1024
MAX_PDF_RENDERS = 2  # a PDF render takes seconds of CPU; more requests get 503
_TICKER = re.compile(r"[A-Z0-9]{1,10}")
TOKEN_ENV = "AIFIN_API_TOKEN"
EXPOSE_PROGRAM_ENV = "AIFIN_EXPOSE_PROGRAM"
SOURCE_KEYS = ("fact_id", "report_id", "source_ref", "statement", "table_id", "source_cell_id", "page_id",
               "raw_text", "label_text")
WEB_ROOT = Path(__file__).with_name("web")
# A fixed map: request paths never reach the filesystem.
WEB_FILES = {"/": ("index.html", "text/html"), "/static/app.css": ("app.css", "text/css"),
             "/static/app.js": ("app.js", "text/javascript")}
WEB_HEADERS = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
               "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
                                          "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                                          "form-action 'none'; frame-ancestors 'none'"}
log = logging.getLogger("aifin.service")


@dataclass(frozen=True)
class Engine:
    release: Release
    pipeline: Any
    statements: Any = None  # StatementStore when the release pins a statement set


def build_engine(release: Release, history_root: str | Path) -> Engine:
    """The pipeline a release pins: its catalog and its generator."""
    from src.text_to_pandas.catalog import DatasetCatalog
    from src.text_to_pandas.generator import PandasCodeGenerator
    from src.text_to_pandas.pipeline import PandasPipeline

    catalog = DatasetCatalog(release.catalog)
    if catalog.catalog_id != release.catalog_id:
        raise RuntimeError("catalog does not match the release")
    if release.generator.startswith("replay:"):
        from src.service.replay import ReplayClient, load_programs

        client: Any = ReplayClient(load_programs(release.generator[len("replay:"):]))
    else:
        from src.serving.providers import provider_client

        client = provider_client(release.generator[len("provider:"):],
                                 **({"config_path": release.llm_config} if release.llm_config else {}))
    statements = None
    if release.statements:
        from src.statements.store import StatementStore

        statements = StatementStore(release.statements)
        if statements.statements_id != release.statements_id:
            raise RuntimeError("statement set does not match the release")
    return Engine(release, PandasPipeline(catalog, PandasCodeGenerator(client), history_root=history_root),
                  statements)


def public_response(response: dict[str, Any], *, expose_program: bool = False) -> dict[str, Any]:
    keys = ("request_id", "run_id", "status", "reason_code", "message", "result", "unit", "scale", "elapsed_ms",
            "catalog_id")
    body = {key: response.get(key) for key in keys}
    body["sources"] = [{key: source.get(key) for key in SOURCE_KEYS if key in source}
                       for source in response.get("sources", [])]
    if expose_program and response.get("status") == "ANSWERED":
        attempts = response.get("attempts", [])
        passed = [attempt["program"] for attempt in attempts if attempt.get("outcome") == "PASSED" and attempt.get("program")]
        if passed:
            body["program"] = {"code": passed[-1].get("code"), "explanation": passed[-1].get("explanation"),
                               "attempts": len(attempts)}
    return body


def catalog_summary(engine: Engine) -> dict[str, Any]:
    """Coverage a client may show or check before asking; no statement values."""
    from src.text_to_pandas.metrics import METRICS_BY_ID

    catalog = engine.pipeline.catalog
    manifest = getattr(catalog, "manifest", None) or {}
    companies = [{"ticker": str(row["ticker"]), "company_name": str(row["company_name"]),
                  "template": str(row["template"]), "first_year": int(row["first_year"]),
                  "last_year": int(row["last_year"]),
                  "statement_scopes": [scope for scope in str(row["statement_scopes"]).split(",") if scope]}
                 for row in catalog.companies.to_dict("records")]
    return {"catalog_id": catalog.catalog_id, "release": engine.release.release_id,
            "build_version": manifest.get("build_version"), "report_count": manifest.get("report_count"),
            "companies": companies,
            "metrics": {metric_id: metric.name_vi for metric_id, metric in METRICS_BY_ID.items()}}


class QuestionService:
    def __init__(self, store: ReleaseStore, *, engine_factory: Callable[[Release], Engine], slo: SLO | None = None,
                 max_in_flight: int = 32, request_timeout_s: float = 60.0, api_token: str | None = None,
                 recent: int = 10_000, clock: Callable[[], float] = time.monotonic,
                 expose_program: bool = False) -> None:
        self.store, self.engine_factory = store, engine_factory
        self.expose_program = expose_program
        self.slo = slo or load_slo()
        self.metrics = ServiceMetrics(clock)
        self.request_timeout_s = request_timeout_s
        self.api_token = api_token
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._renders = threading.BoundedSemaphore(MAX_PDF_RENDERS)
        self._pool = ThreadPoolExecutor(max_workers=max_in_flight, thread_name_prefix="aifin-ask")
        self._engine: Engine | None = None
        self._engine_lock = threading.Lock()
        self.events: deque = deque(maxlen=recent)

    # ---- release ------------------------------------------------------------
    def reload(self) -> dict[str, Any]:
        release = self.store.current()
        if release is None:
            raise RuntimeError("no promoted release")
        engine = self.engine_factory(release)  # build first: a failed build keeps the old engine
        with self._engine_lock:
            self._engine = engine
            self.metrics.release, self.metrics.ready = release.release_id, True
            self.metrics.reloads += 1
        return {"serving": release.release_id}

    def engine(self) -> Engine | None:
        with self._engine_lock:
            return self._engine

    def ready(self) -> tuple[bool, dict[str, Any]]:
        engine = self.engine()
        if engine is None:
            return False, {"ready": False, "reason": "no release loaded"}
        ok = engine.pipeline.catalog.catalog_id == engine.release.catalog_id
        return ok, {"ready": ok, "release": engine.release.release_id, "catalog_id": engine.release.catalog_id}

    # ---- ask ------------------------------------------------------------------
    def authorized(self, header: str | None) -> bool:
        if not self.api_token:
            return True
        expected = f"Bearer {self.api_token}"
        return header is not None and hmac.compare_digest(header.encode(), expected.encode())

    def _finish(self, started: float, code: int, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        seconds = time.monotonic() - started
        status, reason = body.get("status"), body.get("reason_code")
        self.metrics.finished(code, status, reason, seconds, latency_threshold_s=self.slo.latency_threshold_s)
        self.events.append({"http_status": code, "status": status, "reason": reason, "seconds": seconds})
        log.info(json.dumps({"event": "ask", "code": code, "status": status, "reason": reason,
                             "request_id": body.get("request_id"), "seconds": round(seconds, 3),
                             "release": self.metrics.release}))
        return code, body

    def ask(self, payload: Any) -> tuple[int, dict[str, Any]]:
        started = time.monotonic()
        self.metrics.started()
        if not self._slots.acquire(blocking=False):
            return self._finish(started, 503, {"status": "FAILED", "reason_code": "OVERLOADED",
                                               "message": "all question slots are busy; retry shortly"})
        try:
            engine = self.engine()
            if engine is None:
                return self._finish(started, 503, {"status": "FAILED", "reason_code": "NOT_READY",
                                                   "message": "no release is loaded"})
            if not isinstance(payload, dict) or not isinstance(payload.get("question"), str):
                return self._finish(started, 400, {"status": "INVALID_REQUEST", "reason_code": "INVALID_REQUEST",
                                                   "message": "body must be {\"question\": str, \"request_id\"?: str}"})
            request_id = payload.get("request_id") or uuid4().hex
            future = self._pool.submit(engine.pipeline.ask, payload["question"], request_id=str(request_id))
            try:
                response = future.result(timeout=self.request_timeout_s)
            except FutureTimeout:
                return self._finish(started, 504, {"request_id": request_id, "status": "FAILED",
                                                   "reason_code": "TIMEOUT", "message": "request deadline exceeded"})
            except ValueError as error:
                return self._finish(started, 400, {"request_id": request_id, "status": "INVALID_REQUEST",
                                                   "reason_code": "INVALID_REQUEST", "message": str(error)})
            except Exception as error:  # never leak internals; the type is enough to triage
                return self._finish(started, 500, {"request_id": request_id, "status": "FAILED",
                                                   "reason_code": "INTERNAL_ERROR", "message": type(error).__name__})
            body = public_response(response, expose_program=self.expose_program)
            body["release"] = engine.release.release_id
            return self._finish(started, 200, body)
        finally:
            self._slots.release()

    def catalog(self) -> tuple[int, dict[str, Any]]:
        engine = self.engine()
        if engine is None:
            return 503, {"error": "no release is loaded"}
        return 200, catalog_summary(engine)

    def statements(self, query: dict[str, list[str]]) -> tuple[int, Any, dict[str, str]]:
        """(status, PDF bytes or JSON error, headers) for one company/year/scope statement set."""
        engine = self.engine()
        store = None if engine is None else engine.statements
        if store is None:
            return 503, {"error": "STATEMENTS_NOT_CONFIGURED", "message": "Bản phát hành chưa có bộ BCTC."}, {}
        ticker = (query.get("ticker") or [""])[0].strip().upper()
        year = (query.get("year") or [""])[0].strip()
        scope = (query.get("scope") or [""])[0].strip().upper()
        if not _TICKER.fullmatch(ticker) or not year.isdigit() or len(year) != 4 or scope not in ("HOP_NHAT", "RIENG"):
            return 400, {"error": "INVALID_REQUEST",
                         "message": "Cần ticker, year (4 chữ số) và scope HOP_NHAT hoặc RIENG."}, {}
        if not self._renders.acquire(blocking=False):
            return 503, {"error": "BUSY", "message": "Đang tạo bộ BCTC khác, vui lòng thử lại."}, {}
        try:
            template, statement_set = store.get(ticker, int(year), scope)
            if template == "NOT_FOUND":
                return 404, {"error": "NOT_FOUND",
                             "message": f"Không có báo cáo {ticker} năm {year} ({scope}) trong dữ liệu."}, {}
            if statement_set is None:
                return 422, {"error": "UNSUPPORTED_TEMPLATE", "template": template,
                             "message": "Chưa hỗ trợ mẫu báo cáo này (ngân hàng / công ty chứng khoán)."}, {}
            from src.statements.pdf import render_pdf

            data = render_pdf(statement_set, statements_id=store.statements_id)
        except Exception as error:  # never leak internals; the type is enough to triage
            log.exception("statement render failed")
            return 500, {"error": "INTERNAL_ERROR", "message": type(error).__name__}, {}
        finally:
            self._renders.release()
        log.info(json.dumps({"event": "statements", "ticker": ticker, "year": year, "scope": scope,
                             "status": statement_set["status"], "release": engine.release.release_id}))
        return 200, data, {"Content-Disposition": f'attachment; filename="BCTC_{ticker}_{year}_{scope}.pdf"',
                           "X-Statement-Status": statement_set["status"], "Cache-Control": "no-store",
                           "X-Content-Type-Options": "nosniff"}

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def make_handler(service: QuestionService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "aifin"

        def log_message(self, *args: Any) -> None:  # structured logs come from the service
            pass

        def _send(self, code: int, body: Any, content_type: str = "application/json",
                  headers: dict[str, str] | None = None) -> None:
            if isinstance(body, bytes):
                data = body
            else:
                data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            binary = content_type == "application/pdf"
            self.send_header("Content-Type", content_type if binary else f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            if code == 503:
                self.send_header("Retry-After", "1")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            path = urlsplit(self.path).path
            if path in WEB_FILES:
                name, content_type = WEB_FILES[path]
                self._send(200, (WEB_ROOT / name).read_bytes(), content_type, WEB_HEADERS)
            elif path == "/v1/catalog":
                if not service.authorized(self.headers.get("Authorization")):
                    self._send(401, {"error": "unauthorized"})
                    return
                self._send(*service.catalog())
            elif path == "/v1/statements":
                if not service.authorized(self.headers.get("Authorization")):
                    self._send(401, {"error": "unauthorized"})
                    return
                code, body, headers = service.statements(parse_qs(urlsplit(self.path).query))
                self._send(code, body, "application/pdf" if code == 200 else "application/json", headers)
            elif self.path == "/healthz":
                self._send(200, {"alive": True})
            elif self.path == "/readyz":
                ok, detail = service.ready()
                self._send(200 if ok else 503, detail)
            elif self.path == "/metrics":
                self._send(200, service.metrics.render(), "text/plain; version=0.0.4")
            elif self.path == "/slo":
                self._send(200, slo_report(list(service.events), service.slo))
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            if self.path not in ("/v1/ask", "/admin/reload"):
                self._send(404, {"error": "not found"})
                return
            if not service.authorized(self.headers.get("Authorization")):
                self._send(401, {"error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send(413, {"error": "request body too large"})
                return
            raw = self.rfile.read(length)
            if self.path == "/admin/reload":
                try:
                    self._send(200, service.reload())
                except Exception as error:
                    self._send(500, {"error": type(error).__name__, "serving": service.metrics.release})
                return
            try:
                payload = json.loads(raw or b"null")
            except json.JSONDecodeError:
                payload = None
            code, body = service.ask(payload)
            self._send(code, body)

    return Handler


class _Server(ThreadingHTTPServer):
    # The stdlib default backlog (5) makes the kernel reset connections in a burst
    # before admission control can answer 503; queue them instead.
    request_queue_size = 512
    daemon_threads = True


def serve(service: QuestionService, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    return _Server((host, port), make_handler(service))


def main(argv: list | None = None) -> int:  # pragma: no cover - process entry point
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--history-root", default="scratch_logs/service-history")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--max-in-flight", type=int, default=32)
    parser.add_argument("--request-timeout-s", type=float, default=60.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    store = ReleaseStore(args.release_root)
    service = QuestionService(store, engine_factory=lambda release: build_engine(release, args.history_root),
                              max_in_flight=args.max_in_flight, request_timeout_s=args.request_timeout_s,
                              api_token=os.environ.get(TOKEN_ENV),
                              expose_program=os.environ.get(EXPOSE_PROGRAM_ENV) == "1")
    service.reload()
    server = serve(service, args.host, args.port)
    log.info(json.dumps({"event": "listening", "host": args.host, "port": server.server_address[1],
                         "release": service.metrics.release}))
    try:
        server.serve_forever()
    finally:
        service.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
