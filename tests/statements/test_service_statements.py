"""Statement-set PDF over the service: store integrity, release pinning and the endpoint (ADR-075)."""

import json
import threading

import pytest

from src.service.app import Engine, QuestionService, serve
from src.service.release import Release, ReleaseStore, preflight
from src.statements.builder import build_statements
from src.statements.pdf import render_pdf
from src.statements.store import StatementStore, StatementStoreError
from tests.service.test_service import _call, _Pipeline, _release
from tests.statements.test_builder import RAW, _report


@pytest.fixture
def statements_dir(tmp_path):
    _source, report = _report()
    raw_root = tmp_path / "raw"
    (raw_root / "AAA/2024").mkdir(parents=True)
    (raw_root / report["source_ref"]).write_text(RAW, encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"corpus_id": "test-corpus", "reports": [report]}), encoding="utf-8")
    out = tmp_path / "statements"
    build_statements(corpus, raw_root, out, workers=1)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["reports"]["ABB|2024|HOP_NHAT"] = {"report_id": "r-bank", "template": "BANK"}
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return out


@pytest.fixture
def served(tmp_path, statements_dir):
    store = ReleaseStore(tmp_path / "store")
    base = _release(tmp_path, "a")
    release = Release(base.release_id, base.catalog, base.catalog_id, base.generator, base.source_fingerprint,
                      statements=str(statements_dir), statements_id=StatementStore(statements_dir).statements_id)
    store.register(release)
    store.promote("a", "test")
    service = QuestionService(store, engine_factory=lambda rel: Engine(rel, _Pipeline(), StatementStore(rel.statements)),
                              api_token="token")
    service.reload()
    server = serve(service, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield service, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    service.close()


def test_pdf_holds_cover_statements_notes_and_appendix(statements_dir):
    _template, statement_set = StatementStore(statements_dir).get("AAA", 2024, "HOP_NHAT")
    data = render_pdf(statement_set, statements_id="x")
    assert data.startswith(b"%PDF-")
    assert data.count(b"/Type /Page") - data.count(b"/Type /Pages") >= 6  # cover, B01-B03, B09, appendix


def test_endpoint_serves_the_pdf_and_typed_refusals(served):
    _service, base = served
    code, body, headers = _call(f"{base}/v1/statements?ticker=aaa&year=2024&scope=HOP_NHAT", with_headers=True)
    assert code == 200 and body.startswith(b"%PDF-")
    assert headers["Content-Type"] == "application/pdf"
    assert headers["Content-Disposition"] == 'attachment; filename="BCTC_AAA_2024_HOP_NHAT.pdf"'
    assert headers["X-Statement-Status"] == "RECONCILED"
    assert _call(f"{base}/v1/statements?ticker=AAA&year=2024&scope=HOP_NHAT", token=None)[0] == 401
    assert _call(f"{base}/v1/statements?ticker=AAA&year=2024&scope=ALL")[0] == 400
    assert _call(f"{base}/v1/statements?ticker=../x&year=2024&scope=RIENG")[0] == 400
    assert _call(f"{base}/v1/statements?ticker=AAA&year=2023&scope=HOP_NHAT")[0] == 404
    code, body = _call(f"{base}/v1/statements?ticker=ABB&year=2024&scope=HOP_NHAT")
    assert code == 422 and json.loads(body)["template"] == "BANK"


def test_busy_renders_and_unpinned_releases_get_503(served, tmp_path):
    service, _base = served
    query = {"ticker": ["AAA"], "year": ["2024"], "scope": ["HOP_NHAT"]}
    for _ in range(2):
        service._renders.acquire()
    assert service.statements(query)[1]["error"] == "BUSY"
    for _ in range(2):
        service._renders.release()
    plain = ReleaseStore(tmp_path / "plain")
    plain.register(_release(tmp_path, "p"))
    plain.promote("p", "test")
    unpinned = QuestionService(plain, engine_factory=lambda rel: Engine(rel, _Pipeline()))
    unpinned.reload()
    assert unpinned.statements(query)[:2] == (503, {"error": "STATEMENTS_NOT_CONFIGURED",
                                                    "message": "Bản phát hành chưa có bộ BCTC."})
    unpinned.close()


def test_release_pins_the_statement_set_and_old_releases_still_load(tmp_path, statements_dir):
    base = _release(tmp_path, "a")
    pinned = Release(base.release_id, base.catalog, base.catalog_id, base.generator, base.source_fingerprint,
                     statements=str(statements_dir), statements_id="0" * 64)
    assert {"check": "STATEMENTS_ID", "passed": False,
            "actual": StatementStore(statements_dir).statements_id} in preflight(pinned)
    old = {key: value for key, value in base.to_dict().items() if key not in ("statements", "statements_id")}
    assert Release.from_dict(old).statements == ""


def test_store_rejects_a_changed_report_file(statements_dir):
    store = StatementStore(statements_dir)
    entry = store.reports["AAA|2024|HOP_NHAT"]
    (statements_dir / entry["file"]).write_bytes(b"tampered")
    with pytest.raises(StatementStoreError):
        store.get("AAA", 2024, "HOP_NHAT")
