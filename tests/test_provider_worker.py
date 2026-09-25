from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import get_type_hints

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine_bridge import TraceParams
from app.models import (
    AttributedLot,
    Case,
    FrontierItem,
    SourceCoverage,
    TraceSnapshot,
)
from app.services.evidence_store import (
    persist_provider_coverage,
    provider_evidence_scope,
)
from app.services.feature_flags import feature_flags
from app.services.live_tron_resume import defer_frontier_for_retry
from app.services.worker import SupervisedFrontierWorker, _frontier_state_counts
import app.repository as repository
import app.services.live_tron_resume as resume_module
from engine.adapters import tron
from engine.contracts import NormalizedTransferRecord


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = json.dumps(payload).encode("utf-8")

    def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, headers: dict, timeout: float) -> FakeResponse:
        self.calls.append(
            {"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout}
        )
        return self.responses.pop(0)


class NonJsonResponse(FakeResponse):
    def __init__(self, status_code: int, body: bytes) -> None:
        super().__init__(status_code, None)
        self.content = body

    def json(self) -> object:
        raise ValueError("not JSON")


@pytest.fixture
def local_tmp_dir() -> Iterator[Path]:
    root = Path.cwd() / "var" / f"test-provider-worker-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        if root.is_dir() and root.parent == Path.cwd() / "var":
            shutil.rmtree(root)


def test_known_real_public_tron_usdt_transfer_normalises_exact_base_units() -> None:
    # Public transfer supplied with its TronScan transaction reference during this project.
    raw = {
        "transaction_id": "920f2f4e90a80f0695c7f5685de700958a0f77cd23ce07fb1aaecc1108035bc8",
        "token_info": {
            "symbol": "USDT",
            "address": tron.TRON_MAINNET_USDT,
            "decimals": 6,
            "name": "Tether USD",
        },
        "block_timestamp": 1_789_337_235_000,
        "from": "TQH4FqaxJ9rxmKrfGcjiFrhMHnmdxwELbu",
        "to": "TRBnQnobToDDufLGpLnrn4k6KTBzMBDtQr",
        "type": "Transfer",
        "value": "2858661660",
    }

    event = tron.normalise(raw)

    assert event == {
        "txid": raw["transaction_id"],
        "ts_ms": 1_789_337_235_000,
        "source": raw["from"],
        "destination": raw["to"],
        "amount_base": 2_858_661_660,
        "token": "USDT",
        "decimals": 6,
        "contract": tron.TRON_MAINNET_USDT,
        "block": None,
        "event_index": None,
    }
    assert tron._event_recipient(
        {"result": {"to": "0xa6eac45133bcbab02f0bf23468edfc119f0712ad"}}
    ) == raw["to"]


def test_normalised_transfer_contract_preserves_optional_zero_and_empty_provenance() -> None:
    raw = {
        "transaction_id": "0" * 64,
        "block_timestamp": 0,
        "from": "TSource",
        "to": "TDestination",
        "value": "0",
        "block_number": 0,
        "event_index": 0,
        "token_info": {
            "symbol": "USDT",
            "address": tron.TRON_MAINNET_USDT,
            "decimals": 6,
        },
        "_retrieval_ts_ms": 0,
        "_raw_sha256": "",
        "_provider_request_ref": "",
    }

    event = tron.normalise(raw)

    assert get_type_hints(tron.normalise)["return"] is NormalizedTransferRecord
    assert event["ts_ms"] == 0
    assert event["amount_base"] == 0
    assert event["block"] == 0
    assert event["event_index"] == 0
    assert event["retrieval_ts_ms"] == 0
    assert event["raw_sha256"] == ""
    assert event["provider_request_ref"] == ""


def test_every_paginated_provider_response_is_captured_and_persisted(
    local_tmp_dir: Path,
) -> None:
    first_row = {
        "transaction_id": "a" * 64,
        "block_timestamp": 100,
        "from": "TSource",
        "to": "TDestination",
        "value": "5",
        "token_info": {"symbol": "USDT", "decimals": 6, "address": tron.TRON_MAINNET_USDT},
    }
    second_row = {**first_row, "transaction_id": "b" * 64, "block_timestamp": 200}
    http = FakeSession(
        [
            FakeResponse(
                200,
                {"success": True, "data": [first_row], "meta": {"fingerprint": "next"}},
            ),
            FakeResponse(200, {"success": True, "data": [second_row], "meta": {"at": 200}}),
        ]
    )
    config = tron.TronGridConfig(
        base_url="https://api.trongrid.io",
        api_key="provider-secret-not-for-storage",
        page_limit=200,
        max_pages=3,
    )

    with provider_evidence_scope(root=local_tmp_dir, case_id=17, snapshot_id=23) as capture:
        rows = tron.fetch_trc20_transfers(
            "TSource",
            min_timestamp=0,
            max_timestamp=500,
            contract_address=tron.TRON_MAINNET_USDT,
            session=http,
            config=config,
        )

    assert len(rows) == 2
    assert len(capture.records) == 2
    assert capture.records[0]["schema_status"] == "valid"
    assert capture.records[1]["query"]["fingerprint"] == "next"
    for record in capture.records:
        assert Path(record["raw_path"]).exists()
        assert Path(record["receipt_path"]).exists()
        assert Path(record["schema_assessment_path"]).exists()
        assert "provider-secret-not-for-storage" not in json.dumps(record)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            persisted = persist_provider_coverage(session, capture.records)
            session.commit()
            assert len(persisted) == 2
            coverage = session.exec(select(SourceCoverage).order_by(SourceCoverage.id)).all()
            assert coverage[0].query_range["query"]["min_timestamp"] == 0
            assert coverage[0].pagination_cursor == "next"
            assert coverage[1].pagination_cursor is None
            assert coverage[1].query_range["query"]["fingerprint"] == "next"
            assert coverage[1].observed_watermark["provider"]["at"] == 200
            assert all(row.completeness == "response_recorded" for row in coverage)
            assert all(row.observed_watermark["raw_sha256"] for row in coverage)
    finally:
        engine.dispose()


def test_trongrid_account_balance_parses_trc20_wallet_balance(
    local_tmp_dir: Path,
) -> None:
    http = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "address": "TWallet",
                            "trc20": [
                                {tron.TRON_MAINNET_USDT: "123456789"},
                                {"TAnotherToken": "5"},
                            ],
                        }
                    ],
                    "meta": {"at": 1_789_337_235_000},
                },
            )
        ]
    )
    config = tron.TronGridConfig(base_url="https://api.trongrid.io", api_key="test")

    with provider_evidence_scope(root=local_tmp_dir, case_id=18) as capture:
        balance = tron.fetch_trc20_balance("TWallet", session=http, config=config)

    assert balance["amount_base"] == 123_456_789
    assert balance["contract"] == tron.TRON_MAINNET_USDT
    assert balance["retrieval_ts_ms"] == capture.records[0]["retrieval_ts_ms"]
    assert http.calls[0]["url"] == "https://api.trongrid.io/v1/accounts/TWallet"
    assert http.calls[0]["params"] == {}
    assert capture.records[0]["schema_status"] == "valid"


def test_trongrid_account_balance_missing_token_is_zero(
    local_tmp_dir: Path,
) -> None:
    http = FakeSession(
        [
            FakeResponse(
                200,
                {"success": True, "data": [{"address": "TWallet", "trc20": []}]},
            )
        ]
    )
    config = tron.TronGridConfig(base_url="https://api.trongrid.io", api_key="test")

    with provider_evidence_scope(root=local_tmp_dir, case_id=18):
        balance = tron.fetch_trc20_balance("TWallet", session=http, config=config)

    assert balance["amount_base"] == 0


def test_schema_drift_keeps_raw_payload_and_records_conflict(local_tmp_dir: Path) -> None:
    http = FakeSession(
        [FakeResponse(200, {"success": True, "data": {"unexpected": "object"}})]
    )
    config = tron.TronGridConfig(base_url="https://api.trongrid.io", api_key="test")

    with provider_evidence_scope(root=local_tmp_dir, case_id=18) as capture:
        with pytest.raises(tron.ProviderSchemaDriftError, match="data field"):
            tron.fetch_trc20_transfers("TSource", session=http, config=config)

    assert len(capture.records) == 1
    record = capture.records[0]
    assert record["schema_status"] == "schema_drift"
    assert record["conflicts"][0]["kind"] == "schema_drift"
    assert json.loads(Path(record["raw_path"]).read_text())["data"] == {
        "unexpected": "object"
    }


def test_non_json_schema_drift_keeps_exact_response_bytes(local_tmp_dir: Path) -> None:
    body = b"<html><body>upstream unavailable</body></html>\x00"
    http = FakeSession([NonJsonResponse(502, body)])
    config = tron.TronGridConfig(base_url="https://api.trongrid.io", api_key="test")

    with provider_evidence_scope(root=local_tmp_dir, case_id=20) as capture:
        with pytest.raises(tron.ProviderSchemaDriftError, match="non-JSON"):
            tron.fetch_trc20_transfers("TSource", session=http, config=config)

    assert len(capture.records) == 1
    record = capture.records[0]
    assert record["schema_status"] == "schema_drift"
    assert record["raw_content_kind"] == "bytes"
    assert Path(record["raw_path"]).suffix == ".bin"
    assert Path(record["raw_path"]).read_bytes() == body


def test_repository_automatically_links_each_provider_request_to_snapshot_coverage(
    local_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    address = "TQH4FqaxJ9rxmKrfGcjiFrhMHnmdxwELbu"
    txid = "920f2f4e90a80f0695c7f5685de700958a0f77cd23ce07fb1aaecc1108035bc8"
    http = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "event_name": "Transfer",
                            "contract_address": tron.TRON_MAINNET_USDT,
                            "result": {"to": address, "value": "2858661660"},
                        }
                    ],
                },
            ),
            FakeResponse(200, {"success": True, "data": [], "meta": {"at": 1_789_337_235_000}}),
        ]
    )
    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    monkeypatch.delenv("TRINETRA_LIVE_TRON_TRACE_VERIFIED", raising=False)
    monkeypatch.setattr(tron.requests, "Session", lambda: http)
    monkeypatch.setattr(repository, "settings", SimpleNamespace(var_dir=local_tmp_dir))

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            case = Case(
                ack_no="NCRP/2026/TEST/PROVIDER-EVIDENCE",
                category="investment fraud",
                jurisdiction="Test Cyber Cell",
                filed_ts_ms=1_789_337_235_000,
                amount_reported_base=2_858_661_660,
                asset_symbol="USDT",
                asset_decimals=6,
                chain_family="TRON",
                chain_network="mainnet",
                reported_address=address,
                payment_txid=txid,
                payment_ts_ms=1_789_337_235_000,
                created_ts_ms=1,
                updated_ts_ms=1,
            )
            session.add(case)
            session.commit()
            session.refresh(case)

            snapshot, finding = repository.get_or_create_trace(
                session,
                case,
                trace_mode="live",
            )

            assert finding is None
            coverage = session.exec(
                select(SourceCoverage)
                .where(SourceCoverage.snapshot_id == snapshot.id)
                .where(SourceCoverage.provider == "trongrid-v1")
                .order_by(SourceCoverage.id)
            ).all()
            assert len(coverage) == 2
            assert coverage[0].query_range["endpoint"].endswith(f"/{txid}/events")
            assert coverage[1].query_range["endpoint"].endswith("/transactions/trc20")
            assert all(Path(row.observed_watermark["raw_path"]).exists() for row in coverage)
    finally:
        engine.dispose()


def test_rate_limit_retry_after_controls_persisted_frontier_delay(
    local_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = FakeSession(
        [
            FakeResponse(
                429,
                {"success": False, "error": "The key exceeds the frequency limit", "statusCode": 429},
                headers={"Retry-After": "7"},
            )
        ]
    )
    config = tron.TronGridConfig(base_url="https://api.trongrid.io", api_key="test")
    with provider_evidence_scope(root=local_tmp_dir, case_id=19) as capture:
        with pytest.raises(tron.ProviderResponseError) as caught:
            tron.fetch_trc20_transfers("TSource", session=http, config=config)
    assert caught.value.error_kind == "rate_limit"
    assert caught.value.retry_after_ms == 7_000
    assert capture.records[0]["schema_status"] == "provider_error"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.services.live_tron_resume.now_ms", lambda: 10_000)
    try:
        with Session(engine) as session:
            item = FrontierItem(
                case_id=1,
                snapshot_id=1,
                event_ref="rate-limit-event",
                priority_base=100,
                priority_reason="dominant_fund_flow",
                depth=1,
                state="leased",
                resume_cursor={},
                created_ts_ms=1,
                updated_ts_ms=1,
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            deferred = defer_frontier_for_retry(
                session,
                item,
                error=caught.value,
                backoff_ms=1_000,
            )
            assert deferred.resume_cursor["retry_delay_ms"] == 7_000
            assert deferred.resume_cursor["provider_retry_after_ms"] == 7_000
            assert deferred.resume_cursor["next_retry_ts_ms"] == 17_000
    finally:
        engine.dispose()


def _worker_engine() -> tuple[object, int]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        case = Case(
            ack_no="NCRP/2026/TEST/WORKER",
            category="investment fraud",
            jurisdiction="Test Cyber Cell",
            filed_ts_ms=1,
            amount_reported_base=100,
            asset_symbol="USDT",
            asset_decimals=6,
            chain_family="TRON",
            chain_network="mainnet",
            reported_address="TRoot",
            payment_txid="a" * 64,
            payment_ts_ms=1,
            created_ts_ms=1,
            updated_ts_ms=1,
        )
        session.add(case)
        session.commit()
        session.refresh(case)
        snapshot = TraceSnapshot(
            case_id=int(case.id),
            status="incomplete",
            cache_identity="worker-test",
            result_json={},
            sha256="b" * 64,
            chain_family="TRON",
            chain_network="mainnet",
            asset_symbol="USDT",
            asset_decimals=6,
            started_ts_ms=1,
        )
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        lot = AttributedLot(
            case_id=int(case.id),
            snapshot_id=snapshot.id,
            seed_event_ref="seed",
            asset_identifier=f"TRON:mainnet:USDT:{tron.TRON_MAINNET_USDT}",
            current_address="TWorkerAddress",
            remaining_amount_base=100,
            allocation_policy="dominant_fund_flow",
            allocation_version="trinetra.allocation/1",
            state="queued",
            created_ts_ms=1,
            updated_ts_ms=1,
        )
        session.add(lot)
        session.commit()
        session.refresh(lot)
        item = FrontierItem(
            case_id=int(case.id),
            snapshot_id=snapshot.id,
            lot_id=lot.id,
            event_ref="worker-root",
            priority_base=100,
            priority_reason="dominant_fund_flow",
            depth=1,
            lease_version=1,
            state="leased",
            resume_cursor={"leased_by": "dead-worker", "leased_ts_ms": 1},
            created_ts_ms=1,
            updated_ts_ms=1,
        )
        session.add(item)
        session.commit()
        return engine, int(item.id)


def test_frontier_state_counts_preserve_empty_and_mixed_states() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            assert _frontier_state_counts(session) == {
                "queued": 0,
                "leased": 0,
                "deferred": 0,
            }
            for index, state in enumerate(
                ("queued", "queued", "leased", "deferred", "deferred", "completed"),
                start=1,
            ):
                session.add(
                    FrontierItem(
                        case_id=1,
                        event_ref=f"telemetry-{index}",
                        priority_base=1,
                        priority_reason="telemetry-test",
                        depth=1,
                        state=state,
                        created_ts_ms=1,
                        updated_ts_ms=1,
                    )
                )
            session.commit()

            assert _frontier_state_counts(session) == {
                "queued": 2,
                "leased": 1,
                "deferred": 2,
            }
    finally:
        engine.dispose()


def test_supervised_worker_recovers_stale_lease_and_drains_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, item_id = _worker_engine()
    monkeypatch.setattr(tron, "fetch_trc20_transfers", lambda *_args, **_kwargs: [])
    worker = SupervisedFrontierWorker(
        engine,
        enabled=True,
        poll_interval_s=0.01,
        batch_size=1,
        lease_timeout_ms=0,
    )
    try:
        telemetry = worker.run_once(params=TraceParams(max_depth=2))
        assert telemetry["recovered_leases"] == 1
        assert telemetry["expanded_items"] == 1
        assert telemetry["queued"] == 0
        assert telemetry["leased"] == 0
        assert telemetry["failures"] == 0
        assert "api_key" not in telemetry
        assert "response" not in telemetry
        with Session(engine) as session:
            item = session.get(FrontierItem, item_id)
            assert item is not None
            assert item.state == "completed"
            assert item.lease_version == 2
            assert item.resume_cursor["lease_recovery_history"][0]["leased_by"] == "dead-worker"
    finally:
        worker.stop()
        engine.dispose()


def test_supervised_worker_persists_rate_limit_evidence_and_defers_without_leaking_key(
    local_tmp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, item_id = _worker_engine()
    http = FakeSession(
        [
            FakeResponse(
                429,
                {"success": False, "error": "The key exceeds the frequency limit", "statusCode": 429},
                headers={"Retry-After": "3"},
            )
        ]
    )
    monkeypatch.setenv("TRONGRID_API_KEY", "worker-provider-secret")
    monkeypatch.setattr(tron.requests, "Session", lambda: http)
    monkeypatch.setattr(resume_module, "settings", SimpleNamespace(var_dir=local_tmp_dir))
    worker = SupervisedFrontierWorker(
        engine,
        enabled=True,
        poll_interval_s=0.01,
        batch_size=1,
        lease_timeout_ms=0,
    )
    try:
        telemetry = worker.run_once(params=TraceParams(max_depth=2))
        assert telemetry["expanded_items"] == 0
        assert telemetry["failures"] == 1
        assert telemetry["deferred"] == 1
        assert telemetry["last_error_kind"] == "ProviderResponseError"
        assert "worker-provider-secret" not in json.dumps(telemetry)
        with Session(engine) as session:
            item = session.get(FrontierItem, item_id)
            assert item is not None
            assert item.state == "deferred"
            assert item.deferral_reason == "provider_backoff"
            coverage = session.exec(
                select(SourceCoverage).where(SourceCoverage.case_id == item.case_id)
            ).all()
            provider_rows = [row for row in coverage if row.provider == "trongrid-v1"]
            assert len(provider_rows) == 1
            assert provider_rows[0].gaps == [
                {"reason": "provider_http_error", "status_code": 429}
            ]
            assert Path(provider_rows[0].observed_watermark["raw_path"]).exists()
    finally:
        worker.stop()
        engine.dispose()


def test_supervised_worker_requires_its_own_flag_and_all_live_trace_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = (
        "TRINETRA_ENABLE_FRONTIER_WORKER",
        "TRINETRA_ENABLE_LIVE_TRON",
        "TRONGRID_API_KEY",
        "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED",
        "TRINETRA_LIVE_TRON_SMOKE_VERIFIED",
        "TRINETRA_LIVE_TRON_TRACE_VERIFIED",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert feature_flags()["supervised_frontier_worker"]["enabled"] is False

    monkeypatch.setenv("TRINETRA_ENABLE_FRONTIER_WORKER", "true")
    assert feature_flags()["supervised_frontier_worker"]["enabled"] is False

    for name in names[1:]:
        monkeypatch.setenv(name, "test-key" if name == "TRONGRID_API_KEY" else "true")
    assert feature_flags()["supervised_frontier_worker"]["enabled"] is True
