from __future__ import annotations

from dataclasses import dataclass

from app.engine_bridge import AssetRef, ChainRef


@dataclass(frozen=True)
class TronResolution:
    chain: ChainRef
    address: str
    txid: str | None
    amount_base: int | None
    ts_ms: int | None
    asset: AssetRef
    provider: str


def resolve_transaction(txid: str) -> list[TronResolution]:
    from engine.adapters import tron

    resolved = tron.resolve_usdt_transfer(txid)
    return [
        TronResolution(
            chain=ChainRef("TRON", "mainnet"),
            address=str(resolved["destination"]),
            txid=str(resolved["txid"]),
            amount_base=int(resolved["amount_base"]),
            ts_ms=int(resolved["ts_ms"]),
            asset=AssetRef("USDT", str(resolved.get("contract") or tron.TRON_MAINNET_USDT), 6),
            provider="trongrid",
        )
    ]

