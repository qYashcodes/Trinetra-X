from __future__ import annotations

from sqlmodel import Session, select

from app.models import (
    Case,
    CaseAssignment,
    CaseEscalationAssignment,
    CaseWatcher,
    OfficerProfile,
    OfficerRole,
)
from app.services.time import now_ms


def upsert_officer_profile(
    session: Session,
    officer: dict,
    role: OfficerRole,
) -> OfficerProfile:
    pis = str(officer["pis"])
    row = session.exec(select(OfficerProfile).where(OfficerProfile.pis == pis)).first()
    timestamp = now_ms()
    if row is None:
        row = OfficerProfile(
            pis=pis,
            name=str(officer["name"]),
            rank=str(officer["rank"]),
            unit=str(officer["desk"]),
            role=role,
            created_ts_ms=timestamp,
            updated_ts_ms=timestamp,
        )
    else:
        row.name = str(officer["name"])
        row.rank = str(officer["rank"])
        row.unit = str(officer["desk"])
        row.role = role
        row.active = True
        row.updated_ts_ms = timestamp
    session.add(row)
    session.flush()
    return row


def ensure_case_assignment(
    session: Session,
    case: Case,
    *,
    assigned_io_pis: str,
    supervising_acp_pis: str,
) -> CaseAssignment:
    if case.id is None:
        raise ValueError("Case must be persisted before it can be assigned.")
    row = session.exec(
        select(CaseAssignment).where(CaseAssignment.case_id == case.id)
    ).first()
    timestamp = now_ms()
    if row is None:
        row = CaseAssignment(
            case_id=case.id,
            assigned_io_pis=assigned_io_pis,
            supervising_acp_pis=supervising_acp_pis,
            created_ts_ms=timestamp,
            updated_ts_ms=timestamp,
        )
        session.add(row)
        session.flush()
    return row


def assignment_for_case(session: Session, case_id: int) -> CaseAssignment | None:
    return session.exec(
        select(CaseAssignment).where(CaseAssignment.case_id == case_id)
    ).first()


def can_access_case(
    session: Session,
    *,
    case_id: int,
    officer_pis: str,
    role: str,
) -> bool:
    if role == OfficerRole.admin.value:
        return True
    assignment = assignment_for_case(session, case_id)
    # Legacy cases pre-date assignment data. Keeping them visible is the
    # non-breaking default until an explicit assignment is recorded.
    if assignment is None:
        return True
    if role == OfficerRole.io.value and assignment.assigned_io_pis == officer_pis:
        return True
    if role == OfficerRole.supervisor.value and assignment.supervising_acp_pis == officer_pis:
        return True
    watcher = session.exec(
        select(CaseWatcher).where(
            CaseWatcher.case_id == case_id,
            CaseWatcher.officer_pis == officer_pis,
        )
    ).first()
    if watcher is not None:
        return True
    escalation = session.exec(
        select(CaseEscalationAssignment)
        .where(CaseEscalationAssignment.case_id == case_id)
        .order_by(CaseEscalationAssignment.level.desc())
    ).first()
    return escalation is not None and escalation.new_owner_pis == officer_pis


def visible_cases(session: Session, *, officer_pis: str, role: str) -> list[Case]:
    rows = session.exec(select(Case).order_by(Case.updated_ts_ms.desc(), Case.id.desc())).all()
    return [
        row
        for row in rows
        if row.id is not None
        and can_access_case(
            session,
            case_id=row.id,
            officer_pis=officer_pis,
            role=role,
        )
    ]


def active_officer_profiles(session: Session) -> list[OfficerProfile]:
    return list(
        session.exec(
            select(OfficerProfile)
            .where(OfficerProfile.active == True)  # noqa: E712
            .order_by(OfficerProfile.role, OfficerProfile.name)
        ).all()
    )
