from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.main as main_module
from app.main import app
from app.models import Case
from app.repository import get_or_create_trace
from app.services.time import now_ms


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


@contextmanager
def isolated_db_session() -> Iterator[Session]:
    override = app.dependency_overrides[main_module.get_session]
    generator = override()
    session = next(generator)
    try:
        yield session
    finally:
        generator.close()


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
        assert "Sign in as Insp. R. Kulkarni" in login.text
        assert "Sign in as ACP S. Deshmukh" in login.text
        assert "Countersignature officer" in login.text
        assert "Sign in with CCTNS" in login.text
        assert "Sign in with SAHYOG Portal" in login.text
        assert 'href="https://cctns.megpolice.gov.in/Login.aspx"' in login.text
        assert 'href="https://cctns.megpolice.gov.in/Login.aspx" target="_self" rel="noreferrer" referrerpolicy="no-referrer"' in login.text
        assert 'href="https://parichay.nic.in/pnv1/assets/login?sid=1234567899"' in login.text
        assert "Sign in with Parichay" not in login.text

        supervisor_login = client.get("/auth/prototype/supervisor")
        assert supervisor_login.status_code == 200
        assert "Supervisor view" in supervisor_login.text
        assert "Sign in as ACP S. Deshmukh" in supervisor_login.text
        assert "Return as Insp. R. Kulkarni" in supervisor_login.text

        response = client.post("/auth/prototype", data={"role": "io"}, follow_redirects=False)
        assert response.status_code == 303

        intake = client.get("/cases/new")
        assert intake.status_code == 200
        assert "Switch to ACP" in intake.text
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

        intake_token = csrf_from(intake.text)
        blank_ingest = client.post("/cases/ingest", data={"ack_no": "   ", "csrf_token": intake_token})
        assert blank_ingest.status_code == 200
        assert blank_ingest.text.count('data-particular-state="pending"') == 8
        assert 'data-particular-state="missing"' not in blank_ingest.text
        assert "Awaiting input" in blank_ingest.text
        assert "Manual addition required" not in blank_ingest.text

        ingested = client.post(
            "/cases/ingest",
            data={"ack_no": "NCRP/2026/MH/0084213", "csrf_token": intake_token},
        )
        assert ingested.status_code == 200
        assert "8 of 8 particulars" in ingested.text
        assert "Case loaded" in ingested.text
        assert "21,940.00 USDT" in ingested.text
        assert ingested.text.count('data-particular-state="complete"') == 8

        incomplete = client.post(
            "/cases/ingest",
            data={"ack_no": "NCRP/2026/MH/0091002", "csrf_token": intake_token},
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
        assert "Working case trail" in docket.text
        assert "Trace snapshot sealed" in docket.text
        assert "Evidence manifest" in docket.text
        assert "Evidence bundle" in docket.text
        assert "Integration status" in docket.text
        assert "Switch to dark console" not in docket.text
        assert 'href="/notices"' in docket.text
        assert 'href="/notices/1"' not in docket.text

        integrations = client.get("/integrations")
        assert integrations.status_code == 200
        assert "Integration readiness" in integrations.text
        assert "Fixture mode is intentional" in integrations.text
        assert "Secrets rendered" in integrations.text
        assert "dev-only-change-me" not in integrations.text
        assert "change-me-in-local-env" not in integrations.text

        trace = client.get("/traces/1")
        assert trace.status_code == 200
        assert "Live trace complete" in trace.text
        assert "data-progress-value" not in trace.text
        assert "data-progress-stage" in trace.text

        canvas = client.get("/cases/1/canvas?snapshot=1")
        assert canvas.status_code == 200
        assert "Trace graph exhibit" in canvas.text
        assert 'data-omega-graph' in canvas.text
        assert '/static/omega-graph.js' in canvas.text
        assert 'href="/findings/1"' in canvas.text
        assert "Open custody finding" in canvas.text

        risk = client.post(
            "/risk-check",
            data={"address": "TGh3c9PkL8Qn7MuYbxV1aZP2R6EeSsQ7hC"},
        )
        assert risk.status_code == 200
        assert "Investigative signals found" in risk.text

        checks = client.post(
            "/findings/1/checks",
            data={
                "csrf_token": csrf_from(client.get("/findings/1").text),
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

        notice_redirect = client.post(
            "/findings/1/notice",
            data={"csrf_token": csrf_from(client.get("/findings/1").text)},
            follow_redirects=False,
        )
        assert notice_redirect.status_code == 303

        notice = client.get("/notices/1")
        assert notice.status_code == 200
        assert "Freeze and information preservation notice" in notice.text
        tracker = client.get("/dispatch-tracker")
        assert tracker.status_code == 200
        assert "Dispatch tracker" in tracker.text


def test_ingested_reference_becomes_active_working_case() -> None:
    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "io"})
        intake_token = csrf_from(client.get("/cases/new").text)

        ingested = client.post(
            "/cases/ingest",
            data={"ack_no": "NCRP/2026/MH/0091001", "csrf_token": intake_token},
        )
        assert ingested.status_code == 200
        assert "8 of 8 particulars" in ingested.text
        assert "Case loaded" in ingested.text

        started = client.post(
            "/cases/start",
            data={"ack_no": "NCRP/2026/MH/0091001", "reviewed": "yes", "csrf_token": intake_token},
            follow_redirects=False,
        )
        assert started.status_code == 303
        trace_location = started.headers["location"]
        assert re.fullmatch(r"/traces/\d+", trace_location)
        snapshot_id = trace_location.rsplit("/", 1)[1]

        trace = client.get("/traces")
        assert trace.status_code == 200
        assert "NCRP/2026/MH/0091001" in trace.text

        docket = client.get("/docket")
        assert docket.status_code == 200
        assert "NCRP/2026/MH/0091001" in docket.text
        assert "Working case" in docket.text
        assert 'data-active-case="true"' in docket.text
        canvas_match = re.search(r'href="/cases/(\d+)/canvas\?snapshot=' + snapshot_id + r'"', docket.text)
        assert canvas_match
        case_id = canvas_match.group(1)
        assert re.search(r'href="/findings/\d+"', docket.text)
        assert "9 of 37 shown" in docket.text

        canvas = client.get(f"/cases/{case_id}/canvas")
        assert canvas.status_code == 200
        assert "NCRP/2026/MH/0091001" in canvas.text


def test_fixture_ingest_replays_demo_trace_in_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRINETRA_MODE", "live")
    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_TRACE_VERIFIED", "true")

    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "io"})
        intake_token = csrf_from(client.get("/cases/new").text)
        client.post(
            "/cases/ingest",
            data={"ack_no": "NCRP/2026/MH/0091001", "csrf_token": intake_token},
        )

        started = client.post(
            "/cases/start",
            data={"ack_no": "NCRP/2026/MH/0091001", "reviewed": "yes", "csrf_token": intake_token},
            follow_redirects=False,
        )

        assert started.status_code == 303
        trace = client.get(started.headers["location"])
        assert trace.status_code == 200
        assert "NCRP/2026/MH/0091001" in trace.text
        assert "Provider error" not in trace.text
        assert "TronGrid request failed" not in trace.text
        assert "Coinsphere Global (VASP)" in trace.text


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

        manifest = client.get("/api/cases/1/evidence-manifest")
        assert manifest.status_code == 200
        manifest_data = manifest.json()
        assert manifest_data["schema"] == "trinetra.evidence_manifest/1"
        assert manifest_data["case"]["ack_no"] == "NCRP/2026/MH/0084213"
        assert manifest_data["snapshot"]["sha256"]
        assert manifest_data["snapshot"]["graph_exhibit_sha256"]
        assert manifest_data["finding"]["terminal_kind"] == "vasp_deposit"
        assert manifest_data["audit"]["chain_verified"] is True

        bundle = client.get("/api/cases/1/evidence-bundle.zip")
        assert bundle.status_code == 200
        assert bundle.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(BytesIO(bundle.content)) as archive:
            names = set(archive.namelist())
            assert {
                "manifest.json",
                "README.txt",
                "case.json",
                "trace_snapshot.json",
                "graph_exhibit.svg",
                "custody_finding.json",
            }.issubset(names)
            readme = archive.read("README.txt").decode("utf-8")
            manifest_text = archive.read("manifest.json").decode("utf-8")
        assert "No offence finding" in readme
        assert "trinetra.evidence_manifest/1" in manifest_text

        integration_status = client.get("/api/integrations/status")
        assert integration_status.status_code == 200
        status_data = integration_status.json()
        assert status_data["schema"] == "trinetra.integration_status/1"
        assert status_data["mode"] == "fixture"
        assert status_data["secrets_rendered"] is False
        assert "SESSION_SECRET" in status_data["secret_env_names"]
        assert status_data["feature_flags"]["truthful_trace_result"]["enabled"] is True
        assert status_data["feature_flags"]["live_tron_provider"]["enabled"] is False
        assert status_data["capabilities"]


def test_unsupported_trace_page_omits_demo_custody_candidate_text() -> None:
    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "io"})
        with isolated_db_session() as session:
            ts = now_ms()
            case = Case(
                ack_no="NCRP/2026/TEST/UI-UNSUPPORTED",
                category="investment fraud",
                jurisdiction="Test Cyber Cell",
                filed_ts_ms=ts,
                amount_reported_base=100,
                asset_symbol="ETH",
                asset_decimals=18,
                chain_family="EVM",
                chain_network="ethereum",
                reported_address="0x0000000000000000000000000000000000000000",
                payment_txid="1" * 64,
                payment_ts_ms=ts,
                created_ts_ms=ts,
                updated_ts_ms=ts,
            )
            session.add(case)
            session.commit()
            session.refresh(case)
            snapshot, finding = get_or_create_trace(session, case)
            assert finding is None
            snapshot_id = snapshot.id
            case_id = case.id

        page = client.get(f"/traces/{snapshot_id}")

        assert page.status_code == 200
        assert "Unsupported chain" in page.text
        assert "No custody candidate recorded" in page.text
        assert "Coinsphere Global (VASP)" not in page.text
        assert "deposit pattern + 214" not in page.text
        assert 'href="/findings/' not in page.text

        manifest = client.get(f"/api/cases/{case_id}/evidence-manifest")
        assert manifest.status_code == 200
        manifest_data = manifest.json()
        assert manifest_data["snapshot"]["id"] == snapshot_id
        assert manifest_data["finding"] is None
        assert manifest_data["notice"] is None

        bundle = client.get(f"/api/cases/{case_id}/evidence-bundle.zip")
        assert bundle.status_code == 200
        with zipfile.ZipFile(BytesIO(bundle.content)) as archive:
            names = set(archive.namelist())
            assert "manifest.json" in names
            assert "trace_snapshot.json" in names
            assert "graph_exhibit.svg" in names
            assert "custody_finding.json" not in names
            exported_manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert exported_manifest["finding"] is None


def test_sensitive_exports_require_authenticated_session() -> None:
    with TestClient(app) as client:
        for path in (
            "/integrations",
            "/api/integrations/status",
            "/api/cases/1/evidence-manifest",
            "/api/cases/1/evidence-bundle.zip",
            "/api/notices/1/sahyog-export",
            "/dispatch-tracker",
        ):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == "/login"
