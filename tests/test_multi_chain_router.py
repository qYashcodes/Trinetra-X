from __future__ import annotations

import pytest

from app.engine_bridge import AssetRef, ChainRef, TraceParams, TraceSeed, run_trace
from engine.adapters import evm
from engine.runtime import btc_engine, evm_engine, tron_engine
from engine.runtime.router import AmbiguousSeedError, resolve_live_seed


def test_router_resolves_tron_txid_through_tron_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    txid = "a" * 64
    monkeypatch.setattr(
        tron_engine,
        "resolve_transaction",
        lambda _txid: [
            tron_engine.TronResolution(
                chain=ChainRef("TRON", "mainnet"),
                address="TQH4FqaxJ9rxmKrfGcjiFrhMHnmdxwELbu",
                txid=txid,
                amount_base=1_000_000,
                ts_ms=1_789_337_235_000,
                asset=AssetRef("USDT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6),
                provider="trongrid",
            )
        ],
    )
    monkeypatch.setattr(evm_engine, "resolve_transaction", lambda _txid: [])
    monkeypatch.setattr(btc_engine, "resolve_transaction", lambda _txid: [])

    resolved = resolve_live_seed(seed_kind="txid", seed_value=txid)

    assert resolved.chain.family == "TRON"
    assert resolved.payment_txid == txid
    assert resolved.address.startswith("T")
    assert resolved.amount_base == 1_000_000
    assert resolved.resolution == "transaction_hash"


def test_router_sends_0x_txid_to_evm_without_tron_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    txid = "0x" + "1" * 64

    def fail_tron_probe(_txid: str) -> list[tron_engine.TronResolution]:
        raise AssertionError("0x transaction hashes must not be probed through TRON")

    monkeypatch.setattr(tron_engine, "resolve_transaction", fail_tron_probe)
    monkeypatch.setattr(
        evm_engine,
        "resolve_transaction",
        lambda seen_txid: [
            evm_engine.EvmResolution(
                chain=ChainRef("EVM", "ethereum", 1),
                address="0x2222222222222222222222222222222222222222",
                txid=seen_txid,
                amount_base=1_500_000_000_000_000_000,
                ts_ms=1_789_337_235_000,
                asset=AssetRef("ETH", None, 18),
                provider="etherscan-v2",
            )
        ],
    )
    monkeypatch.setattr(btc_engine, "resolve_transaction", lambda _txid: [])

    resolved = resolve_live_seed(seed_kind="txid", seed_value=txid)

    assert resolved.chain.family == "EVM"
    assert resolved.provider == "etherscan-v2"
    assert resolved.payment_txid == txid
    assert resolved.address == "0x2222222222222222222222222222222222222222"
    assert resolved.amount_base == 1_500_000_000_000_000_000


def test_router_fails_closed_when_txid_is_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    txid = "b" * 64
    monkeypatch.setattr(
        tron_engine,
        "resolve_transaction",
        lambda _txid: [
            tron_engine.TronResolution(
                chain=ChainRef("TRON", "mainnet"),
                address="TQH4FqaxJ9rxmKrfGcjiFrhMHnmdxwELbu",
                txid=txid,
                amount_base=1,
                ts_ms=1,
                asset=AssetRef("USDT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6),
                provider="trongrid",
            )
        ],
    )
    monkeypatch.setattr(
        evm_engine,
        "resolve_transaction",
        lambda _txid: [
            evm_engine.EvmResolution(
                chain=ChainRef("EVM", "ethereum", 1),
                address="0x0000000000000000000000000000000000000000",
                txid="0x" + txid,
                amount_base=1,
                ts_ms=1,
                asset=AssetRef("ETH", None, 18),
                provider="etherscan",
            )
        ],
    )
    monkeypatch.setattr(btc_engine, "resolve_transaction", lambda _txid: [])

    with pytest.raises(AmbiguousSeedError):
        resolve_live_seed(seed_kind="txid", seed_value=txid)


def test_address_seed_uses_chain_specific_base_units() -> None:
    evm = resolve_live_seed(
        seed_kind="address",
        seed_value="0x0000000000000000000000000000000000000000",
        amount="1.5",
        payment_ts_ist="2026-09-21T12:00",
    )
    btc = resolve_live_seed(
        seed_kind="address",
        seed_value="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080",
        amount="1.5",
        payment_ts_ist="2026-09-21T12:00",
    )

    assert evm.chain.family == "EVM"
    assert evm.asset.decimals == 18
    assert evm.amount_base == 1_500_000_000_000_000_000
    assert btc.chain.family == "BTC"
    assert btc.asset.decimals == 8
    assert btc.amount_base == 150_000_000


def test_live_evm_and_btc_traces_fail_closed_without_fixture_fallback() -> None:
    evm_result = run_trace(
        TraceSeed(
            address="0x0000000000000000000000000000000000000000",
            payment_ts_ms=1,
            amount_base=1,
            payment_txid="1" * 64,
            chain=ChainRef("EVM", "ethereum", 1),
            asset=AssetRef("ETH", None, 18),
        ),
        TraceParams(trace_mode="live"),
    )
    btc_result = run_trace(
        TraceSeed(
            address="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080",
            payment_ts_ms=1,
            amount_base=1,
            payment_txid="2" * 64,
            chain=ChainRef("BTC", "mainnet"),
            asset=AssetRef("BTC", None, 8),
        ),
        TraceParams(trace_mode="live"),
    )

    assert evm_result["terminal"]["kind"] == "unsupported_chain"
    assert evm_result["outcome"]["creates_finding"] is False
    assert evm_result["hops"] == []
    assert btc_result["terminal"]["kind"] == "unsupported_chain"
    assert btc_result["outcome"]["creates_finding"] is False
    assert btc_result["hops"] == []


def test_live_evm_trace_expands_beyond_first_frontier_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "TRINETRA_ENABLE_LIVE_EVM": "true",
        "ETHERSCAN_API_KEY": "test-key",
        "TRINETRA_LIVE_EVM_SCHEMA_VERIFIED": "true",
        "TRINETRA_LIVE_EVM_SMOKE_VERIFIED": "true",
        "TRINETRA_LIVE_EVM_TRACE_VERIFIED": "true",
    }.items():
        monkeypatch.setenv(name, value)

    seed_address = "0x2222222222222222222222222222222222222222"
    hop_one = "0x3333333333333333333333333333333333333333"
    hop_one_sibling = "0x5555555555555555555555555555555555555555"
    hop_two = "0x4444444444444444444444444444444444444444"
    hop_two_sibling = "0x6666666666666666666666666666666666666666"
    seed_txid = "0x" + "1" * 64
    seed_ts_s = 1_789_337_235

    def tx_row(
        *,
        txid: str,
        source: str,
        destination: str,
        value: int,
        ts_s: int,
        index: int = 0,
    ) -> dict:
        return {
            "txid": txid,
            "status": {
                "confirmed": True,
                "block_time": ts_s,
                "block_height": 100 + index,
                "transaction_index": index,
            },
            "vin": [{"prevout": {"scriptpubkey_address": source}}],
            "vout": [
                {
                    "scriptpubkey_address": destination,
                    "value": value,
                    "token": "ETH",
                    "decimals": 18,
                    "asset_id": "ETH",
                }
            ],
            "symbol": "ETH",
            "decimals": 18,
            "asset_id": "ETH",
        }

    seed_tx = tx_row(
        txid=seed_txid,
        source="0x1111111111111111111111111111111111111111",
        destination=seed_address,
        value=1_000,
        ts_s=seed_ts_s,
    )
    first_hop = tx_row(
        txid="0x" + "2" * 64,
        source=seed_address,
        destination=hop_one,
        value=900,
        ts_s=seed_ts_s + 10,
        index=1,
    )
    second_hop = tx_row(
        txid="0x" + "3" * 64,
        source=hop_one,
        destination=hop_two,
        value=800,
        ts_s=seed_ts_s + 20,
        index=2,
    )
    first_hop_sibling = tx_row(
        txid="0x" + "4" * 64,
        source=seed_address,
        destination=hop_one_sibling,
        value=100,
        ts_s=seed_ts_s + 30,
        index=3,
    )
    second_hop_sibling = tx_row(
        txid="0x" + "5" * 64,
        source=hop_one_sibling,
        destination=hop_two_sibling,
        value=90,
        ts_s=seed_ts_s + 40,
        index=4,
    )

    monkeypatch.setattr(evm, "resolve_transaction", lambda *_args, **_kwargs: seed_tx)
    monkeypatch.setattr(
        evm,
        "fetch_address_transactions",
        lambda address, **_kwargs: {
            seed_address.lower(): [seed_tx, first_hop, first_hop_sibling],
            hop_one.lower(): [second_hop],
            hop_one_sibling.lower(): [second_hop_sibling],
            hop_two.lower(): [],
            hop_two_sibling.lower(): [],
        }.get(str(address).lower(), []),
    )

    result = run_trace(
        TraceSeed(
            address=seed_address,
            payment_ts_ms=seed_ts_s * 1000,
            amount_base=1_000,
            payment_txid=seed_txid,
            chain=ChainRef("EVM", "ethereum", 1),
            asset=AssetRef("ETH", None, 18),
        ),
        TraceParams(max_depth=3, trace_mode="live", breadth_cap=10, address_budget=10),
    )

    assert result["chain"]["family"] == "EVM"
    hop_addresses = [hop["address"] for hop in result["hops"]]
    assert hop_one in hop_addresses
    assert hop_one_sibling in hop_addresses
    assert hop_two in hop_addresses
    assert hop_two_sibling in hop_addresses
    assert result["hops"][0]["frontier_state"] == "expanded"
    assert {hop["hop"] for hop in result["hops"]} >= {1, 2}
    assert result["terminal"]["addresses_queried"] >= 3
