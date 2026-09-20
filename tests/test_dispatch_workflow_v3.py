from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    AuditOutbox,
    Case,
    CaseAssignment,
    CaseEscalationAssignment,
    CaseWatcher,
    DispatchRecord,
    Finding,
    Notice,
    OfficerProfile,
    OfficerRole,
)
from app.services.dispatch_workflow import (
    dispatch_sla_view,
    ensure_dispatch_record,
    escalate_dispatch,
    register_sla_breach,
    set_dispatch_stage,
)


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(session: Session, ts: int = 1_700_000_000_000) -> tuple[Notice, DispatchRecord]:
    case = Case(
        ack_no="NCRP/V3/DISPATCH",
        category="fraud",
        jurisdiction="MH",
        filed_ts_ms=ts,
        amount_reported_base=10_000_000,
        asset_symbol="USDT",
        chain_family="TRON",
        chain_network="mainnet",
        reported_address="TDispatchV31111111111111111111111111",
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.flush()
    for pis, name, role in (
        ("io-1", "One", OfficerRole.io),
        ("acp-1", "Supervisor", OfficerRole.supervisor),
        ("acp-2", "Next", OfficerRole.supervisor),
    ):
        session.add(
            OfficerProfile(
                pis=pis,
                name=name,
                rank="Inspector" if role == OfficerRole.io else "ACP",
                unit="Cyber",
                role=role,
                created_ts_ms=ts,
                updated_ts_ms=ts,
            )
        )
    session.add(
        CaseAssignment(
            case_id=int(case.id or 0),
            assigned_io_pis="io-1",
            supervising_acp_pis="acp-1",
            created_ts_ms=ts,
            updated_ts_ms=ts,
        )
    )
    finding = Finding(
        case_id=int(case.id or 0),
        snapshot_id=1,
        terminal_kind="vasp_deposit",
        custodian_key="coinsphere",
        deposit_address="TDispatchDeposit111111111111111111111",
        amount_credited_base=9_000_000,
        created_ts_ms=ts,
    )
    session.add(finding)
    session.flush()
    notice = Notice(
        case_id=int(case.id or 0),
        finding_id=int(finding.id or 0),
        notice_no="V3-DISPATCH-1",
        created_by_pis="io-1",
        created_ts_ms=ts,
    )
    session.add(notice)
    session.flush()
    record = ensure_dispatch_record(session, notice, version=None, dispatched_ts_ms=ts)
    session.commit()
    return notice, record


def test_sla_breach_event_is_queued_exactly_once() -> None:
    engine = _engine()
    with Session(engine) as session:
        _notice, record = _seed(session)
        after_due = int(record.sla_due_ts_ms or 0) + 1
        assert register_sla_breach(session, record, timestamp_ms=after_due) is True
        assert register_sla_breach(session, record, timestamp_ms=after_due + 1) is False
        session.commit()
        rows = session.exec(select(AuditOutbox)).all()
        assert len(rows) == 1
        assert rows[0].payload["action"] == "SLA_BREACHED"


def test_acknowledgement_freezes_clock_and_escalation_keeps_acp_watcher() -> None:
    engine = _engine()
    with Session(engine) as session:
        _notice, record = _seed(session)
        midpoint = int(record.dispatched_ts_ms or 0) + 12 * 60 * 60 * 1000
        set_dispatch_stage(
            session,
            record,
            stage="acknowledged",
            actor_pis="io-1",
            acknowledgement_state="received",
            timestamp_ms=midpoint,
        )
        frozen = dispatch_sla_view(record, timestamp_ms=midpoint + 7 * 24 * 60 * 60 * 1000)
        assert frozen["frozen"] is True
        assert frozen["tone"] == "amber"

        escalation = escalate_dispatch(
            session,
            record,
            next_owner_pis="acp-2",
            reason="No complete response in the recorded workflow.",
            assigning_acp_pis="acp-1",
        )
        session.commit()
        assert escalation.level == 1
        assert record.current_owner_pis == "acp-2"
        assert session.exec(select(CaseEscalationAssignment)).one().new_owner_pis == "acp-2"
        watcher = session.exec(select(CaseWatcher)).one()
        assert watcher.officer_pis == "acp-1"
