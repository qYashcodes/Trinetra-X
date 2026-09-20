from __future__ import annotations

import hashlib
import secrets

from sqlmodel import Session, select

from app.models import OfficerRole, OfficerSession
from app.services.officers import upsert_officer_profile
from app.services.time import now_ms
from app.settings import settings


def session_token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def device_label(user_agent: str | None) -> str:
    normalized = (user_agent or "").lower()
    if "edg/" in normalized:
        browser = "Edge"
    elif "chrome/" in normalized:
        browser = "Chrome"
    elif "firefox/" in normalized:
        browser = "Firefox"
    elif "safari/" in normalized:
        browser = "Safari"
    else:
        browser = "Browser"

    if "windows" in normalized:
        platform = "Windows"
    elif "mac os" in normalized:
        platform = "macOS"
    elif "android" in normalized:
        platform = "Android"
    elif "iphone" in normalized or "ipad" in normalized:
        platform = "iOS"
    elif "linux" in normalized:
        platform = "Linux"
    else:
        platform = "device"
    return f"{browser} on {platform}"


def create_officer_session(
    session: Session,
    officer: dict,
    role: OfficerRole,
    user_agent: str | None,
    *,
    created_ts_ms: int | None = None,
) -> tuple[OfficerSession, str]:
    created = now_ms() if created_ts_ms is None else created_ts_ms
    upsert_officer_profile(session, officer, role)
    raw_token = secrets.token_urlsafe(48)
    row = OfficerSession(
        token_sha256=session_token_sha256(raw_token),
        officer_pis=str(officer["pis"]),
        officer_name=str(officer["name"]),
        officer_rank=str(officer["rank"]),
        officer_unit=str(officer["desk"]),
        role=role,
        authentication_kind="prototype_demonstration",
        device_label=device_label(user_agent),
        user_agent_sha256=hashlib.sha256((user_agent or "").encode("utf-8")).hexdigest(),
        created_ts_ms=created,
        last_active_ts_ms=created,
        expires_ts_ms=created + settings.session_idle_timeout_seconds * 1000,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, raw_token


def resolve_officer_session(
    session: Session,
    raw_token: str | None,
    *,
    current_ts_ms: int | None = None,
    touch: bool = True,
) -> OfficerSession | None:
    if not raw_token:
        return None
    current = now_ms() if current_ts_ms is None else current_ts_ms
    row = session.exec(
        select(OfficerSession).where(
            OfficerSession.token_sha256 == session_token_sha256(raw_token)
        )
    ).first()
    if row is None or row.revoked or row.ended_ts_ms is not None:
        return None
    if current >= row.expires_ts_ms:
        row.revoked = True
        row.ended_ts_ms = current
        row.end_reason = "idle_timeout"
        session.add(row)
        session.commit()
        return None
    if touch:
        row.last_active_ts_ms = current
        row.expires_ts_ms = current + settings.session_idle_timeout_seconds * 1000
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def officer_identity(row: OfficerSession) -> dict[str, str]:
    role = row.role.value if hasattr(row.role, "value") else str(row.role)
    return {
        "name": row.officer_name,
        "rank": row.officer_rank,
        "pis": row.officer_pis,
        "desk": row.officer_unit,
        "role": role,
        "authentication_kind": row.authentication_kind,
    }


def record_session_activity(
    session: Session,
    row: OfficerSession,
    *,
    case_id: int | None = None,
    trace_run: bool = False,
    artifact_export: bool = False,
) -> OfficerSession:
    if case_id is not None and case_id not in row.cases_touched:
        row.cases_touched = [*row.cases_touched, case_id]
    if trace_run:
        row.traces_run += 1
    if artifact_export:
        row.artifacts_exported += 1
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def end_officer_session(
    session: Session,
    raw_token: str | None,
    *,
    reason: str,
    ended_ts_ms: int | None = None,
) -> OfficerSession | None:
    if not raw_token:
        return None
    row = session.exec(
        select(OfficerSession).where(
            OfficerSession.token_sha256 == session_token_sha256(raw_token)
        )
    ).first()
    if row is None:
        return None
    if row.ended_ts_ms is None:
        ended = now_ms() if ended_ts_ms is None else ended_ts_ms
        row.revoked = True
        row.ended_ts_ms = ended
        row.end_reason = reason
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def active_officer_sessions(
    session: Session,
    officer_pis: str,
    *,
    current_ts_ms: int | None = None,
) -> list[OfficerSession]:
    current = now_ms() if current_ts_ms is None else current_ts_ms
    return list(
        session.exec(
            select(OfficerSession)
            .where(
                OfficerSession.officer_pis == officer_pis,
                OfficerSession.revoked == False,  # noqa: E712
                OfficerSession.ended_ts_ms == None,  # noqa: E711
                OfficerSession.expires_ts_ms > current,
            )
            .order_by(OfficerSession.last_active_ts_ms.desc())
        ).all()
    )


def revoke_all_officer_sessions(
    session: Session,
    officer_pis: str,
    *,
    reason: str,
    ended_ts_ms: int | None = None,
) -> list[OfficerSession]:
    ended = now_ms() if ended_ts_ms is None else ended_ts_ms
    rows = active_officer_sessions(session, officer_pis, current_ts_ms=ended)
    for row in rows:
        row.revoked = True
        row.ended_ts_ms = ended
        row.end_reason = reason
        session.add(row)
    session.commit()
    return rows


def signout_summary(row: OfficerSession, *, ended_ts_ms: int | None = None) -> dict:
    ended = row.ended_ts_ms if ended_ts_ms is None else ended_ts_ms
    effective_end = ended if ended is not None else now_ms()
    return {
        "session_id": row.id,
        "duration_ms": max(0, effective_end - row.created_ts_ms),
        "cases_touched": list(row.cases_touched),
        "traces_run": row.traces_run,
        "artifacts_exported": row.artifacts_exported,
        "end_reason": row.end_reason,
    }
