"""Versioned releases with canary checks and rollback (ADR-072).

A release pins everything that decides an answer: the catalog (path and
expected ``catalog_id``), the code generator (a configured LLM provider, or a
replay of recorded programs for drills), the provider-config SHA-256 and the
source fingerprint it was validated with. The store keeps one JSON file per
release, a ``current`` pointer replaced atomically, and an append-only
``history.jsonl``.

``deploy`` registers a candidate, runs preflight checks and the canary smoke
set, and promotes it only when everything passes; otherwise the serving release
is untouched (the failed deploy is recorded as ``REJECTED``). ``watch_and_rollback``
rolls back automatically when a page alert fires during the post-promotion bake
window; ``rollback`` is the manual path. The service picks up a pointer change
through ``POST /admin/reload``.

    python -m src.service.release deploy --root deploy/releases --release r2026-10-14 \
        --catalog artifacts/t2p-catalog-v3 --generator provider:deepseek
    python -m src.service.release list --root deploy/releases
    python -m src.service.release rollback --root deploy/releases --reason "..."
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

RELEASE_SCHEMA_VERSION = "aifin-release-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Release:
    release_id: str
    catalog: str
    catalog_id: str
    generator: str  # "provider:<name>" or "replay:<history directory>"
    source_fingerprint: str
    llm_config: str = ""
    llm_config_sha256: str = ""
    created_at: str = field(default_factory=_now)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.release_id or "/" in self.release_id or self.release_id.startswith("."):
            raise ValueError("release_id must be a plain name")
        if not self.generator.startswith(("provider:", "replay:")):
            raise ValueError("generator must be provider:<name> or replay:<directory>")

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": RELEASE_SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Release":
        data = dict(value)
        if data.pop("schema_version", None) != RELEASE_SCHEMA_VERSION:
            raise ValueError("unsupported release schema")
        return cls(**data)


class ReleaseStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        (self.root / "releases").mkdir(parents=True, exist_ok=True)

    def _path(self, release_id: str) -> Path:
        return self.root / "releases" / f"{release_id}.json"

    def register(self, release: Release) -> None:
        path = self._path(release.release_id)
        if path.exists():
            if Release.from_dict(json.loads(path.read_text(encoding="utf-8"))) != release:
                raise ValueError(f"release {release.release_id} already exists with different content")
            return
        path.write_text(json.dumps(release.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._event("REGISTERED", release.release_id)

    def get(self, release_id: str) -> Release:
        return Release.from_dict(json.loads(self._path(release_id).read_text(encoding="utf-8")))

    def current_id(self) -> Optional[str]:
        pointer = self.root / "current"
        return pointer.read_text(encoding="utf-8").strip() or None if pointer.is_file() else None

    def current(self) -> Optional[Release]:
        release_id = self.current_id()
        return None if release_id is None else self.get(release_id)

    def history(self) -> List[Dict[str, Any]]:
        path = self.root / "history.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _event(self, event: str, release_id: Optional[str], **detail: Any) -> None:
        with (self.root / "history.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": _now(), "event": event, "release_id": release_id, **detail},
                                    ensure_ascii=False) + "\n")

    def _point(self, release_id: str) -> None:
        temporary = self.root / f".current.{os.getpid()}.tmp"
        temporary.write_text(release_id + "\n", encoding="utf-8")
        temporary.replace(self.root / "current")  # atomic on POSIX and Windows

    def promote(self, release_id: str, reason: str) -> None:
        self.get(release_id)
        previous = self.current_id()
        self._point(release_id)
        self._event("PROMOTED", release_id, previous=previous, reason=reason)

    def reject(self, release_id: str, reason: str, detail: Any = None) -> None:
        self._event("REJECTED", release_id, reason=reason, detail=detail)

    def previous_id(self) -> Optional[str]:
        """The release served before the current one, following promotions and rollbacks."""
        served = [row["release_id"] for row in self.history() if row["event"] in ("PROMOTED", "ROLLED_BACK")]
        current = self.current_id()
        for release_id in reversed(served[:-1]):
            if release_id != current:
                return release_id
        return None

    def rollback(self, reason: str) -> str:
        target = self.previous_id()
        if target is None:
            raise RuntimeError("no earlier release to roll back to")
        rolled_from = self.current_id()
        self._point(target)
        self._event("ROLLED_BACK", target, rolled_from=rolled_from, reason=reason)
        return target


Checker = Callable[[Release], List[Dict[str, Any]]]


def preflight(release: Release, *, source_fingerprint: Optional[str] = None) -> List[Dict[str, Any]]:
    """Static checks: the pinned catalog, config and code are exactly what was validated."""
    checks = []
    manifest = Path(release.catalog) / "manifest.json"
    actual = json.loads(manifest.read_text(encoding="utf-8")).get("catalog_id") if manifest.is_file() else None
    checks.append({"check": "CATALOG_ID", "passed": actual == release.catalog_id, "actual": actual})
    if release.llm_config:
        config = Path(release.llm_config)
        digest = file_sha256(config) if config.is_file() else None
        checks.append({"check": "LLM_CONFIG_SHA256", "passed": digest == release.llm_config_sha256})
    if source_fingerprint is not None:
        checks.append({"check": "SOURCE_FINGERPRINT", "passed": source_fingerprint == release.source_fingerprint})
    if release.generator.startswith("replay:"):
        checks.append({"check": "REPLAY_HISTORY", "passed": Path(release.generator[7:]).is_dir()})
    return checks


def smoke_matches(response: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    if response.get("status") != expected["status"]:
        return False
    if "value" not in expected:
        return True
    result = response.get("result") or {}
    try:
        got, want = Decimal(str(result.get("value"))), Decimal(str(expected["value"]))
    except InvalidOperation:
        return False
    return abs(got - want) <= Decimal("1e-6") * max(abs(want), Decimal(1))


def deploy(store: ReleaseStore, release: Release, checker: Checker, *,
           source_fingerprint: Optional[str] = None) -> Dict[str, Any]:
    """Promote ``release`` only if preflight and every canary smoke case pass."""
    store.register(release)
    checks = preflight(release, source_fingerprint=source_fingerprint)
    if not all(check["passed"] for check in checks):
        store.reject(release.release_id, "PREFLIGHT_FAILED", checks)
        return {"promoted": False, "reason": "PREFLIGHT_FAILED", "checks": checks, "serving": store.current_id()}
    smoke = checker(release)
    if not smoke or not all(item["passed"] for item in smoke):
        failed = [item for item in smoke if not item["passed"]]
        store.reject(release.release_id, "CANARY_FAILED", failed)
        return {"promoted": False, "reason": "CANARY_FAILED", "checks": checks, "smoke": smoke,
                "serving": store.current_id()}
    store.promote(release.release_id, "canary passed")
    return {"promoted": True, "checks": checks, "smoke": smoke, "serving": store.current_id()}


def watch_and_rollback(store: ReleaseStore, firing_alerts: Sequence[str], *, page_alerts: Sequence[str]) -> Optional[str]:
    """Roll back when any page alert fires during the bake window; returns the restored release."""
    paged = [name for name in firing_alerts if name in page_alerts]
    if not paged:
        return None
    return store.rollback(f"page alert during bake: {', '.join(paged)}")


DEFAULT_SMOKE = Path(__file__).resolve().parents[2] / "deploy" / "service" / "smoke.jsonl"


def smoke_checker(smoke_file: str | Path, history_root: str | Path) -> Checker:
    """Run the canary questions through the candidate release's own pipeline."""
    smoke = [json.loads(line) for line in Path(smoke_file).read_text(encoding="utf-8").splitlines() if line.strip()]

    def checker(release: Release) -> List[Dict[str, Any]]:
        from src.service.app import build_engine

        engine = build_engine(release, history_root)
        results = []
        for item in smoke:
            response = engine.pipeline.ask(item["question"])
            results.append({"question": item["question"][:80], "expected": item["expected"],
                            "status": response["status"], "passed": smoke_matches(response, item["expected"])})
        return results
    return checker


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "rollback", "promote", "deploy"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--release")
    parser.add_argument("--reason", default="manual")
    parser.add_argument("--catalog", help="deploy: catalog directory")
    parser.add_argument("--generator", default="provider:deepseek", help="deploy: provider:<name> or replay:<dir>")
    parser.add_argument("--llm-config", default="", help="deploy: providers TOML (default: the repository file)")
    parser.add_argument("--smoke", default=str(DEFAULT_SMOKE))
    parser.add_argument("--history-root", default="scratch_logs/canary-history")
    args = parser.parse_args(argv)
    store = ReleaseStore(args.root)
    if args.command == "deploy":
        from src.serving.providers import DEFAULT_PROVIDERS_FILE
        from src.text_to_pandas.run import source_fingerprint

        catalog_id = json.loads((Path(args.catalog) / "manifest.json").read_text(encoding="utf-8"))["catalog_id"]
        config = args.llm_config or (str(DEFAULT_PROVIDERS_FILE) if args.generator.startswith("provider:") else "")
        release = Release(args.release, str(Path(args.catalog).resolve()), catalog_id, args.generator,
                          source_fingerprint(), llm_config=config,
                          llm_config_sha256=file_sha256(config) if config else "", notes=args.reason)
        result = deploy(store, release, smoke_checker(args.smoke, args.history_root),
                        source_fingerprint=source_fingerprint())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["promoted"] else 1
    if args.command == "rollback":
        print(json.dumps({"serving": store.rollback(args.reason)}))
    elif args.command == "promote":
        store.promote(args.release, args.reason)
        print(json.dumps({"serving": store.current_id()}))
    else:
        print(json.dumps({"serving": store.current_id(), "history": store.history()[-10:]}, ensure_ascii=False,
                         indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
