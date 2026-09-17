from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.hash import sha256_json
from app.services.time import now_ms
from app.settings import settings


def _audit_path() -> Path:
    settings.var_dir.mkdir(parents=True, exist_ok=True)
    return settings.var_dir / "audit.jsonl"


def _last_hash(path: Path) -> str:
    if not path.exists():
        return "0" * 64
    last = ""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = line
    if not last:
        return "0" * 64
    return json.loads(last)["row_hash"]


def append_audit_event(actor: str, action: str, subject: str, data: dict[str, Any] | None = None) -> dict:
    path = _audit_path()
    row = {
        "ts_ms": now_ms(),
        "actor": actor,
        "action": action,
        "subject": subject,
        "data": data or {},
        "prev_hash": _last_hash(path),
    }
    row["row_hash"] = sha256_json(row)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return row


def verify_audit_chain(path: Path | None = None) -> bool:
    source = path or _audit_path()
    prev = "0" * 64
    if not source.exists():
        return True
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            row_hash = row.pop("row_hash")
            if row["prev_hash"] != prev:
                return False
            if sha256_json(row) != row_hash:
                return False
            prev = row_hash
    return True


def read_audit_events(
    *,
    subject: str | None = None,
    actions: set[str] | None = None,
) -> list[dict[str, Any]]:
    path = _audit_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if subject is not None and row.get("subject") != subject:
                continue
            if actions is not None and row.get("action") not in actions:
                continue
            rows.append(row)
    return rows
