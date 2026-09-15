from __future__ import annotations

from collections.abc import Iterator
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.main as main_module
from app.main import app
from app.models import Case, CaseStage, Dispatch, Finding, Notice
from app.repository import finding_review_specs, initial_finding_review_checks


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


def login_and_seed(client: TestClient, role: str = "io") -> int:
    login = client.post("/auth/prototype", data={"role": role}, follow_redirects=False)
    assert login.status_code == 303
    docket = client.get("/docket")
    assert docket.status_code == 200
    return 1


def test_generic_notice_entry_preserves_review_gate_on_fresh_state(isolated_engine) -> None:
    with TestClient(app) as client:
        login = client.post("/auth/prototype", data={"role": "io"}, follow_redirects=False)
        assert login.status_code == 303

        neutral_risk = client.post(
            "/risk-check",
            data={"address": "TGh3c9PkL8Qn7MuYbxV1aZP2R6EeSsQ7hC"},
        )
        assert neutral_risk.status_code == 200
        assert "This address appears in a complaint-linked custody finding" in neutral_risk.text
        assert "No notice is recorded" in neutral_risk.text
        assert 'href="/notices"' in neutral_risk.text
        assert "active restraint" not in neutral_risk.text.lower()
        assert "Restraint in force" not in neutral_risk.text
        assert "Notice out" not in neutral_risk.text

        entry = client.get("/notices", follow_redirects=False)
        assert entry.status_code == 307
        assert entry.headers["location"] == "/findings/1"
        with Session(isolated_engine) as session:
            finding = session.get(Finding, 1)
            assert finding is not None
            assert finding.review_checks == initial_finding_review_checks()
            assert session.exec(select(Notice)).first() is None

        gated_page = client.get(entry.headers["location"])
        assert gated_page.status_code == 200
        assert "Locked: <span data-blocked-count>2</span> checks remaining" in gated_page.text
        assert 'href="/notices"' in gated_page.text
        assert 'href="/notices/1"' not in gated_page.text

        keys = [str(spec["key"]) for spec in finding_review_specs()]
        prepared = client.post(
            "/findings/1/checks",
            data={
                "review_check": keys,
                "action": "prepare",
                "csrf_token": csrf_from(gated_page.text),
            },
            follow_redirects=False,
        )
        assert prepared.status_code == 303

        draft_risk = client.post(
            "/risk-check",
            data={"address": "TGh3c9PkL8Qn7MuYbxV1aZP2R6EeSsQ7hC"},
        )
        assert "A draft notice is recorded for this address" in draft_risk.text
        assert "It has not been countersigned or dispatched" in draft_risk.text
        assert "active restraint" not in draft_risk.text.lower()

        existing_entry = client.get("/notices", follow_redirects=False)
        assert existing_entry.status_code == 307
        assert existing_entry.headers["location"] == "/notices/1"


def test_finding_review_contract_uses_explicit_persisted_keys(isolated_engine) -> None:
    expected = initial_finding_review_checks()
    keys = [str(spec["key"]) for spec in finding_review_specs()]

    with TestClient(app) as client:
        finding_id = login_and_seed(client)
        page = client.get(f"/findings/{finding_id}")
        assert page.status_code == 200
        assert "Locked: <span data-blocked-count>2</span> checks remaining" in page.text
        for key, checked in expected.items():
            marker = f'name="review_check" value="{key}"'
            assert marker in page.text
            rendered_input = page.text.split(marker, 1)[1].split(">", 1)[0]
            assert (" checked" in rendered_input) is checked

        with Session(isolated_engine) as session:
            finding = session.get(Finding, finding_id)
            assert finding is not None
            assert finding.review_checks == expected

        subset = keys[:1] + keys[2:3]
        saved = client.post(
            f"/findings/{finding_id}/checks",
            data={"review_check": subset, "csrf_token": csrf_from(page.text)},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        with Session(isolated_engine) as session:
            finding = session.get(Finding, finding_id)
            assert finding is not None
            assert finding.review_checks == {key: key in subset for key in keys}

        invalid = client.post(
            f"/findings/{finding_id}/checks",
            data={
                "review_check": [keys[0], "positional_check_0"],
                "csrf_token": csrf_from(client.get(f"/findings/{finding_id}").text),
            },
        )
        assert invalid.status_code == 400
        with Session(isolated_engine) as session:
            finding = session.get(Finding, finding_id)
            assert finding is not None
            assert finding.review_checks == {key: key in subset for key in keys}

        prepared = client.post(
            f"/findings/{finding_id}/checks",
            data={
                "review_check": keys,
                "action": "prepare",
                "csrf_token": csrf_from(client.get(f"/findings/{finding_id}").text),
            },
            follow_redirects=False,
        )
        assert prepared.status_code == 303
        assert prepared.headers["location"] == "/notices/1"
        with Session(isolated_engine) as session:
            finding = session.get(Finding, finding_id)
            notice = session.get(Notice, 1)
            assert finding is not None
            assert notice is not None
            assert finding.review_checks == {key: True for key in keys}
            assert notice.status == "draft"


def test_workflow_forms_reject_missing_csrf_token(isolated_engine) -> None:
    with TestClient(app) as client:
        finding_id = login_and_seed(client)
        denied = client.post(
            f"/findings/{finding_id}/checks",
            data={"review_check": [str(spec["key"]) for spec in finding_review_specs()]},
        )
        assert denied.status_code == 403
        assert "Invalid or missing workflow token" in denied.text


def test_notice_requires_distinct_persisted_countersignature_before_dispatch(
    isolated_engine,
) -> None:
    keys = [str(spec["key"]) for spec in finding_review_specs()]

    with TestClient(app) as investigator, TestClient(app) as supervisor:
        finding_id = login_and_seed(investigator)
        prepared = investigator.post(
            f"/findings/{finding_id}/checks",
            data={
                "review_check": keys,
                "action": "prepare",
                "csrf_token": csrf_from(investigator.get(f"/findings/{finding_id}").text),
            },
            follow_redirects=False,
        )
        assert prepared.status_code == 303
        notice_id = 1

        draft = investigator.get(f"/notices/{notice_id}")
        assert draft.status_code == 200
        assert 'data-notice-status="draft"' in draft.text
        assert 'data-countersigned="false"' in draft.text
        assert "Not yet requested" in draft.text
        assert "Request countersignature" in draft.text
        assert "Template BNSS-106 v3.2 · specimen · legal review pending" in draft.text
        assert "SPECIMEN · NOT FOR LIVE DISPATCH" in draft.text
        assert "approved 2026-07-01" not in draft.text

        sahyog = investigator.get(f"/api/notices/{notice_id}/sahyog-export")
        assert sahyog.status_code == 200
        sahyog_data = sahyog.json()
        assert sahyog_data["submission"] == "integration_pending"
        assert sahyog_data["submission_status"] == "integration_pending"
        assert sahyog_data["specimen_only"] is True
        assert sahyog_data["external_submission_performed"] is False
        assert sahyog_data["integration_boundary"] == {
            "provider": "SAHYOG",
            "approved_schema_configured": False,
            "credentials_configured": False,
            "live_dispatch_enabled": False,
            "reason": "Government portal submission requires approved schemas, provider metadata and credentials.",
        }

        premature = investigator.post(
            f"/notices/{notice_id}/dispatch",
            data={
                "channel": ["portal", "email"],
                "deadline_hours": "24",
                "csrf_token": csrf_from(draft.text),
            },
        )
        assert premature.status_code == 409

        requested = investigator.post(
            f"/notices/{notice_id}/countersign-request",
            data={"csrf_token": csrf_from(draft.text)},
            follow_redirects=False,
        )
        assert requested.status_code == 303
        with Session(isolated_engine) as session:
            notice = session.get(Notice, notice_id)
            case = session.get(Case, 1)
            assert notice is not None
            assert case is not None
            assert notice.status == "awaiting_countersignature"
            assert notice.countersigned_by_pis is None
            assert case.stage == CaseStage.awaiting_countersignature

        awaiting = investigator.get(f"/notices/{notice_id}")
        assert "Awaiting ACP S. Deshmukh" in awaiting.text
        assert 'data-countersigned="false"' in awaiting.text
        assert "data-request-sign" not in awaiting.text

        self_sign = investigator.post(
            f"/notices/{notice_id}/countersign",
            data={"csrf_token": csrf_from(awaiting.text)},
        )
        assert self_sign.status_code == 403

        login_and_seed(supervisor, "supervisor")
        supervisor_view = supervisor.get(f"/notices/{notice_id}")
        assert "data-countersign" in supervisor_view.text
        signed = supervisor.post(
            f"/notices/{notice_id}/countersign",
            data={"csrf_token": csrf_from(supervisor_view.text)},
            follow_redirects=False,
        )
        assert signed.status_code == 303
        with Session(isolated_engine) as session:
            notice = session.get(Notice, notice_id)
            case = session.get(Case, 1)
            assert notice is not None
            assert case is not None
            assert notice.status == "countersigned"
            assert notice.countersigned_by_pis == "61207"
            assert notice.countersigned_ts_ms is not None
            assert case.stage == CaseStage.countersigned

        cannot_dispatch_as_supervisor = supervisor.post(
            f"/notices/{notice_id}/dispatch",
            data={
                "channel": ["portal", "email"],
                "deadline_hours": "72",
                "csrf_token": csrf_from(supervisor.get(f"/notices/{notice_id}").text),
            },
        )
        assert cannot_dispatch_as_supervisor.status_code == 403

        signed_view = investigator.get(f"/notices/{notice_id}")
        assert 'data-countersigned="true"' in signed_view.text
        assert 'data-can-dispatch="true"' in signed_view.text
        assert "Countersigned by ACP S. Deshmukh" in signed_view.text

        dispatched = investigator.post(
            f"/notices/{notice_id}/dispatch",
            data={
                "channel": ["portal", "email", "nodal-copy"],
                "deadline_hours": "72",
                "csrf_token": csrf_from(signed_view.text),
            },
            follow_redirects=False,
        )
        assert dispatched.status_code == 303
        with Session(isolated_engine) as session:
            notice = session.get(Notice, notice_id)
            case = session.get(Case, 1)
            rows = session.exec(
                select(Dispatch).where(Dispatch.notice_id == notice_id).order_by(Dispatch.id)
            ).all()
            assert notice is not None
            assert case is not None
            assert notice.status == "dispatched"
            assert notice.deadline_hours == 72
            assert notice.pdf_sha256
            original_hash = notice.pdf_sha256
            assert case.stage == CaseStage.notice_out
            assert [row.channel for row in rows] == [
                "le-portal",
                "compliance-email",
                "state-nodal-copy",
            ]
            assert [row.status for row in rows] == ["sent", "sent", "failed"]

        repeated = investigator.post(
            f"/notices/{notice_id}/dispatch",
            data={
                "channel": "portal",
                "deadline_hours": "24",
                "csrf_token": csrf_from(investigator.get(f"/notices/{notice_id}").text),
            },
        )
        assert repeated.status_code == 409
        with Session(isolated_engine) as session:
            notice = session.get(Notice, notice_id)
            rows = session.exec(select(Dispatch).where(Dispatch.notice_id == notice_id)).all()
            assert notice is not None
            assert notice.pdf_sha256 == original_hash
            assert len(rows) == 3

        persisted = investigator.get(f"/notices/{notice_id}")
        assert 'data-notice-status="dispatched"' in persisted.text
        assert "Dispatched, awaiting acknowledgement" in persisted.text
        assert "seventy-two hours" in persisted.text
        assert "Failed — retry required" in persisted.text

        persisted_risk = investigator.post(
            "/risk-check",
            data={"address": "TGh3c9PkL8Qn7MuYbxV1aZP2R6EeSsQ7hC"},
        )
        assert persisted_risk.status_code == 200
        assert "A notice dispatch is recorded for this address" in persisted_risk.text
        assert "3 channel records exist and 1 recorded a delivery failure" in persisted_risk.text
        assert "No restraint confirmation is inferred from dispatch alone" in persisted_risk.text
        assert "active restraint" not in persisted_risk.text.lower()
        assert "Restraint in force" not in persisted_risk.text

    actions = [row["action"] for row in isolated_engine.audit_rows]  # type: ignore[attr-defined]
    assert "notice.countersign_request" in actions
    assert "notice.countersign" in actions
    assert "notice.dispatch" in actions


def test_sahyog_export_rejects_notice_without_finding(isolated_engine) -> None:
    ts = 1_788_000_000_000
    with Session(isolated_engine) as session:
        session.add(
            Case(
                ack_no="NCRP/2026/MH/ORPHAN",
                category="investment_fraud",
                jurisdiction="MH",
                filed_ts_ms=ts,
                amount_reported_base=1,
                asset_symbol="USDT",
                chain_family="TRON",
                chain_network="mainnet",
                reported_address="TXorphanNotice111111111111111111111111",
                payment_txid="orphan-notice",
                payment_ts_ms=ts,
                stage=CaseStage.notice_draft,
                created_ts_ms=ts,
                updated_ts_ms=ts,
            )
        )
        session.commit()
        session.add(
            Notice(
                case_id=1,
                finding_id=999,
                notice_no="MH-CYBER/ORPHAN/1",
                status="draft",
                deadline_hours=24,
                created_by_pis="48421",
                created_ts_ms=ts,
            )
        )
        session.commit()

    with TestClient(app) as client:
        login_and_seed(client)
        response = client.get("/api/notices/1/sahyog-export")

    assert response.status_code == 404
    assert "Finding not found for notice." in response.text
