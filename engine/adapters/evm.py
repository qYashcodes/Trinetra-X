from __future__ import annotations

import os

from app.engine_bridge import EVM_CHAINS, ChainRef, explorer_url


class EvmAdapterUnavailableError(RuntimeError):
    pass


def configured_chains() -> list[ChainRef]:
    return EVM_CHAINS


def adapter_status() -> dict[str, object]:
    return {
        "family": "EVM",
        "enabled": False,
        "configured_key": bool((os.getenv("ETHERSCAN_API_KEY") or "").strip()),
        "required_gate": "TRINETRA_EVM_ADAPTER_VERIFIED",
        "reason": "EVM token tracing is explicitly unsupported until adapter evidence gates pass.",
    }


def fetch_token_transfers(_address: str, _chain: ChainRef) -> list[dict]:
    raise EvmAdapterUnavailableError(adapter_status()["reason"])


def address_url(address: str, chain: ChainRef | None = None) -> str:
    return explorer_url("address", address, chain or EVM_CHAINS[0])
