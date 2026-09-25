from __future__ import annotations

from engine.adapters import evm


def test_evm_configured_chains_defaults_to_ethereum(monkeypatch) -> None:
    monkeypatch.delenv("TRINETRA_EVM_CHAIN_IDS", raising=False)

    chains = evm.configured_chains()

    assert [chain.chain_id for chain in chains] == [1]


def test_evm_normalise_token_row_preserves_decimals_and_ms_timestamp() -> None:
    row = {
        "hash": "0x" + "a" * 64,
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x2222222222222222222222222222222222222222",
        "contractAddress": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "value": "1234567",
        "tokenSymbol": "USDT",
        "tokenDecimal": "6",
        "blockNumber": "123",
        "transactionIndex": "5",
        "timeStamp": "1789337235",
    }

    normalized_tx = evm._normalize_token_row(row)
    assert normalized_tx is not None
    transfer = evm.normalise(normalized_tx)

    assert transfer["txid"] == row["hash"]
    assert transfer["ts_ms"] == 1_789_337_235_000
    assert transfer["source"] == row["from"]
    assert transfer["destination"] == row["to"]
    assert transfer["amount_base"] == 1_234_567
    assert transfer["token"] == "USDT"
    assert transfer["decimals"] == 6
    assert transfer["contract"] == row["contractAddress"].lower()


def test_evm_normalise_native_row_uses_wei_base_units() -> None:
    row = {
        "hash": "0x" + "b" * 64,
        "from": "0x3333333333333333333333333333333333333333",
        "to": "0x4444444444444444444444444444444444444444",
        "value": "1500000000000000000",
        "blockNumber": "456",
        "transactionIndex": "2",
        "timeStamp": "1789337000",
    }

    normalized_tx = evm._normalize_native_row(row, symbol="ETH")
    assert normalized_tx is not None
    transfer = evm.normalise(normalized_tx)

    assert transfer["amount_base"] == 1_500_000_000_000_000_000
    assert transfer["token"] == "ETH"
    assert transfer["decimals"] == 18
    assert transfer["contract"] is None


def test_evm_allows_empty_token_history_response() -> None:
    evm._validate_response(
        {"status": "0", "message": "NOTOK", "result": "No transactions found"}
    )
