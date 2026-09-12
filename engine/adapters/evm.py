from __future__ import annotations

from app.engine_bridge import EVM_CHAINS, ChainRef, explorer_url


def configured_chains() -> list[ChainRef]:
    return EVM_CHAINS


def fetch_token_transfers(_address: str, _chain: ChainRef) -> list[dict]:
    raise RuntimeError("Etherscan V2 fetching requires ETHERSCAN_API_KEY.")


def address_url(address: str, chain: ChainRef | None = None) -> str:
    return explorer_url("address", address, chain or EVM_CHAINS[0])
