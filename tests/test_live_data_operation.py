from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.main as main_module
import app.repository as repository
import app.services.live_tron_resume as resume_module
from app.engine_bridge import AssetRef, ChainRef, TraceParams, TraceSeed, run_trace
from app.main import app, evidence_manifest
from app.models import CanonicalTraceEvent, Case, Finding, FrontierItem, TraceEvent, TraceSnapshot
from app.repository import get_or_create_trace
from app.services.frontier import lease_frontier_batch
from app.services.live_trace import (
    HEURISTIC_DISCLAIMER,
    parse_amount_base,
    parse_trace_params,
    snapshot_runtime_status,
)
from app.services.live_tron_resume import (
    expand_live_tron_frontier_item,
    release_due_frontier_retries,
)
from app.services.time import now_ms
from engine.adapters import tron


SEED_ADDRESS = "TQH4FqaxJ9rxmKrfGcjiFrhMHnmdxwELbu"
NEXT_ADDRESS = "TRBnQnobToDDufLGpLnrn4k6KTBzMBDtQr"
SEED_TXID = "9" * 64
SEED_TS = 1_789_337_235_000
SEED_AMOUNT = 1_000_000


def _enable_live(monkeypatch: pytest.MonkeyPatch, *, worker: bool = False) -> None:
    values = {
        "TRINETRA_ENABLE_LIVE_TRON": "true",
        "TRONGRID_API_KEY": "test-key",
        "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED": "true",
        "TRINETRA_LIVE_TRON_SMOKE_VERIFIED": "true",
        "TRINETRA_LIVE_TRON_TRACE_VERIFIED": "true",
    }
    if worker:
        values["TRINETRA_ENABLE_FRONTIER_WORKER"] = "true"
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _raw_transfer(
    *,
    txid: str,
    ts_ms: int,
    source: str,
    destination: str,
    amount_base: int,
) -> dict:
    return {
        "transaction_id": txid,
        "block_timestamp": ts_ms,
        "from": source,
        "to": destination,
        "value": str(amount_base),
        "event_index": 0,
        "token_info": {
            "symbol": "USDT",
            "decimals": 6,
            "address": tron.TRON_MAINNET_USDT,
        },
    }


def _seed_case(*, payment_txid: str | None = SEED_TXID, payment_ts_ms: int | None = SEED_TS) -> Case:
    return Case(
        ack_no=f"LIVE/TEST/{uuid.uuid4().hex}",
        category="live blockchain trace",
        jurisdiction="Test Cyber Cell",
        filed_ts_ms=SEED_TS,
        amount_reported_base=SEED_AMOUNT,
        asset_symbol="USDT",
        asset_decimals=6,
        chain_family="TRON",
        chain_network="mainnet",
        reported_address=SEED_ADDRESS,
        payment_txid=payment_txid,
        payment_ts_ms=payment_ts_ms,
        created_ts_ms=SEED_TS,
        updated_ts_ms=SEED_TS,
    )


@pytest.fixture
def local_tmp_dir() -> Iterator[Path]:
    root = Path.cwd() / "var" / f"test-live-operation-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        if root.is_dir() and root.parent == Path.cwd() / "var":
            shutil.rmtree(root)


@contextmanager
def memory_session() -> Iterator[tuple[object, Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield engine, session
    finally:
        engine.dispose()


def test_address_seed_resolves_an_exact_confirmed_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live(monkeypatch)
    calls: list[dict] = []
    seed_row = _raw_transfer(
        txid=SEED_TXID,
        ts_ms=SEED_TS,
        source="TSender11111111111111111111111111111",
        destination=SEED_ADDRESS,
        amount_base=SEED_AMOUNT,
    )
    outgoing = _raw_transfer(
        txid="a" * 64,
        ts_ms=SEED_TS + 1_000,
        source=SEED_ADDRESS,
        destination=NEXT_ADDRESS,
        amount_base=800_000,
    )

    def fake_fetch(address: str, **kwargs):
        calls.append({"address": address, **kwargs})
        return [seed_row] if kwargs.get("only_to") else [seed_row, outgoing]

    monkeypatch.setattr(tron, "fetch_trc20_transfers", fake_fetch)
    monkeypatch.setattr(tron, "verify_seed_transfer", lambda **_kwargs: {"event_name": "Transfer"})

    result = run_trace(
        TraceSeed(
            address=SEED_ADDRESS,
            payment_ts_ms=SEED_TS,
            amount_base=SEED_AMOUNT,
            chain=ChainRef("TRON", "mainnet"),
            asset=AssetRef("USDT", tron.TRON_MAINNET_USDT, 6),
        ),
        TraceParams(max_depth=1, trace_mode="live"),
    )

    assert result["seed_match"]["resolution"] == "address_tuple"
    assert result["seed_match"]["txid"] == SEED_TXID
    assert result["seed_match"]["ts_ms"] == SEED_TS
    assert result["case"]["payment_txid"] == SEED_TXID
    assert result["hops"][0]["address"] == NEXT_ADDRESS
    assert calls[0]["only_to"] is True
    assert calls[0]["min_timestamp"] == SEED_TS
    assert calls[1]["max_timestamp"] == SEED_TS + 8 * 60 * 60 * 1000


def test_txid_seed_derives_chronology_from_confirmed_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live(monkeypatch)
    seed_row = _raw_transfer(
        txid=SEED_TXID,
        ts_ms=SEED_TS,
        source="TSender11111111111111111111111111111",
        destination=SEED_ADDRESS,
        amount_base=SEED_AMOUNT,
    )
    calls: list[dict] = []
    monkeypatch.setattr(tron, "verify_seed_transfer", lambda **_kwargs: {"event_name": "Transfer"})
    monkeypatch.setattr(
        tron,
        "fetch_trc20_transfers",
        lambda address, **kwargs: calls.append({"address": address, **kwargs}) or [seed_row],
    )

    result = run_trace(
        TraceSeed(
            address=SEED_ADDRESS,
            payment_ts_ms=None,
            amount_base=SEED_AMOUNT,
            payment_txid=SEED_TXID,
            chain=ChainRef("TRON", "mainnet"),
            asset=AssetRef("USDT", tron.TRON_MAINNET_USDT, 6),
        ),
        TraceParams(max_depth=1, trace_mode="live"),
    )

    assert result["seed_match"]["resolution"] == "transaction_hash"
    assert result["started_ts"] == SEED_TS
    assert result["case"]["payment_ts"] == SEED_TS
    assert calls[0]["order_by"] == "block_timestamp,desc"


def test_seed_provider_network_failure_preserves_typed_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live(monkeypatch)
    monkeypatch.setattr(
        tron,
        "verify_seed_transfer",
        lambda **_kwargs: (_ for _ in ()).throw(
            tron.ProviderResponseError(
                "network interrupted",
                error_kind="network_error",
            )
        ),
    )

    result = run_trace(
        TraceSeed(
            address=SEED_ADDRESS,
            payment_ts_ms=SEED_TS,
            amount_base=SEED_AMOUNT,
            payment_txid=SEED_TXID,
            chain=ChainRef("TRON", "mainnet"),
            asset=AssetRef("USDT", tron.TRON_MAINNET_USDT, 6),
        ),
        TraceParams(max_depth=1, trace_mode="live"),
    )

    assert result["terminal"]["kind"] == "provider_error"
    assert result["terminal"]["reason"] == "network_error"
    assert result["outcome"]["creates_finding"] is False


def test_unconfirmed_channel_never_enters_canonical_attribution(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_dir: Path,
) -> None:
    _enable_live(monkeypatch)
    seed_row = _raw_transfer(
        txid=SEED_TXID,
        ts_ms=SEED_TS,
        source="TSender11111111111111111111111111111",
        destination=SEED_ADDRESS,
        amount_base=SEED_AMOUNT,
    )
    outgoing = _raw_transfer(
        txid="b" * 64,
        ts_ms=SEED_TS + 1_000,
        source=SEED_ADDRESS,
        destination=NEXT_ADDRESS,
        amount_base=500_000,
    )
    unconfirmed_txid = "c" * 64
    unconfirmed = _raw_transfer(
        txid=unconfirmed_txid,
        ts_ms=SEED_TS + 2_000,
        source=SEED_ADDRESS,
        destination="TUnconfirmed111111111111111111111111",
        amount_base=400_000,
    )
    monkeypatch.setattr(tron, "verify_seed_transfer", lambda **_kwargs: {"event_name": "Transfer"})
    monkeypatch.setattr(tron, "fetch_trc20_transfers", lambda *_args, **_kwargs: [seed_row, outgoing])
    monkeypatch.setattr(
        tron,
        "fetch_unconfirmed_trc20_transfers",
        lambda *_args, **_kwargs: [unconfirmed],
    )
    monkeypatch.setattr(repository, "settings", SimpleNamespace(var_dir=local_tmp_dir, data_stale_after_seconds=900))

    with memory_session() as (_engine, session):
        case = _seed_case()
        session.add(case)
        session.commit()
        session.refresh(case)
        snapshot, finding = get_or_create_trace(
            session,
            case,
            trace_mode="live",
            params=TraceParams(max_depth=1, trace_mode="live", include_unconfirmed=True),
        )
        canonical_txids = {
            row.txid
            for row in session.exec(select(CanonicalTraceEvent)).all()
        }
        event_types = [row.event_type for row in session.exec(select(TraceEvent)).all()]

        assert finding is None
        assert snapshot.result_json["unconfirmed_observations"][0]["txid"] == unconfirmed_txid
        assert unconfirmed_txid not in canonical_txids
        assert "unconfirmed" in event_types
        manifest = evidence_manifest(case, snapshot, None, None, [])
        assert manifest["snapshot"]["unconfirmed_observations"]["count"] == 1
        assert "excluded from attribution" in manifest["snapshot"]["unconfirmed_observations"]["warning"]


def test_mid_trace_provider_failure_persists_countdown_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp_dir: Path,
) -> None:
    _enable_live(monkeypatch)
    seed_row = _raw_transfer(
        txid=SEED_TXID,
        ts_ms=SEED_TS,
        source="TSender11111111111111111111111111111",
        destination=SEED_ADDRESS,
        amount_base=SEED_AMOUNT,
    )
    outgoing = _raw_transfer(
        txid="d" * 64,
        ts_ms=SEED_TS + 1_000,
        source=SEED_ADDRESS,
        destination=NEXT_ADDRESS,
        amount_base=900_000,
    )

    def interrupted_fetch(address: str, **_kwargs):
        if address == SEED_ADDRESS:
            return [seed_row, outgoing]
        raise tron.ProviderResponseError(
            "network interrupted",
            error_kind="network_error",
            retry_after_ms=2_000,
        )

    monkeypatch.setattr(tron, "verify_seed_transfer", lambda **_kwargs: {"event_name": "Transfer"})
    monkeypatch.setattr(tron, "fetch_trc20_transfers", interrupted_fetch)
    monkeypatch.setattr(repository, "settings", SimpleNamespace(var_dir=local_tmp_dir, data_stale_after_seconds=900))
    monkeypatch.setattr(resume_module, "settings", SimpleNamespace(var_dir=local_tmp_dir))

    with memory_session() as (_engine, session):
        case = _seed_case()
        session.add(case)
        session.commit()
        session.refresh(case)
        snapshot, _finding = get_or_create_trace(
            session,
            case,
            trace_mode="live",
            params=TraceParams(max_depth=3, trace_mode="live"),
        )
        item = session.exec(select(FrontierItem)).one()
        status = snapshot_runtime_status(session, snapshot)

        assert snapshot.result_json["hops"][0]["address"] == NEXT_ADDRESS
        assert snapshot.result_json["hops"][0]["frontier_state"] == "deferred"
        assert item.state == "deferred"
        assert item.deferral_reason == "provider_backoff"
        assert item.resume_cursor["provider"] == "trongrid"
        assert item.resume_cursor["next_retry_ts_ms"] > now_ms()
        assert status["provider_backoff"][0]["next_retry_ts_ms"] == item.resume_cursor["next_retry_ts_ms"]
        assert status["provider_backoff"][0]["provider"] == "trongrid"
        assert status["stop_summary"]["primary_reason"] == "provider_backoff"
        assert status["stop_summary"]["label"] == "Provider retry pending"
        assert status["provider_evidence"]["canonical_event_count"] >= 1
        assert status["behavioral_projection"]["disclaimer"] == HEURISTIC_DISCLAIMER
        assert status["behavioral_projection"]["band"] == "unknown"

        monkeypatch.setattr(tron, "fetch_trc20_transfers", lambda *_args, **_kwargs: [])
        released = release_due_frontier_retries(
            session,
            case_id=int(case.id),
            now_ts_ms=int(item.resume_cursor["next_retry_ts_ms"]),
        )
        assert len(released) == 1
        leased = lease_frontier_batch(session, case_id=int(case.id), worker_id="resume-test", limit=1)
        assert len(leased) == 1
        expand_live_tron_frontier_item(session, leased[0])
        session.refresh(leased[0])
        assert leased[0].state == "completed"


def test_force_retrace_supersedes_without_deleting_prior_snapshot() -> None:
    from app.repository import seed_demo

    with memory_session() as (_engine, session):
        case = seed_demo(session)
        params = TraceParams(trace_mode="fixture", max_depth=4)
        first, _first_finding = get_or_create_trace(
            session,
            case,
            trace_mode="fixture",
            params=params,
        )
        second, _second_finding = get_or_create_trace(
            session,
            case,
            trace_mode="fixture",
            params=params,
            force_new=True,
        )
        session.refresh(first)
        snapshots = session.exec(
            select(TraceSnapshot).where(TraceSnapshot.case_id == case.id).order_by(TraceSnapshot.id)
        ).all()

        assert [row.id for row in snapshots] == [first.id, second.id]
        assert first.superseded_by_id == second.id
        assert second.parent_snapshot_id == first.id
        assert first.result_json["params"]["max_depth"] == 4
        assert first.sha256 != second.sha256


def test_live_input_parsing_keeps_integer_units_and_bounds() -> None:
    assert parse_amount_base("2858.661660") == 2_858_661_660
    params = parse_trace_params(
        {
            "max_depth": "7",
            "time_window_hours": "10000",
            "value_floor_share": "0.005",
            "breadth_cap": "4",
            "address_budget": "120",
            "strategy": "value_weighted",
            "include_unconfirmed": "yes",
        }
    )
    assert params.max_depth == 7
    assert params.time_window_hours == 10000
    assert str(params.value_floor_share) == "0.005"
    assert params.include_unconfirmed is True
    with pytest.raises(ValueError, match="10000 hours"):
        parse_trace_params({"time_window_hours": "10001"})
    with pytest.raises(ValueError):
        parse_amount_base("1.0000001")


def test_live_intake_route_mode_banner_status_and_retrace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live(monkeypatch)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    seed_row = _raw_transfer(
        txid=SEED_TXID,
        ts_ms=SEED_TS,
        source="TSender11111111111111111111111111111",
        destination=SEED_ADDRESS,
        amount_base=SEED_AMOUNT,
    )
    outgoing = _raw_transfer(
        txid="e" * 64,
        ts_ms=SEED_TS + 1_000,
        source=SEED_ADDRESS,
        destination=NEXT_ADDRESS,
        amount_base=10_000_000,
    )

    def fake_fetch(address: str, **_kwargs):
        return [seed_row, outgoing] if address == SEED_ADDRESS else []

    def fake_balance(address: str, **_kwargs):
        amount = 42_000_000 if address == NEXT_ADDRESS else 2_000_000
        return {
            "amount_base": amount,
            "retrieval_ts_ms": SEED_TS + 2_000,
            "raw_sha256": "balance-hash",
            "provider_request_ref": "balance-request",
        }

    app.dependency_overrides[main_module.get_session] = session_override
    monkeypatch.setattr(main_module, "append_audit_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_module, "read_audit_events", lambda **_kwargs: [])
    monkeypatch.setattr(
        tron,
        "resolve_usdt_transfer",
        lambda _txid: {
            "txid": SEED_TXID,
            "source": "TSender11111111111111111111111111111",
            "destination": SEED_ADDRESS,
            "amount_base": SEED_AMOUNT,
            "ts_ms": SEED_TS,
            "contract": tron.TRON_MAINNET_USDT,
            "decimals": 6,
            "block": 123,
            "event_index": 0,
        },
    )
    monkeypatch.setattr(tron, "verify_seed_transfer", lambda **_kwargs: {"event_name": "Transfer"})
    monkeypatch.setattr(tron, "fetch_trc20_transfers", fake_fetch)
    monkeypatch.setattr(tron, "fetch_trc20_balance", fake_balance)
    try:
        with TestClient(app) as client:
            assert client.post("/auth/prototype", data={"role": "io"}).status_code == 200
            intake = client.get("/cases/live/new")
            assert intake.status_code == 200
            assert "LIVE PROVIDER MODE" in intake.text
            assert 'name="time_window_hours" min="1" max="10000"' in intake.text
            token = re.search(r'name="csrf_token" value="([^"]+)"', intake.text)
            assert token
            started = client.post(
                "/cases/live/start",
                data={
                    "csrf_token": token.group(1),
                    "seed_kind": "txid",
                    "seed_value": SEED_TXID,
                    "max_depth": "1",
                    "time_window_hours": "8",
                    "value_floor_share": "0.02",
                    "breadth_cap": "3",
                    "address_budget": "60",
                    "strategy": "dominant_fund_flow",
                },
                follow_redirects=False,
            )
            assert started.status_code == 303
            trace = client.get(started.headers["location"])
            assert trace.status_code == 200
            assert "LIVE PROVIDER MODE" in trace.text
            assert "Sealed bounds" in trace.text
            assert "10.00 USDT" in trace.text
            assert "Attributed share 1.00 USDT" in trace.text
            assert "Provider proof" in trace.text
            assert "Projected classification" in trace.text
            assert HEURISTIC_DISCLAIMER in trace.text
            assert "Feature gates" in trace.text
            assert "Burner classification: disabled" in trace.text
            assert "View custody finding" in trace.text
            assert "No custody finding is recorded for this live snapshot." in trace.text
            assert 'href="/findings/' not in trace.text
            snapshot_id = int(started.headers["location"].rsplit("/", 1)[-1])
            status = client.get(f"/api/traces/{snapshot_id}/status").json()
            assert status["mode"] == "live"
            assert status["provider_evidence"]["canonical_event_count"] >= 1
            assert status["behavioral_projection"]["disclaimer"] == HEURISTIC_DISCLAIMER
            assert any(item["key"] == "live_tron_usdt" for item in status["capability_snapshot"])
            with Session(engine) as session:
                live_snapshot = session.get(TraceSnapshot, snapshot_id)
                assert live_snapshot
                live_case_id = live_snapshot.case_id
                live_case = session.get(Case, live_case_id)
                assert live_case.payment_txid == SEED_TXID
                assert live_case.reported_address == SEED_ADDRESS
                assert live_case.amount_reported_base == SEED_AMOUNT
                assert live_case.payment_ts_ms == SEED_TS
                hop = live_snapshot.result_json["hops"][0]
                assert hop["observed_amount_base"] == 10_000_000
                assert hop["value_base"] == SEED_AMOUNT
                canonical = session.exec(
                    select(CanonicalTraceEvent).where(CanonicalTraceEvent.snapshot_id == snapshot_id)
                ).all()
                assert [row.amount_base for row in canonical] == [10_000_000]
            mismatched_canvas = client.get(f"/cases/1/canvas?snapshot={snapshot_id}")
            assert mismatched_canvas.status_code == 404
            canvas = client.get(f"/cases/{live_case_id}/canvas?snapshot={snapshot_id}")
            assert canvas.status_code == 200
            assert "Confirmed fund-flow canvas" in canvas.text
            assert "Reported / attributed at frontier" in canvas.text
            assert "Confirmed transfer" in canvas.text
            assert "Attributed share 1.00 USDT" in canvas.text
            assert 'data-omega-graph' in canvas.text
            assert '/static/omega-graph.js' in canvas.text
            assert 'omega-graph-viewport' in canvas.text
            assert '"available": true' in canvas.text
            assert '"value": 42000000' in canvas.text
            assert "VASP hot wallet" not in canvas.text
            graph_js = client.get("/static/omega-graph.js")
            assert graph_js.status_code == 200
            assert "edgeWidth" in graph_js.text
            assert "data-omega-zoom" in graph_js.text
            assert "Balance source" in graph_js.text
            manifest = client.get(f"/api/cases/{live_case_id}/evidence-manifest").json()
            assert manifest["snapshot"]["trace_mode"] == "live"
            assert manifest["snapshot"]["params"]["max_depth"] == 1
            with Session(engine) as session:
                session.add(
                    Finding(
                        case_id=live_case_id,
                        snapshot_id=snapshot_id,
                        terminal_kind="vasp_deposit",
                        custodian_key="certified-live-custodian",
                        deposit_address=NEXT_ADDRESS,
                        amount_credited_base=SEED_AMOUNT,
                        created_ts_ms=SEED_TS + 3_000,
                    )
                )
                session.commit()
                finding = session.exec(
                    select(Finding).where(Finding.snapshot_id == snapshot_id)
                ).one()
                finding_id = int(finding.id)
            trace_with_finding = client.get(f"/traces/{snapshot_id}")
            assert trace_with_finding.status_code == 200
            assert f'href="/findings/{finding_id}"' in trace_with_finding.text
            assert "View custody finding" in trace_with_finding.text
            assert "No custody finding is recorded for this live snapshot." not in trace_with_finding.text
            canvas_with_finding = client.get(f"/cases/{live_case_id}/canvas?snapshot={snapshot_id}")
            assert f'href="/findings/{finding_id}"' in canvas_with_finding.text

            retrace_token = re.search(r'name="csrf_token" value="([^"]+)"', trace.text)
            assert retrace_token
            retraced = client.post(
                f"/traces/{snapshot_id}/retrace",
                data={"csrf_token": retrace_token.group(1)},
                follow_redirects=False,
            )
            assert retraced.status_code == 303
            assert retraced.headers["location"] != started.headers["location"]
            with Session(engine) as session:
                snapshots = session.exec(
                    select(TraceSnapshot)
                    .where(TraceSnapshot.case_id == live_case_id)
                    .order_by(TraceSnapshot.id)
                ).all()
                assert len(snapshots) == 2
                assert snapshots[0].superseded_by_id == snapshots[1].id
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_live_txid_intake_rejects_bad_hash_before_provider_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live(monkeypatch)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    def forbidden_resolver(_txid: str):
        raise AssertionError("Provider resolver should not be called for malformed txid")

    app.dependency_overrides[main_module.get_session] = session_override
    monkeypatch.setattr(main_module, "append_audit_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(tron, "resolve_usdt_transfer", forbidden_resolver)
    try:
        with TestClient(app) as client:
            assert client.post("/auth/prototype", data={"role": "io"}).status_code == 200
            intake = client.get("/cases/live/new")
            token = re.search(r'name="csrf_token" value="([^"]+)"', intake.text)
            assert token
            response = client.post(
                "/cases/live/start",
                data={
                    "csrf_token": token.group(1),
                    "seed_kind": "txid",
                    "seed_value": "not-a-real-hash",
                    "max_depth": "1",
                    "time_window_hours": "8",
                    "value_floor_share": "0.02",
                    "breadth_cap": "3",
                    "address_budget": "60",
                    "strategy": "dominant_fund_flow",
                },
            )

            assert response.status_code == 422
            assert "Transaction hash must be 64 hexadecimal characters." in response.text
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_live_txid_intake_surfaces_provider_resolution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_live(monkeypatch)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    def failing_resolver(_txid: str):
        raise tron.ProviderResponseError("No confirmed TRON USDT Transfer event found.")

    app.dependency_overrides[main_module.get_session] = session_override
    monkeypatch.setattr(main_module, "append_audit_event", lambda *args, **kwargs: {})
    monkeypatch.setattr(tron, "resolve_usdt_transfer", failing_resolver)
    try:
        with TestClient(app) as client:
            assert client.post("/auth/prototype", data={"role": "io"}).status_code == 200
            intake = client.get("/cases/live/new")
            token = re.search(r'name="csrf_token" value="([^"]+)"', intake.text)
            assert token
            response = client.post(
                "/cases/live/start",
                data={
                    "csrf_token": token.group(1),
                    "seed_kind": "txid",
                    "seed_value": "a" * 64,
                    "max_depth": "1",
                    "time_window_hours": "8",
                    "value_floor_share": "0.02",
                    "breadth_cap": "3",
                    "address_budget": "60",
                    "strategy": "dominant_fund_flow",
                },
            )

            assert response.status_code == 422
            assert "No confirmed TRON USDT Transfer event found." in response.text
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
