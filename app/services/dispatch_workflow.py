from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.models import (
    CaseAssignment,
    CaseEscalationAssignment,
    CaseWatcher,
    DispatchRecord,
    Finding,
    Notice,
    NoticeDraft,
    NoticeVersion,
    OfficerProfile,
    VaspSlaOverride,
)
from app.services.audit import flush_audit_outbox, queue_audit_event
from app.services.demo import demo_case
from app.services.time import now_ms


DEFAULT_SLA_MINUTES = 1_440
TERMINAL_SLA_STAGES = {"acknowledged", "rejected", "closed", "responded"}


class DispatchWorkflowError(ValueError):
    pass


def ensure_dispatch_record(
    session: Session,
    notice: Notice,
    *,
    version: NoticeVersion | None,
    dispatched_ts_ms: int | None = None,
) -> DispatchRecord:
    row = session.exec(
        select(DispatchRecord).where(DispatchRecord.notice_id == notice.id)
    ).first()
    finding = session.get(Finding, notice.finding_id)
    vasp_label = (
        str(finding.custodian_key).replace("_", " ").title()
        if finding and finding.custodian_key
        else "Custodian from reviewed finding"
    )
    assignment = session.exec(
        select(CaseAssignment).where(CaseAssignment.case_id == notice.case_id)
    ).first()
    fixture = demo_case()["officers"]
    assigned_io = assignment.assigned_io_pis if assignment else notice.created_by_pis
    supervising_acp = (
        assignment.supervising_acp_pis if assignment else str(fixture["supervisor"]["pis"])
    )
    sla_minutes, source = _sla_window(session, finding)
    timestamp = now_ms()
    if row is None:
        row = DispatchRecord(
            case_id=notice.case_id,
            notice_id=int(notice.id or 0),
            notice_version_id=version.id if version else None,
            notice_type="freeze",
            vasp_label=vasp_label,
            stage="dispatched" if dispatched_ts_ms is not None else "drafted",
            acknowledgement_state="awaiting",
            dispatched_ts_ms=dispatched_ts_ms,
            sla_window_minutes=sla_minutes,
            sla_due_ts_ms=(dispatched_ts_ms + sla_minutes * 60_000) if dispatched_ts_ms else None,
            sla_override_source=source,
            assigned_io_pis=assigned_io,
            supervising_acp_pis=supervising_acp,
            current_owner_pis=assigned_io,
            created_ts_ms=timestamp,
            updated_ts_ms=timestamp,
        )
    else:
        if version is not None:
            row.notice_version_id = version.id
        if dispatched_ts_ms is not None and row.dispatched_ts_ms is None:
            row.dispatched_ts_ms = dispatched_ts_ms
            row.stage = "dispatched"
            row.sla_window_minutes = sla_minutes
            row.sla_due_ts_ms = dispatched_ts_ms + sla_minutes * 60_000
            row.sla_override_source = source
        row.updated_ts_ms = timestamp
    session.add(row)
    session.flush()
    return row


def set_dispatch_stage(
    session: Session,
    record: DispatchRecord,
    *,
    stage: str,
    actor_pis: str,
    acknowledgement_state: str | None = None,
    timestamp_ms: int | None = None,
) -> DispatchRecord:
    timestamp = now_ms() if timestamp_ms is None else timestamp_ms
    if stage not in {
        "drafted", "awaiting_countersignature", "countersigned", "dispatched",
        "acknowledged", "responded", "rejected", "closed", "escalated",
    }:
        raise DispatchWorkflowError("Unknown dispatch stage.")
    if acknowledgement_state is not None:
        record.acknowledgement_state = acknowledgement_state
    if stage == "dispatched" and record.dispatched_ts_ms is None:
        record.dispatched_ts_ms = timestamp
        record.sla_due_ts_ms = timestamp + record.sla_window_minutes * 60_000
    if stage in TERMINAL_SLA_STAGES and record.sla_frozen_remaining_ms is None:
        record.sla_frozen_remaining_ms = (
            max(0, record.sla_due_ts_ms - timestamp)
            if record.sla_due_ts_ms is not None
            else 0
        )
        if stage in {"acknowledged", "responded"}:
            record.acknowledged_ts_ms = timestamp
        if stage == "closed":
            record.closed_ts_ms = timestamp
    record.stage = stage
    record.updated_ts_ms = timestamp
    session.add(record)
    queue_audit_event(
        session,
        actor=actor_pis,
        action="dispatch.stage_changed",
        subject=str(record.notice_id),
        entity_type="dispatch_record",
        entity_id=record.id,
        case_id=record.case_id,
        summary=f"Dispatch stage changed to {stage}",
        data={"stage": stage, "acknowledgement_state": record.acknowledgement_state},
        ts_ms=timestamp,
    )
    session.flush()
    return record


def register_sla_breach(
    session: Session,
    record: DispatchRecord,
    *,
    timestamp_ms: int | None = None,
) -> bool:
    timestamp = now_ms() if timestamp_ms is None else timestamp_ms
    if (
        record.dispatched_ts_ms is None
        or record.sla_due_ts_ms is None
        or record.sla_frozen_remaining_ms is not None
        or timestamp <= record.sla_due_ts_ms
        or record.sla_breach_audit_event_id is not None
    ):
        return False
    event = queue_audit_event(
        session,
        actor="system",
        action="SLA_BREACHED",
        subject=str(record.notice_id),
        entity_type="dispatch_record",
        entity_id=record.id,
        case_id=record.case_id,
        summary="Dispatch acknowledgement SLA breached",
        data={"due_ts_ms": record.sla_due_ts_ms, "window_minutes": record.sla_window_minutes},
        ts_ms=timestamp,
    )
    session.flush()
    record.sla_breach_audit_event_id = event.event_id
    record.updated_ts_ms = timestamp
    session.add(record)
    session.flush()
    return True


def escalate_dispatch(
    session: Session,
    record: DispatchRecord,
    *,
    next_owner_pis: str,
    reason: str,
    assigning_acp_pis: str,
) -> CaseEscalationAssignment:
    reason = reason.strip()
    if not reason:
        raise DispatchWorkflowError("An escalation reason is required.")
    if assigning_acp_pis != record.supervising_acp_pis:
        raise DispatchWorkflowError("Only the supervising ACP may select the next escalation owner.")
    officer = session.exec(
        select(OfficerProfile).where(
            OfficerProfile.pis == next_owner_pis,
            OfficerProfile.active == True,  # noqa: E712
        )
    ).first()
    if officer is None:
        raise DispatchWorkflowError("Select an active configured officer.")
    timestamp = now_ms()
    previous = record.current_owner_pis
    level = record.escalation_level + 1
    assignment = CaseEscalationAssignment(
        case_id=record.case_id,
        dispatch_record_id=int(record.id or 0),
        level=level,
        previous_owner_pis=previous,
        new_owner_pis=next_owner_pis,
        reason=reason,
        assigned_by_pis=assigning_acp_pis,
        created_ts_ms=timestamp,
    )
    session.add(assignment)
    watcher = session.exec(
        select(CaseWatcher).where(
            CaseWatcher.case_id == record.case_id,
            CaseWatcher.officer_pis == record.supervising_acp_pis,
        )
    ).first()
    if watcher is None:
        session.add(
            CaseWatcher(
                case_id=record.case_id,
                officer_pis=record.supervising_acp_pis,
                reason="Original supervising ACP retained after escalation",
                created_ts_ms=timestamp,
            )
        )
    record.current_owner_pis = next_owner_pis
    record.escalation_level = level
    record.stage = "escalated"
    record.updated_ts_ms = timestamp
    session.add(record)
    queue_audit_event(
        session,
        actor=assigning_acp_pis,
        action="dispatch.escalated",
        subject=str(record.notice_id),
        entity_type="dispatch_record",
        entity_id=record.id,
        case_id=record.case_id,
        summary=f"Dispatch escalated to level {level}",
        data={
            "level": level,
            "previous_owner_pis": previous,
            "new_owner_pis": next_owner_pis,
            "reason": reason,
        },
        ts_ms=timestamp,
    )
    session.flush()
    return assignment


def commit_and_flush_audit(session: Session) -> None:
    """Commit the mutation/outbox atomically, then idempotently append JSONL."""
    session.commit()
    flush_audit_outbox(session)


def dispatch_sla_view(record: DispatchRecord, *, timestamp_ms: int | None = None) -> dict[str, Any]:
    timestamp = now_ms() if timestamp_ms is None else timestamp_ms
    total = record.sla_window_minutes * 60_000
    if record.sla_frozen_remaining_ms is not None:
        remaining = record.sla_frozen_remaining_ms
    elif record.sla_due_ts_ms is not None:
        remaining = record.sla_due_ts_ms - timestamp
    else:
        remaining = total
    ratio = remaining / total if total > 0 else 0
    tone = "red" if remaining < 0 else "amber" if ratio <= 0.5 else "green"
    return {
        "remaining_ms": remaining,
        "ratio": max(0.0, min(1.0, ratio)),
        "tone": tone,
        "breached": remaining < 0,
        "frozen": record.sla_frozen_remaining_ms is not None,
    }


def version_for_draft(session: Session, notice: Notice) -> NoticeVersion | None:
    draft = session.exec(select(NoticeDraft).where(NoticeDraft.notice_id == notice.id)).first()
    if draft is None or draft.active_version_no is None:
        return None
    return session.exec(
        select(NoticeVersion).where(
            NoticeVersion.draft_id == draft.id,
            NoticeVersion.version_no == draft.active_version_no,
        )
    ).first()


def _sla_window(session: Session, finding: Finding | None) -> tuple[int, str]:
    key = str(finding.custodian_key or "") if finding else ""
    override = session.exec(
        select(VaspSlaOverride).where(
            VaspSlaOverride.vasp_key == key,
            VaspSlaOverride.active == True,  # noqa: E712
        )
    ).first()
    if override is not None:
        return override.acknowledgement_minutes, override.source
    return DEFAULT_SLA_MINUTES, "default_24h"
