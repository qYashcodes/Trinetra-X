from __future__ import annotations

from collections.abc import Iterator
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
