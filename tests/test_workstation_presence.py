from __future__ import annotations

from collections.abc import Iterator
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.main as main_module
from app.main import app
from app.models import OfficerSession
from app.services.capabilities import capability_matrix
from app.services.feature_flags import feature_flags
from app.services.integrations import integration_status
from app.services.time import now_ms


PRESENCE_ENABLE = "TRINETRA_ENABLE_WORKSTATION_PRESENCE"
PRESENCE_REVIEW = "TRINETRA_WORKSTATION_PRESENCE_REVIEWED"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


@pytest.fixture
def presence_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[object, list[dict]]]:
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


@pytest.mark.parametrize(
    ("enable", "review", "expected"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
    ],
)
def test_presence_requires_both_server_gates(
    monkeypatch: pytest.MonkeyPatch,
    enable: bool,
    review: bool,
    expected: bool,
) -> None:
    monkeypatch.setenv(PRESENCE_ENABLE, str(enable).lower())
    monkeypatch.setenv(PRESENCE_REVIEW, str(review).lower())
    flag = feature_flags()["workstation_presence"]
    assert flag["enabled"] is expected
    capability = {row["key"]: row for row in capability_matrix()}["workstation_presence"]
    assert capability["state"] == ("enabled" if expected else "integration-tested")
    assert "identity or liveness" in capability["detail"] if expected else True


def test_default_off_omits_ui_script_and_denies_camera(
    presence_app: tuple[object, list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PRESENCE_ENABLE, raising=False)
    monkeypatch.delenv(PRESENCE_REVIEW, raising=False)
    with TestClient(app) as client:
        unauthenticated = client.post(
            "/api/session/presence",
            json={"enabled": True},
            follow_redirects=False,
        )
        assert unauthenticated.status_code == 303
        assert unauthenticated.headers["location"] == "/login"
        client.post("/auth/prototype", data={"role": "io"})
        page = client.get("/sessions")
        assert page.status_code == 200
        assert page.headers["permissions-policy"] == "camera=(), microphone=()"
        assert page.headers["content-security-policy"] == "connect-src 'self'"
        assert "data-presence-controls" not in page.text
        assert "/static/presence-detection.js" not in page.text
        missing_csrf = client.post(
            "/api/session/presence",
            json={"enabled": True},
        )
        assert missing_csrf.status_code == 403
        unavailable = client.post(
            "/api/session/presence",
            headers={"X-CSRF-Token": csrf_from(page.text)},
            json={"enabled": True},
        )
        assert unavailable.status_code == 409


def test_enabled_preference_is_csrf_protected_boolean_idempotent_and_session_scoped(
    presence_app: tuple[object, list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, audit_rows = presence_app
    monkeypatch.setenv(PRESENCE_ENABLE, "true")
    monkeypatch.setenv(PRESENCE_REVIEW, "true")
    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "io"})
        page = client.get("/sessions")
        csrf = csrf_from(page.text)
        assert page.headers["permissions-policy"] == "camera=(self), microphone=()"
        assert page.headers["content-security-policy"] == "connect-src 'self'"
        assert "data-presence-controls" in page.text
        assert 'data-presence-enabled="false"' in page.text
        assert 'data-presence-absence-ms="15000"' in page.text
        assert 'data-presence-check-interval-ms="400"' in page.text
        assert 'data-presence-resume-grace-ms="5000"' in page.text
        assert "/static/presence-detection.js" in page.text
        assert "not identity or liveness verification" in page.text
        assert "<video" not in page.text

        missing_csrf = client.post("/api/session/presence", json={"enabled": True})
        assert missing_csrf.status_code == 403
        invalid_csrf = client.post(
            "/api/session/presence",
            headers={"X-CSRF-Token": "invalid"},
            json={"enabled": True},
        )
        assert invalid_csrf.status_code == 403
        for invalid in (None, 1, "true", {}, []):
            invalid_response = client.post(
                "/api/session/presence",
                headers={"X-CSRF-Token": csrf},
                json={"enabled": invalid},
            )
            assert invalid_response.status_code == 422
        invalid_json = client.post(
            "/api/session/presence",
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
            content="not-json",
        )
        assert invalid_json.status_code == 422

        enabled = client.post(
            "/api/session/presence",
            headers={"X-CSRF-Token": csrf},
            json={"enabled": True},
        )
        assert enabled.status_code == 200
        assert enabled.json() == {"available": True, "enabled": True}
        repeated = client.post(
            "/api/session/presence",
            headers={"X-CSRF-Token": csrf},
            json={"enabled": True},
        )
        assert repeated.status_code == 200
        refreshed = client.get("/sessions")
        assert 'data-presence-enabled="true"' in refreshed.text

        preference_audits = [
            row for row in audit_rows if row["action"] == "session.presence_monitoring_changed"
        ]
        assert len(preference_audits) == 1
        assert set(preference_audits[0]["data"]) == {"session_id", "enabled"}
        assert preference_audits[0]["data"]["enabled"] is True

        disabled = client.post(
            "/api/session/presence",
            headers={"X-CSRF-Token": csrf},
            json={"enabled": False},
        )
        assert disabled.json() == {"available": True, "enabled": False}
        repeated_disabled = client.post(
            "/api/session/presence",
            headers={"X-CSRF-Token": csrf},
            json={"enabled": False},
        )
        assert repeated_disabled.status_code == 200
        preference_audits = [
            row for row in audit_rows if row["action"] == "session.presence_monitoring_changed"
        ]
        assert [row["data"]["enabled"] for row in preference_audits] == [True, False]

        client.post("/auth/prototype", data={"role": "supervisor"})
        role_switched = client.get("/sessions")
        assert 'data-presence-enabled="false"' in role_switched.text


def test_presence_preference_resets_with_session_end_flows(
    presence_app: tuple[object, list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _audit_rows = presence_app
    monkeypatch.setenv(PRESENCE_ENABLE, "true")
    monkeypatch.setenv(PRESENCE_REVIEW, "true")

    def enable_presence(client: TestClient) -> str:
        page = client.get("/sessions")
        csrf = csrf_from(page.text)
        response = client.post(
            "/api/session/presence",
            headers={"X-CSRF-Token": csrf},
            json={"enabled": True},
        )
        assert response.status_code == 200
        return csrf

    def assert_new_login_starts_disabled(client: TestClient) -> None:
        client.post("/auth/prototype", data={"role": "io"})
        assert 'data-presence-enabled="false"' in client.get("/sessions").text

    with TestClient(app) as client:
        assert_new_login_starts_disabled(client)
        csrf = enable_presence(client)
        client.post("/auth/logout", data={"csrf_token": csrf})
        assert_new_login_starts_disabled(client)

        csrf = enable_presence(client)
        client.post("/auth/logout-all", data={"csrf_token": csrf})
        assert_new_login_starts_disabled(client)

        enable_presence(client)
        with Session(engine) as session:
            current = session.exec(
                select(OfficerSession)
                .where(OfficerSession.revoked == False)  # noqa: E712
                .order_by(OfficerSession.id.desc())
            ).first()
            assert current is not None
            current.expires_ts_ms = now_ms() - 1
            session.add(current)
            session.commit()
        expired = client.get("/sessions", follow_redirects=False)
        assert expired.status_code == 303
        assert expired.headers["location"] == "/login"
        assert_new_login_starts_disabled(client)


def test_presence_integration_group_reports_gates_assets_and_secure_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PRESENCE_ENABLE, raising=False)
    monkeypatch.delenv(PRESENCE_REVIEW, raising=False)
    group = {row["key"]: row for row in integration_status()["groups"]}[
        "workstation_presence"
    ]
    items = {row["label"]: row for row in group["items"]}
    assert group["status"] == "disabled"
    assert items[PRESENCE_ENABLE]["status"] == "disabled"
    assert items[PRESENCE_REVIEW]["status"] == "approval_required"
    assert items["Pinned local runtime"]["status"] == "configured"
    assert "HTTPS or localhost" in items["Browser security context"]["detail"]
