from __future__ import annotations

from app.engine_bridge import ChainRef, explorer_url


BTC_MAINNET = ChainRef("BTC", "mainnet")


def fetch_history(_address: str) -> list[dict]:
    raise RuntimeError("Esplora fetching is disabled in fixture mode.")


def address_url(address: str) -> str:
    return explorer_url("address", address, BTC_MAINNET)
