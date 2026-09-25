from __future__ import annotations

from collections.abc import Iterator
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.main as main_module
from app.main import app
from app.services.demo import demo_case
from app.services.money import format_amount
from app.services.time import format_ist


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


@pytest.fixture
def workflow_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
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
        with TestClient(app) as client:
            client.post("/auth/prototype", data={"role": "io"})
            client.get("/docket")
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_workflow_screens_render_canonical_case_evidence(workflow_client: TestClient) -> None:
    data = demo_case()
    hops = data["dominant_path"]
    case = data["case"]
    terminal = data["terminal"]

    trace = workflow_client.get("/traces/1").text
    canvas = workflow_client.get("/cases/1/canvas?snapshot=1").text
    finding = workflow_client.get("/findings/1").text

    for page in (trace, canvas, finding):
        assert case["ack_no"] in page

    for hop in hops:
        assert hop["address"] in trace
        assert format_amount(hop["value_base"]).split()[0] in trace
        assert format_ist(hop["ts_ms"])[11:19] in trace
        assert hop["address"] in canvas
        assert format_amount(hop["value_base"]) in canvas
        assert hop["address"] in finding

    assert case["reported_address"] in trace
    assert 'data-final-duration-seconds="10"' in trace
    assert f'data-address="{terminal["deposit_address"]}"' in finding
    assert f'data-deposit-address="{terminal["deposit_address"]}"' in canvas
    assert f'data-hot-wallet-address="{terminal["hot_wallet"]}"' in canvas
    assert f"Hop {hops[-1]['hop']} of the recorded trace" in finding
    assert "<span>Custody hop</span><b class=\"mono\">H4</b>" in finding
    assert format_amount(terminal["amount_credited_base"]) in finding

    review_keys = [item["key"] for item in data["finding_review_checks"]]
    prepared = workflow_client.post(
        "/findings/1/checks",
        data={"review_check": review_keys, "action": "prepare", "csrf_token": csrf_from(finding)},
        follow_redirects=False,
    )
    assert prepared.status_code == 303
    notice = workflow_client.get("/notices/1").text
    assert terminal["deposit_address"] in notice
    assert case["payment_txid"] in notice
    assert format_amount(case["amount_reported_base"]) in notice
    assert format_amount(terminal["amount_credited_base"]) in notice
    assert format_ist(case["victim_payment_ts_ms"]) in notice
    assert format_ist(hops[-1]["ts_ms"]) in notice

    stale_identifiers = (
        "TNq7hVb4Ld9sMe2XcQ1yRf8KpZ3aWu6Kd21",
        "TWx2mR8pQe4nZ7bVc1aYt5KdJ9sLu3Fm77",
        "TKp5nD2wYs8aXe3RcM7bQv1LtZ6uJf9Hg04",
        "TBd9sQ4uLk1mPe7XvN2cRt8ZaYj5Wf3Cx56",
        "TGh3vB6nJq9sWe2XdL5cRm8KpZ1aYt7Nu83",
    )
    rendered = trace + canvas + finding + notice
    assert not any(identifier in rendered for identifier in stale_identifiers)
