from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.main as main_module
from app.main import app
from app.models import Case
from app.services.behavior import assign_evidence_band, band_methodology, extract_behavioral_features
from app.services.evidence_store import capture_provider_payload, mark_provider_schema, provider_evidence_scope
from app.services.risk import live_risk_check
from app.services.time import now_ms
from engine.adapters import tron


ADDRESS = "TQH4FqaxJ9rxmKrfGcjiFrhMHnmdxwELbu"
SOURCE_A = "TVX5Dc7X2wsTwY7GmH4cZgXZyTBMmE43zb"
SOURCE_B = "TSender22222222222222222222222222222"
DESTINATION_A = "TRBnQnobToDDufLGpLnrn4k6KTBzMBDtQr"
DESTINATION_B = "TU4vScqBRHoVeC8Jn1ooDn2Db4Yv7y7Pvaa"
BASE_TS = 1_789_337_235_000


def _transfer(
    index: int,
    *,
    source: str,
    destination: str,
    amount_base: int,
    ts_ms: int | None = None,
) -> dict:
    return {
        "txid": f"{index:064x}",
        "event_index": 0,
        "source": source,
        "destination": destination,
        "amount_base": amount_base,
        "ts_ms": BASE_TS + index * 1_000 if ts_ms is None else ts_ms,
    }


def _rapid_pass_through_history() -> list[dict]:
    return [
        _transfer(1, source=SOURCE_A, destination=ADDRESS, amount_base=100_000_000),
        _transfer(2, source=ADDRESS, destination=DESTINATION_A, amount_base=90_000_000),
        _transfer(3, source=SOURCE_B, destination=ADDRESS, amount_base=100_000_000),
        _transfer(4, source=ADDRESS, destination=DESTINATION_B, amount_base=90_000_000),
        _transfer(5, source=SOURCE_A, destination=ADDRESS, amount_base=100_000_000),
        _transfer(6, source=ADDRESS, destination=DESTINATION_A, amount_base=90_000_000),
    ]


def _raw_history() -> list[dict]:
    return [
        {
            "transaction_id": row["txid"],
            "event_index": row["event_index"],
            "block_timestamp": row["ts_ms"],
            "from": row["source"],
            "to": row["destination"],
            "value": str(row["amount_base"]),
            "token_info": {
                "symbol": "USDT",
                "decimals": 6,
                "address": tron.TRON_MAINNET_USDT,
            },
        }
        for row in _rapid_pass_through_history()
    ]


@pytest.fixture
def risk_state_dir() -> Iterator[Path]:
    root = Path.cwd() / "var" / f"test-risk-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        if root.is_dir() and root.parent == Path.cwd() / "var":
            shutil.rmtree(root)


def test_sparse_four_event_history_is_unknown() -> None:
    features = extract_behavioral_features(ADDRESS, _rapid_pass_through_history()[:4])
    result = assign_evidence_band(features)

    assert features["observed_event_count"] == 4
    assert features["sufficient_for_band"] is False
    assert result["band"] == "unknown"
    assert "At least 5 observed transfers" in result["insufficiency_reasons"][0]


def test_transparent_features_precede_documented_band_and_retain_evidence_refs() -> None:
    features = extract_behavioral_features(ADDRESS, _rapid_pass_through_history())
    result = assign_evidence_band(features)
    by_key = {item["key"]: item for item in features["items"]}

    assert result["band"] == "medium"
    assert result["schema"] == "trinetra.evidence_band_rules/1"
    assert "rapid_pass_through" in result["matched_rules"]
    assert by_key["forward_ratio"]["values"]["outbound_to_inbound_bp"] == 9000
    assert by_key["resting_balance"]["values"]["retained_base"] == 30_000_000
    assert by_key["sweep_timing"]["values"]["median_hold_ms"] == 1000
    assert by_key["pass_through_pattern"]["evidence_refs"]
    assert all(
        reference.startswith("tron:")
        for feature in features["items"]
        for reference in feature["evidence_refs"]
    )


def test_zero_value_event_is_preserved_in_raw_feature_evidence() -> None:
    history = _rapid_pass_through_history()
    history.append(
        _transfer(
            7,
            source=SOURCE_B,
            destination=ADDRESS,
            amount_base=0,
            ts_ms=0,
        )
    )
    features = extract_behavioral_features(ADDRESS, history)
    lifetime = next(item for item in features["items"] if item["key"] == "address_lifetime")

    assert features["observed_event_count"] == 7
    assert lifetime["values"]["first_seen_ts_ms"] == 0
    assert "tron:" + f"{7:064x}" + ":0" in lifetime["evidence_refs"]


def test_short_lived_address_does_not_enable_burner_classification() -> None:
    history = [
        _transfer(1, source=SOURCE_A, destination=ADDRESS, amount_base=100_000_000),
        _transfer(2, source=SOURCE_B, destination=ADDRESS, amount_base=100_000_000),
        _transfer(3, source=ADDRESS, destination=DESTINATION_A, amount_base=40_000_000),
        _transfer(4, source=ADDRESS, destination=DESTINATION_B, amount_base=40_000_000),
        _transfer(5, source=SOURCE_A, destination=ADDRESS, amount_base=100_000_000),
        _transfer(6, source=ADDRESS, destination=DESTINATION_A, amount_base=40_000_000),
    ]
    result = assign_evidence_band(extract_behavioral_features(ADDRESS, history))
    methodology = band_methodology()

    assert result["band"] == "low"
    assert not any("burner" in item for item in result["observed_classes"])
    assert methodology["burner_classification"]["status"] == "disabled"
    assert "false-positive benchmark" in methodology["burner_classification"]["reason"]


def test_case_less_provider_evidence_is_retained_under_lookup_reference(
    risk_state_dir: Path,
) -> None:
    with provider_evidence_scope(root=risk_state_dir, lookup_ref="risk-lookup-17") as capture:
        receipt = capture_provider_payload(
            provider="trongrid-v1",
            endpoint="/v1/accounts/T/transactions/trc20",
            query={"limit": 1},
            status_code=200,
            payload={"data": []},
            retrieval_ts_ms=BASE_TS,
        )
        mark_provider_schema(receipt, status="valid")

    assert receipt is not None
    assert receipt["case_id"] is None
    assert receipt["lookup_ref"] == "risk-lookup-17"
    assert Path(receipt["raw_path"]).is_file()
    assert "lookup-risk-lookup-17" in receipt["raw_path"]
    assert receipt["schema_status"] == "valid"


def test_live_provider_failure_is_unknown_without_fixture_fallback(
    monkeypatch: pytest.MonkeyPatch,
    risk_state_dir: Path,
) -> None:
    def fail_fetch(*_args, **_kwargs):
        raise tron.ProviderResponseError("blocked", error_kind="network_error")

    monkeypatch.setattr(tron, "fetch_trc20_transfers", fail_fetch)
    result = live_risk_check(ADDRESS, evidence_root=risk_state_dir)

    assert result["mode"] == "live"
    assert result["band"] == "unknown"
    assert result["attribution"] is None
    assert result["score"] is None
    assert result["posterior"] is None
    assert result["probability_enabled"] is False
    assert result["live_enrichment"] == "provider_unavailable"
    assert "Coinsphere" not in str(result)
    assert Path(result["result_record"]["path"]).is_file()


def test_live_risk_result_has_raw_features_and_separate_source_lines(
    monkeypatch: pytest.MonkeyPatch,
    risk_state_dir: Path,
) -> None:
    monkeypatch.setattr(tron, "fetch_trc20_transfers", lambda *_args, **_kwargs: _raw_history())
    result = live_risk_check(ADDRESS, evidence_root=risk_state_dir)

    assert result["band"] == "medium"
    assert len(result["features"]) == 10
    assert result["calibration_status"] == "disabled_pending_independent_labelled_data"
    assert result["score"] is None
    assert result["posterior"] is None
    assert result["probability_enabled"] is False
    assert Path(result["result_record"]["path"]).is_file()
    sources = {row["source"] for row in result["attribution_evidence"]}
    assert "TRINETRA local case records" in sources
    assert "reviewed VASP registry" in sources
    assert "sanctions sources" in sources
    assert "reported-abuse sources" in sources


@pytest.fixture
def risk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, object, list[dict]]]:
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
            "row_hash": f"risk-audit-{len(audit_rows) + 1}",
        }
        audit_rows.append(row)
        return row

    for name, value in {
        "TRINETRA_ENABLE_LIVE_TRON": "true",
        "TRONGRID_API_KEY": "test-key",
        "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED": "true",
        "TRINETRA_LIVE_TRON_SMOKE_VERIFIED": "true",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(tron, "fetch_trc20_transfers", lambda *_args, **_kwargs: _raw_history())
    monkeypatch.setattr(main_module, "append_audit_event", audit_override)
    monkeypatch.setattr(main_module, "read_audit_events", lambda **_kwargs: [])
    app.dependency_overrides[main_module.get_session] = session_override
    try:
        with TestClient(app) as client:
            client.post("/auth/prototype", data={"role": "io"})
            yield client, engine, audit_rows
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_live_risk_routes_show_features_audit_and_prepare_case_intake(
    risk_client: tuple[TestClient, object, list[dict]],
) -> None:
    client, engine, audit_rows = risk_client
    response = client.post("/risk-check", data={"address": ADDRESS, "mode": "live"})

    assert response.status_code == 200
    assert "LIVE PROVIDER LOOKUP" in response.text
    assert "Raw behavioural features" in response.text
    assert "Pass-through pattern" in response.text
    assert "disabled pending independent labelled data" in response.text
    assert "reported-abuse sources" in response.text
    assert "No case has been created by this check" in response.text
    assert "Burner classification" in response.text
    check = next(row for row in audit_rows if row["action"] == "risk.check")
    assert check["subject"] == ADDRESS
    assert check["data"]["mode"] == "live"
    assert check["data"]["band"] == "medium"
    assert check["data"]["observed_event_count"] == 6
    assert check["data"]["calibration_status"] == "disabled_pending_independent_labelled_data"

    api = client.post("/api/risk-check", json={"address": ADDRESS, "mode": "live"})
    assert api.status_code == 200
    assert api.json()["feature_set"]["observed_event_count"] == 6

    token_match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert token_match
    prepared = client.post(
        "/risk-check/escalate",
        data={"address": ADDRESS, "mode": "live", "csrf_token": token_match.group(1)},
        follow_redirects=False,
    )
    assert prepared.status_code == 303
    assert prepared.headers["location"] == "/cases/live/new"
    intake = client.get("/cases/live/new")
    assert f'value="{ADDRESS}"' in intake.text
    with Session(engine) as session:
        assert session.exec(select(Case).where(Case.reported_address == ADDRESS)).all() == []
    prepared_audit = next(
        row for row in audit_rows if row["action"] == "risk.prepare_case_intake"
    )
    assert prepared_audit["data"]["case_created"] is False
