from __future__ import annotations

from sqlmodel import Session, select

from app.models import CaseStage, Notice, TraceSnapshot
from app.services.officers import visible_cases


ACTIVE_TRACE_STATUSES = {"queued", "running", "under_process", "paused", "provider_backoff"}
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
    snapshots = session.exec(select(TraceSnapshot)).all()
    active_traces = sum(
        row.case_id in case_ids and str(row.status).lower() in ACTIVE_TRACE_STATUSES
        for row in snapshots
    )
    notices = session.exec(select(Notice)).all()
    freeze_notices = sum(
        row.case_id in case_ids
        and row.status in OPEN_NOTICE_STATUSES
        and row.tracker_status not in {"responded"}
        for row in notices
    )
    return {
        "case_docket": case_docket,
        "active_traces": active_traces,
        "freeze_notices": freeze_notices,
    }
