from __future__ import annotations

from collections import Counter
from typing import Any

from sqlmodel import Session, select

from app.models import Case, Dispatch, Finding, Notice, NoticeTrackerEvent
from app.services.time import now_ms


TRACKER_STATUS_LABELS = {
    "drafted": "Drafted",
    "dispatched": "Dispatched",
    "acknowledged": "Acknowledged",
    "responded": "Responded",
    "overdue": "Overdue",
    "escalated": "Escalated",
}
MANUAL_TRACKER_STATUSES = {
    "drafted",
    "dispatched",
    "acknowledged",
    "responded",
    "escalated",
}
RESPONSE_SUB_OUTCOMES = {
    "ack_only": "Acknowledgement only",
    "kyc_records_received": "KYC or account records received",
    "no_matching_account": "No matching account reported",
    "insufficient_response": "Response incomplete",
}
DEADLINE_HOURS = 24
DEADLINE_MS = DEADLINE_HOURS * 60 * 60 * 1000


def record_tracker_event(
    session: Session,
    notice: Notice,
    *,
    actor_pis: str,
    to_status: str,
    note: str | None = None,
    sub_outcome: str | None = None,
    ts_ms: int | None = None,
) -> NoticeTrackerEvent:
    normalized = normalize_manual_status(to_status)
    timestamp = ts_ms if ts_ms is not None else now_ms()
    from_status = notice.tracker_status or _status_from_notice(notice)
    notice.tracker_status = normalized
    notice.tracker_sub_outcome = sub_outcome or None
    notice.tracker_last_note = (note or "").strip() or None
    notice.tracker_updated_ts_ms = timestamp
    if normalized == "dispatched" and notice.dispatched_ts_ms is None:
        notice.dispatched_ts_ms = timestamp
    event = NoticeTrackerEvent(
        notice_id=int(notice.id or 0),
        actor_pis=actor_pis,
        from_status=from_status,
        to_status=normalized,
        sub_outcome=sub_outcome or None,
        note=(note or "").strip() or None,
        created_ts_ms=timestamp,
    )
    session.add(notice)
    session.add(event)
    return event


def normalize_manual_status(status: str) -> str:
    normalized = str(status or "").strip().lower().replace("-", "_")
    if normalized not in MANUAL_TRACKER_STATUSES:
        raise ValueError("Unknown tracker status.")
    return normalized


def tracker_rows(
    session: Session,
    *,
    status_filter: str | None = None,
    vasp_filter: str | None = None,
    case_filter: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    now = now_ts_ms if now_ts_ms is not None else now_ms()
    notices = session.exec(select(Notice).order_by(Notice.created_ts_ms.desc(), Notice.id.desc())).all()
    dispatches_by_notice = _dispatches_by_notice(session)
    events_by_notice = _events_by_notice(session)
    rows: list[dict[str, Any]] = []
    for notice in notices:
        case = session.get(Case, notice.case_id)
        finding = session.get(Finding, notice.finding_id)
        row = tracker_row(
            notice,
            case=case,
            finding=finding,
            dispatches=dispatches_by_notice.get(int(notice.id or 0), []),
            events=events_by_notice.get(int(notice.id or 0), []),
            now_ts_ms=now,
        )
        if status_filter and row["effective_status"] != status_filter:
            continue
        if vasp_filter and vasp_filter.lower() not in row["vasp_label"].lower():
            continue
        if case_filter and case_filter.lower() not in row["case_ref"].lower():
            continue
        rows.append(row)
    return sorted(rows, key=_tracker_sort_key)


def tracker_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(row["effective_status"] for row in rows)
    pending = sum(counter.get(status, 0) for status in ("dispatched", "acknowledged"))
    return {
        "total": len(rows),
        "pending": pending,
        "overdue": counter.get("overdue", 0),
        "responded": counter.get("responded", 0),
        "escalated": counter.get("escalated", 0),
    }


def tracker_row(
    notice: Notice,
    *,
    case: Case | None,
    finding: Finding | None,
    dispatches: list[Dispatch],
    events: list[NoticeTrackerEvent],
    now_ts_ms: int,
) -> dict[str, Any]:
    first_dispatch = notice.dispatched_ts_ms
    dispatch_ts_values = [row.created_ts_ms for row in dispatches if row.created_ts_ms is not None]
    if first_dispatch is None and dispatch_ts_values:
        first_dispatch = min(dispatch_ts_values)
    deadline_ts_ms = first_dispatch + DEADLINE_MS if first_dispatch is not None else None
    base_status = notice.tracker_status or _status_from_notice(notice)
    effective_status = _effective_status(base_status, deadline_ts_ms=deadline_ts_ms, now_ts_ms=now_ts_ms)
    return {
        "notice": notice,
        "case": case,
        "finding": finding,
        "notice_id": notice.id,
        "notice_no": notice.notice_no,
        "case_ref": case.ack_no if case else "Unknown case",
        "vasp_label": _vasp_label(finding),
        "deposit_address": finding.deposit_address if finding else "",
        "dispatch_ts_ms": first_dispatch,
        "deadline_ts_ms": deadline_ts_ms,
        "deadline_hours": DEADLINE_HOURS,
        "time_label": _time_label(effective_status, deadline_ts_ms=deadline_ts_ms, now_ts_ms=now_ts_ms),
        "base_status": base_status,
        "effective_status": effective_status,
        "status_label": TRACKER_STATUS_LABELS[effective_status],
        "sub_outcome": notice.tracker_sub_outcome,
        "sub_outcome_label": RESPONSE_SUB_OUTCOMES.get(notice.tracker_sub_outcome or "", ""),
        "last_note": notice.tracker_last_note or "",
        "last_updated_ts_ms": notice.tracker_updated_ts_ms or notice.dispatched_ts_ms or notice.created_ts_ms,
        "dispatches": dispatches,
        "events": events,
        "manual_notice": "Status updated manually by officer" if events else "No manual tracker update recorded",
    }


def _effective_status(base_status: str, *, deadline_ts_ms: int | None, now_ts_ms: int) -> str:
    if base_status in {"responded", "escalated"}:
        return base_status
    if deadline_ts_ms is not None and now_ts_ms > deadline_ts_ms:
        return "overdue"
    return base_status if base_status in TRACKER_STATUS_LABELS else "drafted"


def _time_label(status: str, *, deadline_ts_ms: int | None, now_ts_ms: int) -> str:
    if deadline_ts_ms is None:
        return "Not dispatched"
    if status == "responded":
        return "Response recorded"
    if status == "escalated":
        return "Escalated manually"
    delta = deadline_ts_ms - now_ts_ms
    absolute_minutes = abs(delta) // 60_000
    hours = absolute_minutes // 60
    minutes = absolute_minutes % 60
    if delta >= 0:
        return f"{hours}h {minutes:02d}m remaining"
    return f"{hours}h {minutes:02d}m overdue"


def _status_from_notice(notice: Notice) -> str:
    if notice.status == "dispatched":
        return "dispatched"
    return "drafted"


def _vasp_label(finding: Finding | None) -> str:
    if not finding:
        return "Unknown VASP"
    if finding.custodian_key:
        return str(finding.custodian_key).replace("_", " ").title()
    return "Custodian from finding"


def _dispatches_by_notice(session: Session) -> dict[int, list[Dispatch]]:
    rows = session.exec(select(Dispatch).order_by(Dispatch.created_ts_ms, Dispatch.id)).all()
    grouped: dict[int, list[Dispatch]] = {}
    for row in rows:
        grouped.setdefault(row.notice_id, []).append(row)
    return grouped


def _events_by_notice(session: Session) -> dict[int, list[NoticeTrackerEvent]]:
    rows = session.exec(
        select(NoticeTrackerEvent).order_by(NoticeTrackerEvent.created_ts_ms.desc(), NoticeTrackerEvent.id.desc())
    ).all()
    grouped: dict[int, list[NoticeTrackerEvent]] = {}
    for row in rows:
        grouped.setdefault(row.notice_id, []).append(row)
    return grouped


def _tracker_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    priority = {
        "overdue": 0,
        "dispatched": 1,
        "acknowledged": 2,
        "escalated": 3,
        "responded": 4,
        "drafted": 5,
    }.get(row["effective_status"], 9)
    deadline = row["deadline_ts_ms"] or 9_999_999_999_999
    return (priority, deadline, str(row["notice_no"]))
