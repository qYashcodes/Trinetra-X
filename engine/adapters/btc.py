from __future__ import annotations

import os

from app.engine_bridge import ChainRef, explorer_url


BTC_MAINNET = ChainRef("BTC", "mainnet")


class BtcAdapterUnavailableError(RuntimeError):
    pass


def adapter_status() -> dict[str, object]:
    return {
        "family": "BTC",
        "enabled": False,
        "configured_endpoint": bool((os.getenv("ESPLORA_BASE_URL") or "").strip()),
        "required_gate": "TRINETRA_BTC_ADAPTER_VERIFIED",
        "reason": "Bitcoin outpoint tracing is explicitly unsupported until UTXO gates pass.",
    }


def fetch_history(_address: str) -> list[dict]:
    raise BtcAdapterUnavailableError(adapter_status()["reason"])


def address_url(address: str) -> str:
    return explorer_url("address", address, BTC_MAINNET)
