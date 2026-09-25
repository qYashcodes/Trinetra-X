from __future__ import annotations

from collections.abc import Iterator
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.main as main_module
from app.main import app
from app.models import Case, Dispatch, Finding, Notice, NoticeTrackerEvent


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


@pytest.fixture
def isolated_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    audit_rows: list[dict] = []

    def audit_override(actor: str, action: str, subject: str, data: dict | None = None) -> dict:
        row = {"actor": actor, "action": action, "subject": subject, "data": data or {}}
        audit_rows.append(row)
        return row

    app.dependency_overrides[main_module.get_session] = session_override
    monkeypatch.setattr(main_module, "append_audit_event", audit_override)
    engine.audit_rows = audit_rows  # type: ignore[attr-defined]
    try:
        yield engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _seed_dispatched_notice(engine: object) -> None:
    ts = 1_789_337_235_000
    with Session(engine) as session:
        case = Case(
            ack_no="NCRP/2026/MH/TRACKER",
            category="investment_fraud",
            jurisdiction="MH",
            filed_ts_ms=ts,
            amount_reported_base=1_000_000,
            asset_symbol="USDT",
            asset_decimals=6,
            chain_family="TRON",
            chain_network="mainnet",
            reported_address="TTrackerSeed111111111111111111111111",
            payment_txid="a" * 64,
            payment_ts_ms=ts,
            created_ts_ms=ts,
            updated_ts_ms=ts,
        )
        session.add(case)
        session.commit()
        finding = Finding(
            case_id=int(case.id),
            snapshot_id=1,
            terminal_kind="vasp_deposit",
            custodian_key="coinsphere",
            deposit_address="TDepositTracker111111111111111111111",
            amount_credited_base=900_000,
            created_ts_ms=ts,
        )
        session.add(finding)
        session.commit()
        notice = Notice(
            case_id=int(case.id),
            finding_id=int(finding.id),
            notice_no="MH-CYBER/TRACKER/1",
            status="dispatched",
            deadline_hours=24,
            created_by_pis="48421",
            created_ts_ms=ts,
            dispatched_ts_ms=ts,
            tracker_status="dispatched",
            tracker_updated_ts_ms=ts,
        )
        session.add(notice)
        session.commit()
        session.add(
            Dispatch(
                notice_id=int(notice.id),
                channel="le-portal",
                target="coinsphere-le",
                status="sent",
                attempts=1,
                created_ts_ms=ts,
                updated_ts_ms=ts,
            )
        )
        session.commit()


def test_dispatch_tracker_lists_filters_updates_and_requires_csrf(isolated_engine) -> None:
    _seed_dispatched_notice(isolated_engine)

    with TestClient(app) as client:
        login = client.post("/auth/prototype", data={"role": "io"}, follow_redirects=False)
        assert login.status_code == 303

        page = client.get("/dispatch-tracker")
        assert page.status_code == 200
        assert "Dispatch tracker" in page.text
        assert "Dispatch is a pending state; it does not confirm restraint." in page.text
        assert "Overdue" in page.text
        assert "NCRP/2026/MH/TRACKER" in page.text

        filtered = client.get("/dispatch-tracker?status=overdue&vasp=coin&case=TRACKER")
        assert filtered.status_code == 200
        assert "MH-CYBER/TRACKER/1" in filtered.text

        missing_csrf = client.post(
            "/dispatch-tracker/1/status",
            data={"tracker_status": "responded"},
        )
        assert missing_csrf.status_code == 403

        updated = client.post(
            "/dispatch-tracker/1/status",
            data={
                "csrf_token": csrf_from(page.text),
                "tracker_status": "responded",
                "sub_outcome": "kyc_records_received",
                "note": "Records received by email.",
            },
            follow_redirects=False,
        )
        assert updated.status_code == 303
        with Session(isolated_engine) as session:
            notice = session.get(Notice, 1)
            assert notice is not None
            assert notice.tracker_status == "responded"
            assert notice.tracker_sub_outcome == "kyc_records_received"
            assert notice.tracker_last_note == "Records received by email."
            events = session.exec(select(NoticeTrackerEvent)).all()
            assert len(events) == 1
            assert events[0].to_status == "responded"

        responded = client.get("/dispatch-tracker?status=responded")
        assert "Responded" in responded.text
        assert "KYC or account records received" in responded.text
        assert "Status updated manually by officer" in responded.text

        escalated = client.post(
            "/dispatch-tracker/1/escalate",
            data={
                "csrf_token": csrf_from(responded.text),
                "note": "Escalated to nodal officer.",
            },
            follow_redirects=False,
        )
        assert escalated.status_code == 303
        with Session(isolated_engine) as session:
            notice = session.get(Notice, 1)
            assert notice is not None
            assert notice.tracker_status == "escalated"
            assert len(session.exec(select(NoticeTrackerEvent)).all()) == 2

    actions = [row["action"] for row in isolated_engine.audit_rows]  # type: ignore[attr-defined]
    assert "notice.tracker_update" in actions
    assert "notice.tracker_escalate" in actions


def test_dispatch_tracker_role_switch_changes_visible_workbench(isolated_engine) -> None:
    _seed_dispatched_notice(isolated_engine)

    with TestClient(app) as client:
        login = client.post("/auth/prototype", data={"role": "io"}, follow_redirects=False)
        assert login.status_code == 303

        io_page = client.get("/dispatch-tracker")
        assert io_page.status_code == 200
        assert "IO workqueue view" in io_page.text
        assert "Status update desk" in io_page.text
        assert "tracker-update-form" in io_page.text
        assert "ACP oversight controls" not in io_page.text

        switched = client.post(
            "/auth/prototype",
            data={"role": "supervisor", "next_url": "/dispatch-tracker"},
            follow_redirects=False,
        )
        assert switched.status_code == 303
        assert switched.headers["location"] == "/dispatch-tracker"
        acp_page = client.get("/dispatch-tracker")
        assert acp_page.status_code == 200
        assert "ACP oversight view" in acp_page.text
        assert "Escalation and SLA supervision" in acp_page.text
        assert "ACP oversight controls" in acp_page.text
        assert "Status updates remain with the assigned IO" in acp_page.text
        assert "tracker-update-form" not in acp_page.text
