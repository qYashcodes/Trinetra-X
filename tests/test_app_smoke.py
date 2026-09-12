from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.main as main_module
from app.main import app


@pytest.fixture(autouse=True)
def isolate_app_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep workflow smoke tests from changing the live demo database or audit log."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[main_module.get_session] = session_override
    monkeypatch.setattr(main_module, "append_audit_event", lambda *args, **kwargs: {})
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_fixture_workflow_pages_render() -> None:
    with TestClient(app) as client:
        login = client.get("/login")
        assert login.status_code == 200
        assert "PROTOTYPE BASED LOGIN" in login.text
        assert "Sign in with CCTNS" in login.text
        assert "Sign in with SAHYOG Portal" in login.text
        assert 'href="https://cctns.megpolice.gov.in/Login.aspx"' in login.text
        assert 'href="https://parichay.nic.in/pnv1/assets/login?sid=1234567899"' in login.text
        assert "Sign in with Parichay" not in login.text

        response = client.post("/auth/prototype", data={"role": "io"}, follow_redirects=False)
        assert response.status_code == 303

        intake = client.get("/cases/new")
        assert intake.status_code == 200
        assert "Open a case from a filed complaint" in intake.text
        assert "Particulars read from the complaint" in intake.text
        assert "4 complaints" in intake.text
        assert "Switch to dark console" not in intake.text
        assert "हिन्दी" not in intake.text
        assert "A+" not in intake.text
        assert "NCRP/2026/MH/0091001" in intake.text
        assert "NCRP/2026/MH/0091002" in intake.text
        assert 'id="ack_no" name="ack_no" value=""' in intake.text
        assert intake.text.count('data-particular-state="pending"') == 8
        assert 'data-particular-state="missing"' not in intake.text
        assert "Awaiting input" in intake.text

        blank_ingest = client.post("/cases/ingest", data={"ack_no": "   "})
        assert blank_ingest.status_code == 200
        assert blank_ingest.text.count('data-particular-state="pending"') == 8
        assert 'data-particular-state="missing"' not in blank_ingest.text
        assert "Awaiting input" in blank_ingest.text
        assert "Manual addition required" not in blank_ingest.text

        ingested = client.post(
            "/cases/ingest",
            data={"ack_no": "NCRP/2026/MH/0084213"},
        )
        assert ingested.status_code == 200
        assert "8 of 8 particulars" in ingested.text
        assert "Case loaded" in ingested.text
        assert "21,940.00 USDT" in ingested.text
        assert ingested.text.count('data-particular-state="complete"') == 8

        incomplete = client.post(
            "/cases/ingest",
            data={"ack_no": "NCRP/2026/MH/0091002"},
        )
        assert incomplete.status_code == 200
        assert "6 of 8 particulars" in incomplete.text
        assert incomplete.text.count('data-particular-state="complete"') == 6
        assert incomplete.text.count('data-particular-state="missing"') == 2
        assert incomplete.text.count("Manual addition required") == 2
        assert 'action="/cases/start"' not in incomplete.text

        docket = client.get("/docket")
        assert docket.status_code == 200
        assert "NCRP/2026/MH/0084213" in docket.text
        assert docket.text.count("data-docket-row") == 8
        assert "Value under trace" in docket.text
        assert "2.41" in docket.text
        assert "8 of 37 shown" in docket.text
        assert "Switch to dark console" not in docket.text
        assert 'href="/notices"' in docket.text
        assert 'href="/notices/1"' not in docket.text

        trace = client.get("/traces/1")
        assert trace.status_code == 200
        assert "Live trace complete" in trace.text

        canvas = client.get("/cases/1/canvas?snapshot=1")
        assert canvas.status_code == 200
        assert "Trace graph exhibit" in canvas.text

        risk = client.post(
            "/risk-check",
            data={"address": "TGh3c9PkL8Qn7MuYbxV1aZP2R6EeSsQ7hC"},
        )
        assert risk.status_code == 200
        assert "Investigative signals found" in risk.text

        checks = client.post(
            "/findings/1/checks",
            data={
                "review_check": [
                    "complaint_particulars_verified",
                    "deposit_attribution_confirmed",
                    "india_compliance_desk_confirmed",
                    "delegated_authority_confirmed",
                ]
            },
            follow_redirects=False,
        )
        assert checks.status_code == 303

        notice_redirect = client.post("/findings/1/notice", follow_redirects=False)
        assert notice_redirect.status_code == 303

        notice = client.get("/notices/1")
        assert notice.status_code == 200
        assert "Freeze and information preservation notice" in notice.text


def test_api_contracts() -> None:
    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "io"})
        client.get("/docket")

        chain = client.get("/api/chains/resolve", params={"seed": "0x0000000000000000000000000000000000000000"})
        assert chain.status_code == 200
        assert chain.json()["family"] == "EVM"

        search = client.get("/api/search", params={"q": "0084213"})
        assert search.status_code == 200
        assert search.json()["results"]

        graph = client.get("/api/cases/1/graph?view=case")
        assert graph.status_code == 200
        assert graph.json()["view"] == "case"
