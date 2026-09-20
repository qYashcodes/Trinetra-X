from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlmodel import Session, select

from app.models import AuditOutbox
from app.services.hash import sha256_json
from app.services.time import now_ms
from app.settings import settings


_AUDIT_LOCK = Lock()


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


def append_audit_event(
    actor: str,
    action: str,
    subject: str,
    data: dict[str, Any] | None = None,
    *,
    event_id: str | None = None,
    actor_name: str | None = None,
    actor_rank: str | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    case_id: int | None = None,
    summary: str | None = None,
    demo_session: bool | None = None,
    ts_ms: int | None = None,
) -> dict:
    """Append one backward-compatible v2 row to the canonical hash chain."""
    path = _audit_path()
    stable_event_id = event_id or str(uuid4())
    with _AUDIT_LOCK:
        existing = _event_by_id(path, stable_event_id)
        if existing is not None:
            return existing
        meta = dict(data or {})
        row = {
            "schema_version": 2,
            "event_id": stable_event_id,
            "ts_ms": now_ms() if ts_ms is None else int(ts_ms),
            "actor": actor,
            "actor_id": actor,
            "actor_name": actor_name,
            "actor_rank": actor_rank,
            "actor_role": actor_role,
            "action": action,
            "subject": subject,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id is not None else None,
            "case_id": case_id,
            "summary": summary,
            "data": meta,
            "meta": meta,
            "demo_session": demo_session,
            "prev_hash": _last_hash(path),
        }
        row["row_hash"] = sha256_json(row)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return row


def queue_audit_event(
    session: Session,
    *,
    actor: str,
    action: str,
    subject: str,
    data: dict[str, Any] | None = None,
    actor_name: str | None = None,
    actor_rank: str | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    case_id: int | None = None,
    summary: str | None = None,
    demo_session: bool | None = None,
    ts_ms: int | None = None,
) -> AuditOutbox:
    """Queue an audit event inside the caller's current SQLite transaction."""
    timestamp = now_ms() if ts_ms is None else int(ts_ms)
    event_id = str(uuid4())
    payload = {
        "actor": actor,
        "action": action,
        "subject": subject,
        "data": dict(data or {}),
        "event_id": event_id,
        "actor_name": actor_name,
        "actor_rank": actor_rank,
        "actor_role": actor_role,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "case_id": case_id,
        "summary": summary,
        "demo_session": demo_session,
        "ts_ms": timestamp,
    }
    row = AuditOutbox(
        event_id=event_id,
        payload=payload,
        created_ts_ms=timestamp,
    )
    session.add(row)
    return row


def flush_audit_outbox(session: Session) -> list[dict[str, Any]]:
    """Idempotently deliver committed outbox rows to the JSONL hash chain."""
    pending = session.exec(
        select(AuditOutbox)
        .where(AuditOutbox.flushed_ts_ms == None)  # noqa: E711
        .order_by(AuditOutbox.id)
    ).all()
    appended: list[dict[str, Any]] = []
    for item in pending:
        item.attempts += 1
        try:
            payload = dict(item.payload)
            event = append_audit_event(**payload)
        except Exception as exc:
            item.last_error = type(exc).__name__
            session.add(item)
            session.commit()
            raise
        item.flushed_ts_ms = now_ms()
        item.last_error = None
        session.add(item)
        appended.append(event)
    if pending:
        session.commit()
    return appended


def _event_by_id(path: Path, event_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event_id") == event_id:
                return row
    return None


def verify_audit_chain(path: Path | None = None) -> bool:
    source = path or _audit_path()
    prev = "0" * 64
    if not source.exists():
        return True
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            row_hash = row.pop("row_hash", None)
            if not row_hash or row.get("prev_hash") != prev:
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
