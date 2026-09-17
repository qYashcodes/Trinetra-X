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
    ]
    all_assets: set[str] = set()
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200
        assert "Switch to dark console" not in response.text
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
