from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.main as main_module
from app.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
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
        with TestClient(app) as test_client:
            test_client.post("/auth/prototype", data={"role": "io"})
            test_client.get("/docket")
            yield test_client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _static_paths(html: str) -> set[str]:
    return set(re.findall(r"""(?:href|src)=["'](/static/[^"']+)["']""", html))


def test_main_screens_use_local_resolvable_assets(client: TestClient) -> None:
    routes = [
        "/cases/new",
        "/docket",
        "/traces",
        "/cases/1/canvas?snapshot=1",
        "/findings/1",
        "/notices",
        "/risk-check",
        "/integrations",
        "/dispatch-tracker",
    ]
    all_assets: set[str] = set()
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200
        assert "Switch to dark console" not in response.text
        assert 'class="skip-link" href="#main-content"' in response.text
        assert "cdn." not in response.text.lower()
        assert "unpkg.com" not in response.text.lower()
        assert "jsdelivr" not in response.text.lower()
        all_assets.update(_static_paths(response.text))

    assert all_assets
    for path in sorted(all_assets):
        asset = client.get(path)
        assert asset.status_code == 200, path
        assert asset.content, path


def test_dispatch_visual_state_is_pending_not_success() -> None:
    app_css = Path("app/static/app.css").read_text()
    notice_css = Path("app/static/notice.css").read_text()

    green_chip_rule = next(
        rule for rule in app_css.splitlines() if ".chip.high" in rule and "green" in rule
    )
    amber_chip_rule = next(
        rule for rule in app_css.splitlines() if ".chip.medium" in rule and "amber" in rule
    )

    assert ".chip.dispatched" not in green_chip_rule
    assert ".chip.dispatched" in amber_chip_rule
    assert ".notice-state.sent i { background:#16a34a" not in notice_css
    assert ".notice-sendbar.sent { border-left-color:#16a34a" not in notice_css
    assert ".notice-state.sent i { background:#c98a12" in notice_css
    assert ".notice-sendbar.sent { border-left-color:#c98a12" in notice_css


def test_accessibility_hooks_for_canvas_and_explanation(client: TestClient) -> None:
    canvas = client.get("/cases/1/canvas?snapshot=1")
    assert canvas.status_code == 200
    assert 'aria-controls="canvas-bottom-panel-methodology"' in canvas.text
    assert 'aria-labelledby="canvas-bottom-tab-timeline"' in canvas.text
    assert 'aria-label="Fit graph to view"' in canvas.text

    trace = client.get("/traces/1")
    assert trace.status_code == 200
    assert 'aria-controls="trace-explain-content"' in trace.text
    assert 'role="tabpanel" aria-labelledby="trace-explain-tab-investigator"' in trace.text


def test_workspace_tabs_keep_scrollable_layout() -> None:
    app_css = Path("app/static/app.css").read_text()
    workspace_css = Path("app/static/workspace-shell.css").read_text()
    trace_css = Path("app/static/trace.css").read_text()
    notice_css = Path("app/static/notice.css").read_text()
    finding_css = Path("app/static/finding.css").read_text()
    canvas_css = Path("app/static/canvas.css").read_text()
    dispatch_css = Path("app/static/dispatch-tracker.css").read_text()

    assert ".content-shell { min-width: 0; min-height: 0; padding: 14px 23px 0; overflow: auto;" in app_css
    assert ".docket-page .content-shell {\n  padding: 0;\n  overflow: auto;" in app_css
    assert ".docket-screen {\n  height: auto;\n  min-height: 100%;" in app_css
    assert ".docket-table-wrap { min-height: 0; overflow: auto; }" in app_css

    assert "scrollbar-gutter: stable;" in app_css
    assert ".nav-rail nav {\n  flex: 1 1 auto;\n  min-height: 0;" in app_css
    assert ".workspace-nav-links {\n  flex: 1 1 auto;\n  min-height: 0;" in workspace_css

    assert ".trace-live {\n  min-width: 0;\n  min-height: 0;\n  overflow-x: hidden;\n  overflow-y: auto;" in trace_css
    assert ".trace-table-wrap {\n  height: auto;\n  flex: 1;\n  min-height: 0;\n  overflow: auto;" in trace_css

    assert "scrollbar-width:auto" in notice_css
    assert "scrollbar-width:auto" in finding_css
    assert "scrollbar-width: auto;" in canvas_css
    assert ".dispatch-tracker-shell {\n  min-width: 0;\n  min-height: 0;" in dispatch_css


def test_public_pages_clear_cached_workspace_context() -> None:
    app_js = Path("app/static/app.js").read_text()

    assert 'window.sessionStorage.removeItem("trinetraWorkingContext")' in app_js
    assert 'document.body?.classList.contains("authenticated-session")' in app_js
    assert "Public pages must not retain the previous officer's workspace context." in app_js


def test_supervisor_mode_has_visible_role_banner(client: TestClient) -> None:
    client.post("/auth/prototype", data={"role": "supervisor"})
    response = client.get("/docket")
    assert response.status_code == 200
    assert "Acting as ACP countersignature officer" in response.text
    assert "Draft notice status" in response.text
    assert 'data-acp-case-view="all"' in response.text
    assert 'data-acp-case-view="escalated"' in response.text
    assert "All IO cases" in response.text
    assert "Escalated only" in response.text
    assert 'data-escalated="true"' in response.text


def test_io_mode_does_not_show_acp_supervisor_queue(client: TestClient) -> None:
    response = client.get("/docket")
    assert response.status_code == 200
    assert "Draft notice status" not in response.text
    assert "Escalated only" not in response.text


def test_audit_log_page_is_read_only_and_filterable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "ts_ms": 1788258600000,
            "actor": "ACP-001",
            "action": "notice.dispatch",
            "subject": "TRINETRA-FN-2026-0001",
            "data": {"channels": ["portal"], "deadline_hours": 24},
            "prev_hash": "0" * 64,
            "row_hash": "1" * 64,
        },
        {
            "ts_ms": 1788258700000,
            "actor": "IO-001",
            "action": "session.signout",
            "subject": "session:2",
            "data": {"reason": "officer_signout"},
            "prev_hash": "1" * 64,
            "row_hash": "2" * 64,
        },
    ]

    def append_forbidden(*_args, **_kwargs):
        raise AssertionError("audit log viewing must not append a row")

    monkeypatch.setattr(main_module, "append_audit_event", append_forbidden)
    monkeypatch.setattr(main_module, "read_audit_events", lambda **_kwargs: rows)
    monkeypatch.setattr(main_module, "verify_audit_chain", lambda: True)

    response = client.get("/audit-log")
    assert response.status_code == 200
    assert "Audit log" in response.text
    assert "Viewing this page does not append a new audit row." in response.text
    assert "Verified" in response.text
    assert "notice.dispatch" in response.text
    assert "session.signout" in response.text
    assert "var/audit.jsonl" in response.text

    filtered = client.get(
        "/audit-log",
        params={"subject": "FN-2026", "action": "notice.dispatch", "actor": "ACP"},
    )
    assert filtered.status_code == 200
    assert "notice.dispatch" in filtered.text
    assert "TRINETRA-FN-2026-0001" in filtered.text
    assert "session:2" not in filtered.text


def test_risk_screen_presents_record_band_not_calibrated_score(client: TestClient) -> None:
    response = client.post(
        "/risk-check",
        data={"address": "TGh3c9PkL8Qn7MuYbxV1aZP2R6EeSsQ7hC"},
    )
    assert response.status_code == 200
    body = response.text

    assert "High evidence" in body
    assert "fixture evidence band" in body
    assert "risk score of 100" not in body.lower()
    assert "Classifier band" not in body
    assert "calibrated probability" in body

    api_response = client.post(
        "/api/risk-check",
        json={"address": "TGh3c9PkL8Qn7MuYbxV1aZP2R6EeSsQ7hC"},
    )
    assert api_response.status_code == 200
    payload = api_response.json()
    assert payload["score"] is None
    assert payload["posterior"] is None
    assert payload["probability_enabled"] is False
    assert payload["score_kind"] == "fixture_evidence_band"
