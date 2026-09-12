from __future__ import annotations

from app.engine_bridge import ChainRef, explorer_url


def matches(address: str) -> bool:
    return address.startswith("T") and 26 <= len(address) <= 41


def fetch(address: str, *_args, **_kwargs) -> list[dict]:
    raise RuntimeError("Live TronGrid fetching is not configured in this fixture build.")


def normalise(raw: dict) -> dict:
    return {
        "txid": raw["transaction_id"],
        "ts_ms": int(raw["block_timestamp"]),
        "source": raw["from"],
        "destination": raw["to"],
        "amount_base": int(raw["value"]),
        "token": raw.get("token_info", {}).get("symbol", "USDT"),
        "decimals": int(raw.get("token_info", {}).get("decimals", 6)),
    }


def address_url(address: str) -> str:
    return explorer_url("address", address, ChainRef("TRON", "mainnet"))
