"""Read-only access to a built statement-set artifact (ADR-075).

The manifest is checked once (schema and ``statements_id`` over the per-report
file hashes); each report file is checked against its own SHA-256 when it is
read. File paths come only from the manifest, never from a request.
"""

from __future__ import annotations

import gzip
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "statement-set-v1"
SCOPES = ("HOP_NHAT", "RIENG")


class StatementStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def report_key(ticker: str, year: int, scope: str) -> str:
    return f"{ticker}|{int(year)}|{scope}"


def files_id(files: dict[str, str]) -> str:
    """The artifact id: SHA-256 over the sorted ``{file: sha256}`` map."""
    return sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()


class StatementStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise StatementStoreError("STATEMENTS_SCHEMA", str(manifest.get("schema_version")))
        self.reports: dict[str, dict[str, Any]] = manifest["reports"]
        files = {entry["file"]: entry["sha256"] for entry in self.reports.values() if "file" in entry}
        if files_id(files) != manifest["statements_id"]:
            raise StatementStoreError("STATEMENTS_ID_MISMATCH", str(self.root))
        self.statements_id: str = manifest["statements_id"]

    def get(self, ticker: str, year: int, scope: str) -> tuple[str, dict[str, Any] | None]:
        """(template, statement set): ``NOT_FOUND``, an unsupported template with None, or TT200 with the set."""
        entry = self.reports.get(report_key(ticker, year, scope))
        if entry is None:
            return "NOT_FOUND", None
        if "file" not in entry:
            return entry["template"], None
        data = (self.root / entry["file"]).read_bytes()
        if sha256(data).hexdigest() != entry["sha256"]:
            raise StatementStoreError("STATEMENTS_FILE_CHANGED", entry["file"])
        return entry["template"], json.loads(gzip.decompress(data))
