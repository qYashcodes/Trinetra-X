from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from threading import Barrier, Lock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.engine_bridge as engine_bridge
import app.repository as repository
from app.engine_bridge import (
    AssetRef,
    ChainRef,
    TraceParams,
    TraceSeed,
    classify,
    resolve_vasp,
    run_trace,
    terminal_creates_finding,
    trace_cache_identity,
)
from app.models import (
    AttributedLot,
    CanonicalTraceEvent,
    Case,
    CaseStage,
    CustodyAssertion,
    Finding,
    FrontierItem,
    SourceCoverage,
    TraceEvent,
    TraceSnapshot,
)
from app.repository import get_or_create_trace
from app.services.demo import demo_case
from app.services.time import now_ms
from app.settings import ROOT_DIR


def _trace(address: str, **overrides) -> dict:
    payload = {
        "address": address,
        "payment_ts_ms": None,
        "amount_base": None,
        "payment_txid": None,
    }
    payload.update(overrides)
    return run_trace(TraceSeed(**payload), TraceParams())


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_invalid_and_unsupported_seeds_do_not_serialize_demo_custody() -> None:
    examples = [
        ("invalid", "not-an-address", "invalid_seed"),
        ("evm", "0x0000000000000000000000000000000000000000", "unsupported_chain"),
        ("btc", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080", "unsupported_chain"),
        ("tron_nonfixture", "T1111111111111111111111111111111111", "seed_mismatch"),
    ]

    for _label, address, expected_terminal in examples:
        snapshot = _trace(address)
        assert snapshot["terminal"]["kind"] == expected_terminal
        assert snapshot["terminal"]["kind"] != "vasp_deposit"
        assert snapshot["seed_match"]["matched"] is False
        assert snapshot["outcome"]["creates_finding"] is False
        assert snapshot["hops"] == []
        assert snapshot["chain"]["family"] != "TRON" or address.startswith("T")


def test_zero_negative_and_wrong_payment_details_are_rejected_without_fallback() -> None:
    case = demo_case()["case"]
    address = case["reported_address"]
    examples = [
        {"amount_base": 0},
        {"amount_base": -1},
        {"payment_ts_ms": 0},
        {"payment_ts_ms": -1},
        {"amount_base": case["amount_reported_base"] + 1},
        {"payment_ts_ms": case["victim_payment_ts_ms"] + 1},
        {"payment_txid": "0" * 64},
        {"asset": AssetRef("USDT", "wrong-contract", 6)},
    ]

    for overrides in examples:
        snapshot = _trace(address, **overrides)
        assert snapshot["terminal"]["kind"] in {"invalid_seed", "seed_mismatch"}
        assert snapshot["terminal"]["kind"] != "vasp_deposit"
        assert snapshot["seed_match"]["matched"] is False
        if "amount_base" in overrides:
            assert snapshot["case"]["amount_reported_base"] == overrides["amount_base"]
        if "payment_ts_ms" in overrides:
            assert snapshot["case"]["payment_ts"] == overrides["payment_ts_ms"]


def test_live_edge_identity_preserves_explicit_zero_and_empty_txid() -> None:
    edge = {
        "source": "TSeed",
        "destination": "TNext",
        "amount_base": 0,
        "attributed_base": 0,
        "txid": "",
        "ts_ms": 0,
        "event_index": 0,
    }

    rows = engine_bridge._live_outgoing_rows([edge], source_address="TSeed", min_ts_ms=0)
    event_ref = engine_bridge._live_edge_ref(edge, 4)
    hop = engine_bridge._live_hop_event(
        {**edge, "stable_id": event_ref, "residual_numerator": 0},
        rank=1,
        seed_address="TSeed",
        seed_amount_base=1,
    )

    assert rows == [edge]
    assert event_ref == "live:tron::0"
    assert hop["observed_amount_base"] == 0
    assert hop["value_base"] == 0
    assert hop["txids"] == [""]


def test_live_mode_without_adapter_closes_as_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRINETRA_MODE", "live")
    snapshot = _trace(demo_case()["case"]["reported_address"])
    assert snapshot["engine"]["mode"] == "live"
    assert snapshot["terminal"]["kind"] == "provider_error"
    assert snapshot["outcome"]["stage"] == "trace_failed"
    assert snapshot["hops"] == []
    assert snapshot["outcome"]["creates_finding"] is False


def test_live_mode_keeps_evm_explicitly_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRINETRA_MODE", "live")
    snapshot = _trace(
        "0x0000000000000000000000000000000000000000",
        payment_ts_ms=now_ms(),
        amount_base=1,
        payment_txid="1" * 64,
        chain=ChainRef("EVM", "ethereum", 1),
        asset=AssetRef("ETH", None, 18),
    )

    assert snapshot["terminal"]["kind"] == "unsupported_chain"
    assert snapshot["outcome"]["stage"] == "trace_unsupported"
    assert snapshot["outcome"]["creates_finding"] is False


def test_gated_live_tron_slice_verifies_seed_without_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.adapters import tron

    source = demo_case()["case"]
    calls: dict[str, object] = {}

    def fake_verify_seed_transfer(**kwargs):
        calls["verify"] = kwargs
        return {"event_name": "Transfer", "result": {"to": source["reported_address"]}}

    def fake_fetch_trc20_transfers(address: str, **kwargs):
        calls["fetch"] = {"address": address, **kwargs}
        return [
            {
                "transaction_id": source["payment_txid"],
                "block_timestamp": source["victim_payment_ts_ms"],
                "from": "TSender",
                "to": source["reported_address"],
                "value": str(source["amount_reported_base"]),
                "token_info": {
                    "symbol": "USDT",
                    "decimals": 6,
                    "address": tron.TRON_MAINNET_USDT,
                },
            },
            {
                "transaction_id": "2" * 64,
                "block_timestamp": source["victim_payment_ts_ms"] + 60_000,
                "from": source["reported_address"],
                "to": "TNextHop",
                "value": "10000000000",
                "event_index": 0,
                "token_info": {
                    "symbol": "USDT",
                    "decimals": 6,
                    "address": tron.TRON_MAINNET_USDT,
                },
            },
            {
                "transaction_id": "3" * 64,
                "block_timestamp": source["victim_payment_ts_ms"] + 120_000,
                "from": source["reported_address"],
                "to": "TDustHop",
                "value": "100",
                "event_index": 1,
                "token_info": {
                    "symbol": "USDT",
                    "decimals": 6,
                    "address": tron.TRON_MAINNET_USDT,
                },
            },
        ]

    monkeypatch.setenv("TRINETRA_MODE", "live")
    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    monkeypatch.setattr(tron, "verify_seed_transfer", fake_verify_seed_transfer)
    monkeypatch.setattr(tron, "fetch_trc20_transfers", fake_fetch_trc20_transfers)

    snapshot = run_trace(
        TraceSeed(
            address=source["reported_address"],
            payment_ts_ms=source["victim_payment_ts_ms"],
            amount_base=source["amount_reported_base"],
            payment_txid=source["payment_txid"],
            chain=ChainRef("TRON", "mainnet"),
            asset=AssetRef("USDT", tron.TRON_MAINNET_USDT, 6),
        ),
        TraceParams(time_window_hours=1),
    )

    assert snapshot["engine"]["mode"] == "live"
    assert snapshot["terminal"]["kind"] == "depth_exhausted"
    assert snapshot["terminal"]["reason"] == "live_tron_seed_verified"
    assert snapshot["terminal"]["provider"] == "trongrid"
    assert snapshot["terminal"]["outgoing_observed"] == 2
    assert snapshot["terminal"]["scheduled_frontier"] == 1
    assert snapshot["terminal"]["deferred_frontier"] == 1
    assert snapshot["outcome"]["status"] == "incomplete"
    assert snapshot["outcome"]["stage"] == "trace_incomplete"
    assert snapshot["outcome"]["creates_finding"] is False
    assert snapshot["seed_match"]["matched"] is True
    assert [hop["address"] for hop in snapshot["hops"]] == ["TNextHop"]
    assert snapshot["hops"][0]["value_base"] == 10_000_000_000
    assert snapshot["parked"][0]["address"] == "TDustHop"
    assert snapshot["parked"][0]["deferral_reason"] == "value_floor"
    assert snapshot["classifications"] == {}
    assert [event["type"] for event in snapshot["events"]] == [
        "source",
        "work",
        "stats",
        "hop",
        "parked",
        "terminal",
        "done",
    ]
    assert snapshot["stats"]["transfers_read"] == 3
    assert snapshot["stats"]["frontier_queued"] == 1
    assert snapshot["stats"]["branches_parked"] == 1
    assert calls["verify"]["contract_address"] == tron.TRON_MAINNET_USDT
    assert calls["fetch"]["contract_address"] == tron.TRON_MAINNET_USDT


def test_live_tron_trace_gate_runs_bounded_multi_hop_without_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.adapters import tron

    source = demo_case()["case"]
    fetch_calls: list[str] = []
    late_ts = source["victim_payment_ts_ms"] + 3_600_000 + 1

    def fake_verify_seed_transfer(**_kwargs):
        return {"event_name": "Transfer", "result": {"to": source["reported_address"]}}

    def fake_fetch_trc20_transfers(address: str, **_kwargs):
        fetch_calls.append(address)
        if address == source["reported_address"]:
            return [
                {
                    "transaction_id": source["payment_txid"],
                    "block_timestamp": source["victim_payment_ts_ms"],
                    "from": "TSender",
                    "to": source["reported_address"],
                    "value": str(source["amount_reported_base"]),
                    "event_index": 0,
                    "token_info": {
                        "symbol": "USDT",
                        "decimals": 6,
                        "address": tron.TRON_MAINNET_USDT,
                    },
                },
                {
                    "transaction_id": "2" * 64,
                    "block_timestamp": source["victim_payment_ts_ms"] + 60_000,
                    "from": source["reported_address"],
                    "to": "TAlpha",
                    "value": "10000000000",
                    "event_index": 1,
                    "token_info": {
                        "symbol": "USDT",
                        "decimals": 6,
                        "address": tron.TRON_MAINNET_USDT,
                    },
                },
                {
                    "transaction_id": "3" * 64,
                    "block_timestamp": source["victim_payment_ts_ms"] + 120_000,
                    "from": source["reported_address"],
                    "to": "TDust",
                    "value": "100",
                    "event_index": 2,
                    "token_info": {
                        "symbol": "USDT",
                        "decimals": 6,
                        "address": tron.TRON_MAINNET_USDT,
                    },
                },
            ]
        if address == "TAlpha":
            return [
                {
                    "transaction_id": "4" * 64,
                    "block_timestamp": source["victim_payment_ts_ms"] + 180_000,
                    "from": "TAlpha",
                    "to": "TBeta",
                    "value": "4000000000",
                    "event_index": 0,
                    "token_info": {
                        "symbol": "USDT",
                        "decimals": 6,
                        "address": tron.TRON_MAINNET_USDT,
                    },
                },
                {
                    "transaction_id": "5" * 64,
                    "block_timestamp": late_ts,
                    "from": "TAlpha",
                    "to": "TTooLate",
                    "value": "2000000000",
                    "event_index": 1,
                    "token_info": {
                        "symbol": "USDT",
                        "decimals": 6,
                        "address": tron.TRON_MAINNET_USDT,
                    },
                },
            ]
        if address == "TBeta":
            return []
        raise AssertionError(f"Unexpected live fetch for {address}")

    monkeypatch.setenv("TRINETRA_MODE", "live")
    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_TRACE_VERIFIED", "true")
    monkeypatch.setattr(tron, "verify_seed_transfer", fake_verify_seed_transfer)
    monkeypatch.setattr(tron, "fetch_trc20_transfers", fake_fetch_trc20_transfers)

    snapshot = run_trace(
        TraceSeed(
            address=source["reported_address"],
            payment_ts_ms=source["victim_payment_ts_ms"],
            amount_base=source["amount_reported_base"],
            payment_txid=source["payment_txid"],
            chain=ChainRef("TRON", "mainnet"),
            asset=AssetRef("USDT", tron.TRON_MAINNET_USDT, 6),
        ),
        TraceParams(
            strategy="value_weighted",
            breadth_cap=2,
            time_window_hours=1,
        ),
    )

    assert snapshot["engine"]["version"] == "engine-v1.2-live-tron-trace"
    assert snapshot["seed_match"]["trace"] == "bounded_multi_hop"
    assert snapshot["terminal"]["reason"] == "live_tron_trace_bounded"
    assert snapshot["terminal"]["addresses_queried"] == 3
    assert snapshot["terminal"]["custody_evaluation"] == "disabled_without_provider_attribution"
    assert snapshot["outcome"]["creates_finding"] is False
    assert snapshot["classifications"] == {}
    assert fetch_calls == [source["reported_address"], "TAlpha", "TBeta"]
    assert [hop["address"] for hop in snapshot["hops"]] == ["TAlpha", "TBeta"]
    assert [hop["frontier_state"] for hop in snapshot["hops"]] == ["expanded", "expanded"]
    assert {row["address"]: row["deferral_reason"] for row in snapshot["parked"]} == {
        "TDust": "value_floor",
        "TTooLate": "time_window",
    }
    assert snapshot["stats"]["addresses_visited"] == 3
    assert snapshot["stats"]["transfers_read"] == 5
    assert snapshot["stats"]["expanded_hops"] == 2
    assert snapshot["stats"]["frontier_queued"] == 0
    assert snapshot["terminal"]["stationary_amount_base"] == 19_939_999_900


def test_live_gate_state_changes_trace_cache_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    source = demo_case()["case"]
    seed = TraceSeed(
        address=source["reported_address"],
        payment_ts_ms=source["victim_payment_ts_ms"],
        amount_base=source["amount_reported_base"],
        payment_txid=source["payment_txid"],
        chain=ChainRef("TRON", "mainnet"),
    )
    params = TraceParams()

    monkeypatch.setenv("TRINETRA_MODE", "live")
    for name in (
        "TRINETRA_ENABLE_LIVE_TRON",
        "TRONGRID_API_KEY",
        "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED",
        "TRINETRA_LIVE_TRON_SMOKE_VERIFIED",
    ):
        monkeypatch.delenv(name, raising=False)
    gated_identity = trace_cache_identity(seed, params)
    gated_snapshot = run_trace(seed, params)

    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    enabled_identity = trace_cache_identity(seed, params)

    assert gated_identity != enabled_identity
    assert gated_snapshot["engine"]["version"] == "engine-v1.0-live-gated"


def test_wrong_chain_classification_does_not_resolve_fixture_vasp() -> None:
    deposit = demo_case()["terminal"]["deposit_address"]
    evm_chain = ChainRef("EVM", "ethereum", 1)
    assert resolve_vasp(deposit, evm_chain) is None
    classified = classify(deposit, evm_chain)
    assert classified["class"] == "unknown"
    assert classified["vasp"] is None


def test_fixture_classification_exposes_evidence_band_not_calibrated_probability() -> None:
    deposit = demo_case()["terminal"]["deposit_address"]
    classified = classify(deposit, ChainRef("TRON", "mainnet"))

    assert classified["band"] == "high"
    assert classified["posterior"] is None
    assert classified["probability_enabled"] is False
    assert classified["score_kind"] == "fixture_evidence_band"
    assert classified["evidence_class"] == "service_role"


def test_non_custody_trace_persists_without_finding_or_custody_stage(session: Session) -> None:
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/UNSUPPORTED",
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
    assert snapshot.status == "unsupported"
    assert snapshot.result_json["terminal"]["kind"] == "unsupported_chain"
    assert snapshot.result_json["outcome"]["creates_finding"] is False
    session.refresh(case)
    assert case.stage == CaseStage.trace_unsupported
    assert session.exec(select(Finding)).all() == []
    event_types = [
        row.event_type
        for row in session.exec(select(TraceEvent).order_by(TraceEvent.seq)).all()
    ]
    assert event_types == ["source", "terminal", "done"]
    assert session.exec(select(CanonicalTraceEvent)).all() == []
    assert session.exec(select(AttributedLot)).all() == []
    assert session.exec(select(FrontierItem)).all() == []
    assert session.exec(select(CustodyAssertion)).all() == []
    coverage = session.exec(select(SourceCoverage)).all()
    assert [row.completeness for row in coverage] == ["closed_without_coverage", "balanced"]
    accounting = next(row for row in coverage if row.provider == "trace-accounting")
    assert accounting.observed_watermark["totals"]["unresolved"] == 100


def test_fixture_trace_persists_source_work_stats_and_finding(session: Session) -> None:
    source = demo_case()["case"]
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/FIXTURE",
        category=source["category"],
        jurisdiction=source["jurisdiction"],
        filed_ts_ms=source["filed_ts_ms"],
        amount_reported_base=source["amount_reported_base"],
        asset_symbol=source["asset"]["symbol"],
        asset_decimals=source["asset"]["decimals"],
        chain_family=source["chain"]["family"],
        chain_network=source["chain"]["network"],
        reported_address=source["reported_address"],
        payment_txid=source["payment_txid"],
        payment_ts_ms=source["victim_payment_ts_ms"],
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)

    snapshot, finding = get_or_create_trace(session, case)

    assert finding is not None
    assert snapshot.status == "complete"
    assert snapshot.cache_identity == snapshot.result_json["cache_identity"]
    assert snapshot.result_json["terminal"]["kind"] == "vasp_deposit"
    session.refresh(case)
    assert case.stage == CaseStage.custody_found
    event_types = [
        row.event_type
        for row in session.exec(select(TraceEvent).order_by(TraceEvent.seq)).all()
    ]
    assert "source" in event_types
    assert "work" in event_types
    assert "stats" in event_types
    assert event_types[-2:] == ["terminal", "done"]
    canonical = session.exec(
        select(CanonicalTraceEvent).order_by(CanonicalTraceEvent.tx_index)
    ).all()
    assert len(canonical) == len(snapshot.result_json["hops"])
    assert canonical[0].destination_address == source["reported_address"]
    assert canonical[-1].destination_address == snapshot.result_json["terminal"]["deposit_address"]
    coverage = session.exec(select(SourceCoverage)).all()
    assert {row.provider for row in coverage} >= {
        "fixture-ledger",
        "attribution-registry",
        "trace-accounting",
    }
    lots = session.exec(select(AttributedLot).order_by(AttributedLot.id)).all()
    assert [row.state for row in lots] == ["custody_candidate", "deferred", "deferred"]
    assert sum(row.remaining_amount_base for row in lots) == source["amount_reported_base"]
    frontier = session.exec(select(FrontierItem).order_by(FrontierItem.priority_base.desc())).all()
    assert [row.deferral_reason for row in frontier] == ["off_dominant_flow", "off_dominant_flow"]
    assertions = session.exec(select(CustodyAssertion)).all()
    assert len(assertions) == 1
    assert assertions[0].deposit_address == snapshot.result_json["terminal"]["deposit_address"]
    assert assertions[0].provenance["hot_wallet"] == snapshot.result_json["terminal"]["hot_wallet"]


def test_verified_custody_terminal_persists_finding_with_provider_provenance(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert terminal_creates_finding({"kind": "verified_custody"}) is False
    assert terminal_creates_finding(
        {
            "kind": "verified_custody",
            "deposit_address": "TVerifiedDeposit",
            "amount_credited_base": 1,
        }
    ) is True
    assert terminal_creates_finding({"kind": "custody_candidate"}) is False

    source = demo_case()["case"]
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/VERIFIED-CUSTODY",
        category=source["category"],
        jurisdiction=source["jurisdiction"],
        filed_ts_ms=source["filed_ts_ms"],
        amount_reported_base=source["amount_reported_base"],
        asset_symbol=source["asset"]["symbol"],
        asset_decimals=source["asset"]["decimals"],
        chain_family=source["chain"]["family"],
        chain_network=source["chain"]["network"],
        reported_address=source["reported_address"],
        payment_txid=source["payment_txid"],
        payment_ts_ms=source["victim_payment_ts_ms"],
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)

    def verified_run_trace(seed: TraceSeed, params: TraceParams) -> dict:
        result = run_trace(seed, params)
        terminal = {
            **result["terminal"],
            "kind": "verified_custody",
            "custodian_key": "coinsphere",
            "provenance": {
                "source": "provider-certified-response",
                "response_ref": "certified-response-1",
            },
        }
        result["terminal"] = terminal
        result["outcome"] = {
            **result["outcome"],
            "stage": "custody_found",
            "creates_finding": True,
        }
        result["events"] = [
            {"type": "terminal", "data": terminal}
            if event.get("type") == "terminal"
            else event
            for event in result["events"]
        ]
        result["sha256"] = repository.sha256_json(result)
        return result

    monkeypatch.setattr(repository, "run_trace", verified_run_trace)

    snapshot, finding = get_or_create_trace(session, case)

    assert finding is not None
    assert finding.terminal_kind == "verified_custody"
    assert snapshot.result_json["terminal"]["kind"] == "verified_custody"
    session.refresh(case)
    assert case.stage == CaseStage.custody_found
    assertions = session.exec(select(CustodyAssertion)).all()
    assert len(assertions) == 1
    assert assertions[0].provider_key == "coinsphere"
    assert assertions[0].provenance["source"] == "provider-certified-response"
    assert assertions[0].provenance["response_ref"] == "certified-response-1"
    assert "separate from the hot wallet" in assertions[0].provenance["note"]


def test_gated_live_tron_slice_persists_verified_coverage_without_finding(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.adapters import tron

    source = demo_case()["case"]

    monkeypatch.setenv("TRINETRA_MODE", "live")
    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    monkeypatch.setattr(
        tron,
        "verify_seed_transfer",
        lambda **_kwargs: {"event_name": "Transfer", "result": {"to": source["reported_address"]}},
    )
    monkeypatch.setattr(
        tron,
        "fetch_trc20_transfers",
        lambda *_args, **_kwargs: [
            {
                "transaction_id": source["payment_txid"],
                "block_timestamp": source["victim_payment_ts_ms"],
                "from": "TSender",
                "to": source["reported_address"],
                "value": str(source["amount_reported_base"]),
                "token_info": {
                    "symbol": "USDT",
                    "decimals": 6,
                    "address": tron.TRON_MAINNET_USDT,
                },
            }
        ],
    )
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/LIVE-TRON",
        category=source["category"],
        jurisdiction=source["jurisdiction"],
        filed_ts_ms=source["filed_ts_ms"],
        amount_reported_base=source["amount_reported_base"],
        asset_symbol=source["asset"]["symbol"],
        asset_decimals=source["asset"]["decimals"],
        chain_family=source["chain"]["family"],
        chain_network=source["chain"]["network"],
        reported_address=source["reported_address"],
        payment_txid=source["payment_txid"],
        payment_ts_ms=source["victim_payment_ts_ms"],
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)

    snapshot, finding = get_or_create_trace(session, case)

    assert finding is None
    assert snapshot.status == "incomplete"
    assert snapshot.result_json["terminal"]["kind"] == "stationary_funds"
    assert snapshot.result_json["terminal"]["reason"] == "live_tron_seed_verified"
    session.refresh(case)
    assert case.stage == CaseStage.stationary_observed
    assert session.exec(select(Finding)).all() == []
    assert session.exec(select(CanonicalTraceEvent)).all() == []
    event_types = [
        row.event_type
        for row in session.exec(select(TraceEvent).order_by(TraceEvent.seq)).all()
    ]
    assert event_types == ["source", "work", "stats", "terminal", "done"]
    coverage = session.exec(select(SourceCoverage).order_by(SourceCoverage.id)).all()
    assert coverage[0].provider == "trongrid"
    assert coverage[0].completeness == "queried_verified"
    assert coverage[0].gaps == []
    assert coverage[-1].provider == "trace-accounting"


def test_gated_live_tron_outgoing_edges_persist_frontier_and_conservation(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.adapters import tron

    source = demo_case()["case"]
    first_out = 10_000_000_000
    dust_out = 100

    monkeypatch.setenv("TRINETRA_MODE", "live")
    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    monkeypatch.setattr(
        tron,
        "verify_seed_transfer",
        lambda **_kwargs: {"event_name": "Transfer", "result": {"to": source["reported_address"]}},
    )
    monkeypatch.setattr(
        tron,
        "fetch_trc20_transfers",
        lambda *_args, **_kwargs: [
            {
                "transaction_id": source["payment_txid"],
                "block_timestamp": source["victim_payment_ts_ms"],
                "from": "TSender",
                "to": source["reported_address"],
                "value": str(source["amount_reported_base"]),
                "event_index": 0,
                "token_info": {
                    "symbol": "USDT",
                    "decimals": 6,
                    "address": tron.TRON_MAINNET_USDT,
                },
            },
            {
                "transaction_id": "2" * 64,
                "block_timestamp": source["victim_payment_ts_ms"] + 60_000,
                "from": source["reported_address"],
                "to": "TNextHop",
                "value": str(first_out),
                "event_index": 7,
                "token_info": {
                    "symbol": "USDT",
                    "decimals": 6,
                    "address": tron.TRON_MAINNET_USDT,
                },
            },
            {
                "transaction_id": "3" * 64,
                "block_timestamp": source["victim_payment_ts_ms"] + 120_000,
                "from": source["reported_address"],
                "to": "TDustHop",
                "value": str(dust_out),
                "event_index": 8,
                "token_info": {
                    "symbol": "USDT",
                    "decimals": 6,
                    "address": tron.TRON_MAINNET_USDT,
                },
            },
        ],
    )
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/LIVE-FRONTIER",
        category=source["category"],
        jurisdiction=source["jurisdiction"],
        filed_ts_ms=source["filed_ts_ms"],
        amount_reported_base=source["amount_reported_base"],
        asset_symbol=source["asset"]["symbol"],
        asset_decimals=source["asset"]["decimals"],
        chain_family=source["chain"]["family"],
        chain_network=source["chain"]["network"],
        reported_address=source["reported_address"],
        payment_txid=source["payment_txid"],
        payment_ts_ms=source["victim_payment_ts_ms"],
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)

    snapshot, finding = get_or_create_trace(session, case)

    assert finding is None
    assert snapshot.result_json["terminal"]["kind"] == "depth_exhausted"
    canonical = session.exec(select(CanonicalTraceEvent)).all()
    assert len(canonical) == 1
    assert canonical[0].source_address == source["reported_address"]
    assert canonical[0].destination_address == "TNextHop"
    assert canonical[0].amount_base == first_out
    assert canonical[0].event_index == 7
    assert canonical[0].finality == "provider_confirmed"

    lots = session.exec(select(AttributedLot).order_by(AttributedLot.state)).all()
    assert {row.current_address: row.state for row in lots} == {
        "TNextHop": "queued",
        "TDustHop": "deferred",
    }
    frontier = session.exec(select(FrontierItem).order_by(FrontierItem.priority_base.desc())).all()
    assert [(row.state, row.deferral_reason) for row in frontier] == [
        ("queued", None),
        ("deferred", "value_floor"),
    ]
    assert frontier[0].resume_cursor["source_address"] == source["reported_address"]
    assert frontier[0].resume_cursor["event_index"] == 7
    assert frontier[1].resume_cursor["event_index"] == 8

    accounting = [
        row for row in session.exec(select(SourceCoverage)).all()
        if row.provider == "trace-accounting"
    ][0]
    totals = accounting.observed_watermark["totals"]
    assert totals["deferred"] == first_out + dust_out
    assert totals["stationary"] == source["amount_reported_base"] - first_out - dust_out
    assert totals["unresolved"] == 0


def test_duplicate_trace_request_reuses_snapshot_and_evidence(session: Session) -> None:
    source = demo_case()["case"]
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/DUPLICATE",
        category=source["category"],
        jurisdiction=source["jurisdiction"],
        filed_ts_ms=source["filed_ts_ms"],
        amount_reported_base=source["amount_reported_base"],
        asset_symbol=source["asset"]["symbol"],
        asset_decimals=source["asset"]["decimals"],
        chain_family=source["chain"]["family"],
        chain_network=source["chain"]["network"],
        reported_address=source["reported_address"],
        payment_txid=source["payment_txid"],
        payment_ts_ms=source["victim_payment_ts_ms"],
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)

    first_snapshot, first_finding = get_or_create_trace(session, case)
    second_snapshot, second_finding = get_or_create_trace(session, case)

    assert first_snapshot.id == second_snapshot.id
    assert first_finding is not None and second_finding is not None
    assert first_finding.id == second_finding.id
    assert len(session.exec(select(TraceSnapshot)).all()) == 1
    assert len(session.exec(select(CanonicalTraceEvent)).all()) == len(
        first_snapshot.result_json["hops"]
    )
    assert len(session.exec(select(CustodyAssertion)).all()) == 1


def test_concurrent_trace_request_reuses_single_snapshot_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = demo_case()["case"]
    db_path = ROOT_DIR / "var" / "test-trace-race.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    ts = now_ms()
    with Session(engine) as setup:
        case = Case(
            ack_no="NCRP/2026/TEST/CONCURRENT",
            category=source["category"],
            jurisdiction=source["jurisdiction"],
            filed_ts_ms=source["filed_ts_ms"],
            amount_reported_base=source["amount_reported_base"],
            asset_symbol=source["asset"]["symbol"],
            asset_decimals=source["asset"]["decimals"],
            chain_family=source["chain"]["family"],
            chain_network=source["chain"]["network"],
            reported_address=source["reported_address"],
            payment_txid=source["payment_txid"],
            payment_ts_ms=source["victim_payment_ts_ms"],
            created_ts_ms=ts,
            updated_ts_ms=ts,
        )
        setup.add(case)
        setup.commit()
        setup.refresh(case)
        case_id = case.id

    original_lookup = repository._trace_by_cache_identity
    barrier = Barrier(2)
    lock = Lock()
    lookup_count = 0

    def racing_lookup(*args, **kwargs):
        nonlocal lookup_count
        with lock:
            lookup_count += 1
            should_race = lookup_count <= 2
        if should_race:
            barrier.wait(timeout=5)
            return None
        return original_lookup(*args, **kwargs)

    monkeypatch.setattr(repository, "_trace_by_cache_identity", racing_lookup)

    def worker() -> tuple[int | None, int | None]:
        with Session(engine) as worker_session:
            worker_case = worker_session.get(Case, case_id)
            assert worker_case is not None
            snapshot, finding = get_or_create_trace(worker_session, worker_case)
            return snapshot.id, finding.id if finding else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        second = pool.submit(worker)
        results = [first.result(timeout=10), second.result(timeout=10)]

    with Session(engine) as verify:
        snapshots = verify.exec(select(TraceSnapshot)).all()
        findings = verify.exec(select(Finding)).all()
        canonical = verify.exec(select(CanonicalTraceEvent)).all()
        assertions = verify.exec(select(CustodyAssertion)).all()

    assert len({snapshot_id for snapshot_id, _finding_id in results}) == 1
    assert len({finding_id for _snapshot_id, finding_id in results}) == 1
    assert len(snapshots) == 1
    assert len(findings) == 1
    assert len(canonical) == len(snapshots[0].result_json["hops"])
    assert len(assertions) == 1
    engine.dispose()
    if db_path.exists():
        db_path.unlink()


def test_trace_write_rolls_back_snapshot_events_and_stage_on_precommit_crash(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = demo_case()["case"]
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/ROLLBACK",
        category=source["category"],
        jurisdiction=source["jurisdiction"],
        filed_ts_ms=source["filed_ts_ms"],
        amount_reported_base=source["amount_reported_base"],
        asset_symbol=source["asset"]["symbol"],
        asset_decimals=source["asset"]["decimals"],
        chain_family=source["chain"]["family"],
        chain_network=source["chain"]["network"],
        reported_address=source["reported_address"],
        payment_txid=source["payment_txid"],
        payment_ts_ms=source["victim_payment_ts_ms"],
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)

    def crash(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated projection crash")

    monkeypatch.setattr(repository, "_add_evidence_projection", crash)

    with pytest.raises(RuntimeError, match="simulated projection crash"):
        repository.get_or_create_trace(session, case)

    assert session.exec(select(TraceSnapshot)).all() == []
    assert session.exec(select(TraceEvent)).all() == []
    assert session.exec(select(CanonicalTraceEvent)).all() == []
    assert session.exec(select(Finding)).all() == []
    session.refresh(case)
    assert case.stage == CaseStage.intake


def test_changed_case_seed_supersedes_stale_snapshot_instead_of_reusing_finding(
    session: Session,
) -> None:
    source = demo_case()["case"]
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/STALE",
        category=source["category"],
        jurisdiction=source["jurisdiction"],
        filed_ts_ms=source["filed_ts_ms"],
        amount_reported_base=source["amount_reported_base"],
        asset_symbol=source["asset"]["symbol"],
        asset_decimals=source["asset"]["decimals"],
        chain_family=source["chain"]["family"],
        chain_network=source["chain"]["network"],
        reported_address=source["reported_address"],
        payment_txid=source["payment_txid"],
        payment_ts_ms=source["victim_payment_ts_ms"],
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)

    first_snapshot, first_finding = get_or_create_trace(session, case)
    assert first_finding is not None
    assert first_snapshot.result_json["terminal"]["kind"] == "vasp_deposit"

    case.payment_txid = "f" * 64
    case.updated_ts_ms = now_ms()
    session.add(case)
    session.commit()
    session.refresh(case)

    second_snapshot, second_finding = get_or_create_trace(session, case)
    session.refresh(first_snapshot)

    assert second_snapshot.id != first_snapshot.id
    assert second_snapshot.parent_snapshot_id == first_snapshot.id
    assert first_snapshot.superseded_by_id == second_snapshot.id
    assert second_finding is None
    assert second_snapshot.result_json["terminal"]["kind"] == "seed_mismatch"
    assert (
        second_snapshot.result_json["cache_identity"]
        != first_snapshot.result_json["cache_identity"]
    )
    assert len(session.exec(select(TraceSnapshot)).all()) == 2
