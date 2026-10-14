"""Sandbox security suite with observable canaries (ADR-072 production gate).

Generated Pandas code is the system's largest attack surface, so every probe
below runs twice: through the parent-side validator (the production path) and
directly through the isolated worker with the validator bypassed (defense in
depth). A probe is a CRITICAL escape when an effect is *observed*, not merely
when the program returns successfully:

- a host secret file's content, or a parent-only environment variable, appears
  in the worker's result or evidence;
- a canary file the program tried to create exists afterwards;
- a loopback TCP listener the program tried to reach accepts a connection;
- a canary file a spawned process would create exists afterwards.

A program that runs to completion without any such effect is ``CONTAINED`` but
counted separately (``CONTAINED_SUCCESS``) so silent no-ops stay visible. The
existing focused security tests (M7 abuse, environment, policy, NsJail
production, validator, worker) also run and their pass/fail counts are
recorded. Exit status is non-zero on any escape or failing security test.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Any
from uuid import uuid4

import pandas as pd

from src.sandbox.pandas_executor import execute_pandas_sandboxed
from src.sandbox.production import resolve_isolation_mode
from src.text_to_pandas.catalog import frame_payload
from src.text_to_pandas.catalog_builder import FACT_COLUMNS
from src.text_to_pandas.metrics import METRICS_BY_ID
from src.text_to_pandas.validator import validate_pandas_code

SCHEMA_VERSION = "sandbox-security-suite-v1"
SECURITY_TESTS = (
    "tests/sandbox/test_abuse.py", "tests/sandbox/test_security_environment.py", "tests/sandbox/test_policy.py",
    "tests/sandbox/test_production.py", "tests/sandbox/test_host_platforms.py", "tests/text_to_pandas",
)
SECRET_ENV = "AI_FIN_SECURITY_CANARY"


class Canaries:
    def __init__(self, root: Path) -> None:
        token = uuid4().hex
        self.secret_value = f"secret-{token}"
        self.env_value = f"env-{token}"
        self.secret_file = root / f"canary-{token}-secret.txt"
        self.secret_file.write_text(self.secret_value, encoding="utf-8")
        self.write_target = root / f"canary-{token}-written.txt"
        self.process_target = root / f"canary-{token}-process.txt"
        self.connections = 0
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self._server.settimeout(0.2)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except (TimeoutError, OSError):
                continue
            self.connections += 1
            connection.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(1)
        self._server.close()

    def leaked(self, text: str) -> bool:
        return self.secret_value in text or self.env_value in text


def probes(c: Canaries) -> dict[str, str]:
    """Hostile programs: each tries one capability the worker must not have."""
    secret, written, proc = (str(path).replace("\\", "/") for path in (c.secret_file, c.write_target, c.process_target))
    url = f"http://127.0.0.1:{c.port}/x.csv"
    return {
        "open_secret": f"result = open('{secret}').read()",
        "pandas_read_secret": f"result = pd.read_csv('{secret}', header=None).iloc[0, 0]",
        "pandas_read_file_url": f"result = pd.read_csv('file://{secret}', header=None).iloc[0, 0]",
        "numpy_load_secret": f"result = str(np.loadtxt('{secret}', dtype=str))",
        "numpy_fromfile_secret": f"result = str(np.fromfile('{secret}', dtype='uint8')[:4])",
        "pandas_write_file": f"income_statement.to_csv('{written}')\nresult = 1",
        "numpy_write_file": f"np.savetxt('{written}', [1, 2])\nresult = 1",
        "pandas_read_url": f"result = pd.read_csv('{url}')",
        "pandas_read_html_url": f"result = pd.read_html('{url}')",
        "dunder_subclasses": "result = [c.__name__ for c in ().__class__.__base__.__subclasses__()]",
        "frame_globals": "result = str(income_statement.__class__.__init__.__globals__.keys())",
        "builtins_import": "result = __builtins__['__import__']('os').environ.get('" + SECRET_ENV + "')",
        "import_os": "import os\nresult = os.environ.get('" + SECRET_ENV + "')",
        "pandas_os_module": f"result = pd.io.common.os.environ.get('{SECRET_ENV}')",
        "pandas_os_system": f"result = pd.io.common.os.system('touch {proc}')",
        "numpy_ctypes": "result = str(np.ctypeslib.ctypes.CDLL(None))",
        "pandas_eval": "result = pd.eval('__import__(\"os\").getpid()')",
        "dataframe_query_injection": "result = income_statement.query('@__import__(\"os\").getpid() > 0')",
        "getattr_chain": "result = getattr(getattr(pd, 'io'), 'common').os.getcwd()",
        "format_string_escape": "result = '{0.__class__.__init__.__globals__}'.format(income_statement)",
        "pickle_load": f"result = pd.read_pickle('{secret}')",
        "proc_environ": "result = open('/proc/self/environ').read()",
        "infinite_loop": "while True:\n    pass\nresult = 1",
        "memory_bomb": "big = np.ones(500_000_000)\nresult = float(big.sum())",
        "recursion_bomb": "def f(n):\n    return f(n + 1)\nresult = f(0)",
        "output_flood": "result = pd.DataFrame({'x': ['y' * 1000] * 100000})",
        "thread_spawn": "import threading\nresult = threading.active_count()",
    }


def _effects(c: Canaries, result: Any, connections_before: int) -> list[str]:
    effects = []
    text = json.dumps({"result": getattr(result, "result", None), "evidence": getattr(result, "evidence", None)},
                      ensure_ascii=False, default=str)
    if c.leaked(text):
        effects.append("SECRET_DISCLOSED")
    if c.write_target.exists():
        effects.append("FILE_WRITTEN")
        c.write_target.unlink()
    if c.process_target.exists():
        effects.append("PROCESS_SPAWNED")
        c.process_target.unlink()
    if c.connections > connections_before:
        effects.append("NETWORK_REACHED")
    return effects


def run_probe(name: str, code: str, c: Canaries, datasets: dict[str, Any]) -> dict[str, Any]:
    validation = validate_pandas_code(code, datasets={key: list(value["columns"]) for key, value in datasets.items()},
                                      known_metrics=METRICS_BY_ID)
    before = c.connections
    # Defense in depth: the worker is exercised even when the validator rejects.
    result = execute_pandas_sandboxed(code, datasets, request_id=f"security-{name}")
    effects = _effects(c, result, before)
    failure = None if result.failure is None else {"stage": result.failure.stage.value, "code": result.failure.code}
    outcome = ("CRITICAL_ESCAPE" if effects else "CONTAINED_SUCCESS" if result.success else "CONTAINED")
    return {"probe": name, "validator_accepts": validation.ok,
            "validator_issue": None if validation.ok else validation.issues[0].code,
            "worker_success": result.success, "worker_failure": failure, "effects": effects, "outcome": outcome}


def run_security_tests(paths: Sequence[str]) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths],
                               cwd=root, capture_output=True, text=True, timeout=1800)
    tail = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    return {"paths": list(paths), "returncode": completed.returncode, "summary": tail}


def evaluate(*, run_tests: bool = True) -> dict[str, Any]:
    os.environ[SECRET_ENV] = "unset"
    with tempfile.TemporaryDirectory(prefix="security-canary-") as directory:
        c = Canaries(Path(directory))
        os.environ[SECRET_ENV] = c.env_value  # parent-only; the worker environment is exact
        try:
            frame = pd.DataFrame([["f1", "AAA", "Co", 2023, "HOP_NHAT", "60", "LNST", "LNST", 1.0, "VND", "RAW"]],
                                 columns=FACT_COLUMNS)
            datasets = {"income_statement": frame_payload(frame)}
            results = [run_probe(name, code, c, datasets) for name, code in probes(c).items()]
        finally:
            c.close()
            os.environ.pop(SECRET_ENV, None)
    tests = run_security_tests(SECURITY_TESTS) if run_tests else None
    escapes = [row["probe"] for row in results if row["outcome"] == "CRITICAL_ESCAPE"]
    report = {
        "schema_version": SCHEMA_VERSION, "isolation_mode": resolve_isolation_mode().value,
        "host": {"system": platform.system(), "release": platform.release(), "python": platform.python_version()},
        "escapes": len(escapes), "escaped_probes": escapes,
        "validator_accepted": [row["probe"] for row in results if row["validator_accepts"]],
        "contained_success": [row["probe"] for row in results if row["outcome"] == "CONTAINED_SUCCESS"],
        "probe_count": len(results), "probes": results, "security_tests": tests,
        "measurement_scope": (f"{resolve_isolation_mode().value} boundary on {platform.system()}; NsJail namespaces/"
                              "cgroups are not exercised unless M7_SANDBOX_ISOLATION=linux-nsjail-v1 on a capable host"),
    }
    report["passed"] = not escapes and (tests is None or tests["returncode"] == 0)
    report["summary"] = {"probes": len(results), "escapes": len(escapes),
                         "security_tests": None if tests is None else tests["summary"]}
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)
    report = evaluate(run_tests=not args.skip_tests)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"]))
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
