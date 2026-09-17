from __future__ import annotations

from collections.abc import Iterator
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.main as main_module
from app.main import app
from app.models import OfficerRole, OfficerSession
from app.services.sessions import (
    create_officer_session,
    record_session_activity,
    resolve_officer_session,
    session_token_sha256,
    signout_summary,
)
from app.services.time import now_ms
from app.settings import settings


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


@pytest.fixture
def session_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[object, list[dict]]]:
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
        row = {
            "ts_ms": now_ms(),
            "actor": actor,
            "action": action,
            "subject": subject,
            "data": data or {},
            "row_hash": f"audit-{len(audit_rows) + 1}",
        }
        audit_rows.append(row)
        return row

    app.dependency_overrides[main_module.get_session] = session_override
    monkeypatch.setattr(main_module, "append_audit_event", audit_override)
    try:
        yield engine, audit_rows
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_live_mode_requires_an_explicit_session_secret() -> None:
    assert settings.session_secret != "dev-only-change-me"
    main_module.require_session_secret("fixture", None)
    main_module.require_session_secret("live", "configured-secret")
    with pytest.raises(RuntimeError, match="SESSION_SECRET is required"):
        main_module.require_session_secret("live", None)


def test_server_session_hashes_token_tracks_activity_and_expires() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    officer = {
        "name": "Insp. Test Officer",
        "rank": "Inspector",
        "pis": "TEST-1",
        "desk": "Test Cyber Cell",
    }
    try:
        with Session(engine) as session:
            row, raw_token = create_officer_session(
                session,
                officer,
                OfficerRole.io,
                "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0",
                created_ts_ms=1_000,
            )
            assert row.token_sha256 == session_token_sha256(raw_token)
            assert raw_token not in row.model_dump().values()
            assert row.device_label == "Chrome on Windows"

            resolved = resolve_officer_session(
                session,
                raw_token,
                current_ts_ms=2_000,
            )
            assert resolved is not None
            record_session_activity(
                session,
                resolved,
                case_id=7,
                trace_run=True,
                artifact_export=True,
            )
            record_session_activity(session, resolved, case_id=7)
            assert resolved.cases_touched == [7]
            assert resolved.traces_run == 1
            assert resolved.artifacts_exported == 1

            expired = resolve_officer_session(
                session,
                raw_token,
                current_ts_ms=resolved.expires_ts_ms,
            )
            assert expired is None
            session.refresh(resolved)
            assert resolved.revoked is True
            assert resolved.end_reason == "idle_timeout"
            assert signout_summary(resolved)["cases_touched"] == [7]
    finally:
        engine.dispose()


def test_export_identity_and_audited_signout(session_app) -> None:
    engine, audit_rows = session_app
    with TestClient(app) as client:
        login = client.post("/auth/prototype", data={"role": "io"}, follow_redirects=False)
        assert login.status_code == 303
        docket = client.get("/docket")
        assert "Demonstration session" in docket.text
        assert "Insp. R. Kulkarni" in docket.text
        assert 'action="/auth/logout"' in docket.text

        manifest = client.get("/api/cases/1/evidence-manifest")
        assert manifest.status_code == 200
        artifact = manifest.json()["artifact"]
        assert artifact["generated_by"] == {
            "name": "Insp. R. Kulkarni",
            "service_id": "74821",
            "unit": "Cyber Cell, Pune City",
            "role": "io",
        }
        assert artifact["artifact_posture"] == "demonstration_generated"
        assert artifact["audit_chain_reference"]["action"] == "artifact.export_manifest"
        assert artifact["audit_chain_reference"]["row_hash"]

        sessions_page = client.get("/sessions")
        assert sessions_page.status_code == 200
        assert "Chrome" not in sessions_page.text
        assert "Current" in sessions_page.text
        rejected = client.post("/auth/logout", data={}, follow_redirects=False)
        assert rejected.status_code == 403
        token = csrf_from(sessions_page.text)
        logout = client.post(
            "/auth/logout",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert logout.status_code == 303
        assert logout.headers["location"] == "/login"

    with Session(engine) as session:
        row = session.exec(select(OfficerSession)).one()
        assert row.revoked is True
        assert row.end_reason == "officer_signout"
        assert row.cases_touched == [1]
        assert row.artifacts_exported == 1
        assert row.traces_run == 0

    signout = next(row for row in audit_rows if row["action"] == "session.signout")
    assert signout["data"]["cases_touched"] == [1]
    assert signout["data"]["artifacts_exported"] == 1
    assert signout["data"]["duration_ms"] >= 0


def test_identity_shells_and_security_gaps_remain_visible(session_app) -> None:
    with TestClient(app) as client:
        login = client.get("/login")
        assert "Demonstration prototype" in login.text
        assert login.text.count("Integration pending") == 2
        assert "Demonstration account" in login.text

        client.post("/auth/prototype", data={"role": "io"})
        status = client.get("/api/integrations/status").json()
        capabilities = {row["key"]: row for row in status["capabilities"]}
        assert capabilities["session_lifecycle"]["state"] == "integration-tested"
        assert capabilities["government_identity"]["state"] == "unavailable"

        groups = {row["key"]: row for row in status["groups"]}
        assert groups["identity"]["status"] == "disabled"
        security = {row["label"]: row for row in groups["session_security"]["items"]}
        assert security["Transport security"]["status"] == "disabled"
        assert security["Secret vault"]["status"] == "disabled"
        assert security["Tenant authorisation boundary"]["status"] == "disabled"
        assert security["Monitoring and backup"]["status"] == "disabled"
        assert status["secrets_rendered"] is False


def test_signout_all_devices_revokes_every_server_session(session_app) -> None:
    engine, audit_rows = session_app
    with TestClient(app) as first, TestClient(app) as second:
        first.post("/auth/prototype", data={"role": "io"})
        second.post("/auth/prototype", data={"role": "io"})

        page = first.get("/sessions")
        assert "2 active sessions" in page.text
        response = first.post(
            "/auth/logout-all",
            data={"csrf_token": csrf_from(page.text)},
            follow_redirects=False,
        )
        assert response.status_code == 303

        denied = second.get("/docket", follow_redirects=False)
        assert denied.status_code == 303
        assert denied.headers["location"] == "/login"

    with Session(engine) as session:
        rows = session.exec(select(OfficerSession).order_by(OfficerSession.id)).all()
        assert len(rows) == 2
        assert all(row.revoked for row in rows)
        assert {row.end_reason for row in rows} == {"officer_signout_all"}
    assert any(row["action"] == "session.signout_all" for row in audit_rows)


def test_expired_server_session_fails_closed(session_app) -> None:
    engine, _audit_rows = session_app
    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "supervisor"})
        with Session(engine) as session:
            row = session.exec(select(OfficerSession)).one()
            row.expires_ts_ms = now_ms() - 1
            session.add(row)
            session.commit()

        response = client.get("/docket", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    with Session(engine) as session:
        row = session.exec(select(OfficerSession)).one()
        assert row.revoked is True
        assert row.end_reason == "idle_timeout"
