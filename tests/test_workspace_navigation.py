from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    Case,
    CaseAssignment,
    CaseEscalationAssignment,
    CaseStage,
    CaseWatcher,
    Finding,
    Notice,
    NoticeDraft,
    TraceSnapshot,
)
from app.services.officers import visible_cases
from app.services.workspace import (
    ACTIVE_TRACE_STATUSES,
    OPEN_NOTICE_STATUSES,
    navigation_counts,
    navigation_gates,
)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _case(session: Session, suffix: str, *, stage: CaseStage) -> Case:
    row = Case(
        ack_no=f"NCRP/2026/TEST/NAV-{suffix}",
        category="investment fraud",
        jurisdiction="Test Cyber Cell",
        filed_ts_ms=1,
        amount_reported_base=1_000,
        asset_symbol="USDT",
        asset_decimals=6,
        chain_family="TRON",
        chain_network="mainnet",
        reported_address=f"T{suffix}",
        payment_txid=suffix.lower().ljust(64, "0"),
        payment_ts_ms=1,
        stage=stage,
        created_ts_ms=1,
        updated_ts_ms=1,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _snapshot(session: Session, case: Case, suffix: str, *, status: str) -> TraceSnapshot:
    row = TraceSnapshot(
        case_id=int(case.id),
        version=1,
        status=status,
        cache_identity=f"nav-{suffix}",
        result_json={},
        sha256=suffix.lower().ljust(64, "f"),
        chain_family="TRON",
        chain_network="mainnet",
        asset_symbol="USDT",
        asset_decimals=6,
        started_ts_ms=1,
    )
    session.add(row)
    session.flush()
    return row


def _notice(
    session: Session,
    case: Case,
    suffix: str,
    *,
    status: str,
    tracker_status: str,
) -> None:
    session.add(
        Notice(
            case_id=int(case.id),
            finding_id=10_000 + int(case.id),
            notice_no=f"NOTICE-NAV-{suffix}",
            status=status,
            tracker_status=tracker_status,
            created_by_pis="IO-OWNER",
            created_ts_ms=1,
        )
    )


def _reference_navigation_counts(
    session: Session,
    *,
    officer_pis: str,
    role: str,
) -> dict[str, int]:
    cases = visible_cases(session, officer_pis=officer_pis, role=role)
    case_ids = {int(case.id) for case in cases if case.id is not None}
    snapshots = session.exec(select(TraceSnapshot)).all()
    notices = session.exec(select(Notice)).all()
    return {
        "case_docket": sum(case.stage != CaseStage.closed_no_custody for case in cases),
        "active_traces": sum(
            row.case_id in case_ids and str(row.status).lower() in ACTIVE_TRACE_STATUSES
            for row in snapshots
        ),
        "freeze_notices": sum(
            row.case_id in case_ids
            and row.status in OPEN_NOTICE_STATUSES
            and row.tracker_status not in {"responded"}
            for row in notices
        ),
    }


def test_navigation_aggregate_counts_match_existing_visibility_and_status_rules(
    db: Session,
) -> None:
    legacy = _case(db, "LEGACY", stage=CaseStage.intake)
    owned = _case(db, "OWNED", stage=CaseStage.trace_running)
    watched = _case(db, "WATCHED", stage=CaseStage.notice_draft)
    escalated = _case(db, "ESCALATED", stage=CaseStage.closed_no_custody)
    other = _case(db, "OTHER", stage=CaseStage.trace_incomplete)

    for case in (owned, watched, escalated, other):
        db.add(
            CaseAssignment(
                case_id=int(case.id),
                assigned_io_pis="IO-OWNER" if case is owned else "IO-OTHER",
                supervising_acp_pis="ACP-OWNER" if case is other else "ACP-OTHER",
                created_ts_ms=1,
                updated_ts_ms=1,
            )
        )
    db.add(
        CaseWatcher(
            case_id=int(watched.id),
            officer_pis="IO-OWNER",
            reason="characterization",
            created_ts_ms=1,
        )
    )
    db.add(
        CaseEscalationAssignment(
            case_id=int(escalated.id),
            dispatch_record_id=99_999,
            level=1,
            previous_owner_pis="IO-OTHER",
            new_owner_pis="IO-OWNER",
            reason="characterization",
            assigned_by_pis="ACP-OTHER",
            created_ts_ms=1,
        )
    )

    _snapshot(db, legacy, "LEGACY-ACTIVE", status="RUNNING")
    _snapshot(db, owned, "OWNED-PAUSED", status="paused")
    _snapshot(db, watched, "WATCHED-COMPLETE", status="complete")
    _snapshot(db, other, "OTHER-BACKOFF", status="provider_backoff")
    _notice(db, legacy, "LEGACY-OPEN", status="draft", tracker_status="drafted")
    _notice(db, owned, "OWNED-RESPONDED", status="dispatched", tracker_status="responded")
    _notice(db, watched, "WATCHED-OPEN", status="countersigned", tracker_status="pending")
    _notice(db, other, "OTHER-CLOSED", status="closed", tracker_status="pending")
    db.commit()

    identities = [
        ("IO-OWNER", "io"),
        ("ACP-OWNER", "supervisor"),
        ("ADMIN", "admin"),
        ("IO-NONE", "io"),
    ]
    for officer_pis, role in identities:
        assert navigation_counts(db, officer_pis=officer_pis, role=role) == (
            _reference_navigation_counts(db, officer_pis=officer_pis, role=role)
        )


def test_navigation_gates_follow_trace_finding_and_generated_notice_state(db: Session) -> None:
    case = _case(db, "GATE", stage=CaseStage.trace_running)
    active = {"id": case.id}

    initial = navigation_gates(db, active)
    assert initial["trace"]["enabled"] is False
    assert initial["canvas"]["enabled"] is False
    assert initial["finding"]["enabled"] is False
    assert initial["notice"]["enabled"] is False

    running = _snapshot(db, case, "GATE-RUNNING", status="running")
    db.commit()
    active["snapshot_id"] = running.id
    running_state = navigation_gates(db, active)
    assert running_state["trace"] == {
        "enabled": True,
        "href": "/traces",
        "reason": "Available after a case is ingested and trace is started.",
    }
    assert running_state["canvas"]["enabled"] is False
    assert running_state["finding"]["enabled"] is False

    running.status = "complete"
    running.closed_ts_ms = 2
    case.stage = CaseStage.custody_found
    db.add(case)
    db.add(running)
    db.commit()
    traced = navigation_gates(db, active)
    assert traced["canvas"] == {
        "enabled": True,
        "href": f"/cases/{case.id}/canvas?snapshot={running.id}",
        "reason": "Available after the active trace completes.",
    }
    assert traced["finding"]["enabled"] is False
    assert traced["notice"]["enabled"] is False

    finding = Finding(
        case_id=int(case.id or 0),
        snapshot_id=int(running.id or 0),
        terminal_kind="vasp_deposit",
        custodian_key="coinsphere",
        deposit_address="TGatedDeposit1111111111111111111111",
        amount_credited_base=500,
        created_ts_ms=3,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    active["finding_id"] = finding.id
    found = navigation_gates(db, active)
    assert found["finding"] == {
        "enabled": True,
        "href": f"/findings/{finding.id}",
        "reason": "Available once the trace has produced a custody finding.",
    }
    assert found["notice"]["enabled"] is False

    notice = Notice(
        case_id=int(case.id or 0),
        finding_id=int(finding.id or 0),
        notice_no="NOTICE-NAV-GATE",
        status="draft",
        tracker_status="drafted",
        created_by_pis="IO-OWNER",
        created_ts_ms=4,
    )
    db.add(notice)
    db.commit()
    db.refresh(notice)
    draft = NoticeDraft(
        case_id=int(case.id or 0),
        finding_id=int(finding.id or 0),
        notice_id=int(notice.id or 0),
        generated=False,
        active_version_no=None,
        created_by_pis="IO-OWNER",
        created_ts_ms=4,
        updated_ts_ms=4,
    )
    db.add(draft)
    db.commit()
    active["notice_id"] = notice.id
    assert navigation_gates(db, active)["notice"]["enabled"] is False

    draft.generated = True
    draft.active_version_no = 1
    draft.updated_ts_ms = 5
    db.add(draft)
    db.commit()
    assert navigation_gates(db, active)["notice"] == {
        "enabled": True,
        "href": "/notices",
        "reason": "Available after a freeze notice draft is generated through the workflow.",
    }
