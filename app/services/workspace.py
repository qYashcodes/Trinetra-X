from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.models import CaseStage, Finding, Notice, NoticeDraft, TraceSnapshot
from app.services.officers import visible_cases


ACTIVE_TRACE_STATUSES = {"queued", "running", "under_process", "paused", "provider_backoff"}
TRACE_COMPLETE_STATUSES = {"complete", "completed", "closed", "failed", "unsupported"}
OPEN_NOTICE_STATUSES = {
    "draft",
    "awaiting_countersignature",
    "countersigned",
    "dispatched",
}


def navigation_counts(session: Session, *, officer_pis: str, role: str) -> dict[str, int]:
    cases = visible_cases(session, officer_pis=officer_pis, role=role)
    case_ids = {int(case.id) for case in cases if case.id is not None}
    closed_stages = {CaseStage.closed_no_custody}
    case_docket = sum(case.stage not in closed_stages for case in cases)
    if not case_ids:
        active_traces = 0
        freeze_notices = 0
    else:
        active_traces = int(
            session.exec(
                select(func.count(TraceSnapshot.id))
                .where(TraceSnapshot.case_id.in_(case_ids))
                .where(func.lower(TraceSnapshot.status).in_(ACTIVE_TRACE_STATUSES))
            ).one()
        )
        freeze_notices = int(
            session.exec(
                select(func.count(Notice.id))
                .where(Notice.case_id.in_(case_ids))
                .where(Notice.status.in_(OPEN_NOTICE_STATUSES))
                .where(
                    or_(
                        Notice.tracker_status.is_(None),
                        Notice.tracker_status.notin_({"responded"}),
                    )
                )
            ).one()
        )
    return {
        "case_docket": case_docket,
        "active_traces": active_traces,
        "freeze_notices": freeze_notices,
    }


def _coerce_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _trace_is_complete(snapshot: TraceSnapshot | None) -> bool:
    if snapshot is None:
        return False
    status = str(snapshot.status or "").lower()
    if status in ACTIVE_TRACE_STATUSES:
        return False
    return snapshot.closed_ts_ms is not None or status in TRACE_COMPLETE_STATUSES


def navigation_gates(session: Session | None, active_case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Resolve left-rail workflow gates for the officer's current case context."""
    case_id = _coerce_int(active_case.get("id") if active_case.get("id") is not None else active_case.get("case_id"))
    snapshot_id = _coerce_int(active_case.get("snapshot_id"))
    finding_id = _coerce_int(active_case.get("finding_id"))
    notice_id = _coerce_int(active_case.get("notice_id"))
    snapshot: TraceSnapshot | None = None

    if session is not None:
        if snapshot_id is not None:
            snapshot = session.get(TraceSnapshot, snapshot_id)
            if snapshot is not None and case_id is not None and snapshot.case_id != case_id:
                snapshot = None
        if snapshot is None and case_id is not None:
            snapshot = session.exec(
                select(TraceSnapshot)
                .where(TraceSnapshot.case_id == case_id)
                .order_by(TraceSnapshot.id.desc())
            ).first()
            if snapshot is not None:
                snapshot_id = int(snapshot.id or 0) or snapshot_id

        if finding_id is None and snapshot is not None and snapshot.id is not None:
            finding = session.exec(
                select(Finding).where(Finding.snapshot_id == snapshot.id).order_by(Finding.id.desc())
            ).first()
            if finding is not None and (case_id is None or finding.case_id == case_id):
                finding_id = int(finding.id or 0) or None

        generated_notice = None
        draft_query = select(NoticeDraft).where(
            NoticeDraft.generated == True,  # noqa: E712 - SQLModel expression
            NoticeDraft.active_version_no.is_not(None),
        )
        if notice_id is not None:
            generated_notice = session.exec(
                draft_query.where(NoticeDraft.notice_id == notice_id)
            ).first()
        if generated_notice is None and case_id is not None:
            generated_notice = session.exec(
                draft_query
                .where(NoticeDraft.case_id == case_id)
                .order_by(NoticeDraft.updated_ts_ms.desc(), NoticeDraft.id.desc())
            ).first()
        if notice_id is None and generated_notice is not None:
            notice_id = _coerce_int(generated_notice.notice_id)

        trace_complete = _trace_is_complete(snapshot)
        notice_enabled = generated_notice is not None
    else:
        trace_complete = snapshot_id is not None
        notice_enabled = False

    canvas_enabled = case_id is not None and snapshot_id is not None and trace_complete
    finding_enabled = finding_id is not None and trace_complete
    notice_enabled = notice_enabled and case_id is not None
    trace_enabled = case_id is not None and snapshot_id is not None

    return {
        "trace": {
            "enabled": trace_enabled,
            "href": "/traces" if trace_enabled else None,
            "reason": "Available after a case is ingested and trace is started.",
        },
        "canvas": {
            "enabled": canvas_enabled,
            "href": (
                f"/cases/{case_id}/canvas?snapshot={snapshot_id}"
                if canvas_enabled
                else None
            ),
            "reason": "Available after the active trace completes.",
        },
        "finding": {
            "enabled": finding_enabled,
            "href": f"/findings/{finding_id}" if finding_enabled else None,
            "reason": "Available once the trace has produced a custody finding.",
        },
        "notice": {
            "enabled": notice_enabled,
            "href": "/notices" if notice_enabled else None,
            "reason": "Available after a freeze notice draft is generated through the workflow.",
        },
    }
