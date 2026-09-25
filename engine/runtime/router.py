from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.engine_bridge import AssetRef, ChainRef, detect_chain
from app.services.time import IST


class SeedResolutionError(ValueError):
    pass


class AmbiguousSeedError(SeedResolutionError):
    pass


class UnsupportedSeedError(SeedResolutionError):
    pass


@dataclass(frozen=True)
class ResolvedSeed:
    seed_kind: Literal["address", "txid"]
    chain: ChainRef
    asset: AssetRef
    address: str
    payment_txid: str | None
    amount_base: int
    payment_ts_ms: int
    provider: str
    resolution: str


def _parse_amount_base(value: str, *, decimals: int) -> int:
    try:
        amount = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise SeedResolutionError("Enter a valid amount.") from exc
    scaled = amount * (Decimal(10) ** decimals)
    if amount <= 0 or scaled != scaled.to_integral_value():
        raise SeedResolutionError(
            f"Amount must be positive with no more than {decimals} decimal places."
        )
    return int(scaled)


def _parse_ist_timestamp(value: str | None) -> int | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SeedResolutionError("Enter a valid payment date and time in IST.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return int(parsed.timestamp() * 1000)


def resolve_live_seed(
    *,
    seed_kind: str,
    seed_value: str | None,
    amount: str | None = None,
    payment_ts_ist: str | None = None,
    address_seed_value: str | None = None,
) -> ResolvedSeed:
    normalized_seed = (seed_value or "").strip()
    if seed_kind not in {"address", "txid"}:
        raise SeedResolutionError("Select address or transaction hash as the seed type.")
    if seed_kind == "txid":
        return _resolve_txid_seed(normalized_seed)
    return _resolve_address_seed(
        (address_seed_value or normalized_seed).strip(),
        amount=amount,
        payment_ts_ist=payment_ts_ist,
    )


def _resolve_txid_seed(txid: str) -> ResolvedSeed:
    if not txid:
        raise SeedResolutionError("Enter a transaction hash.")
    if not re.fullmatch(r"(0x)?[a-fA-F0-9]{64}", txid):
        raise SeedResolutionError("Transaction hash must be 64 hexadecimal characters.")
    evm_prefixed = txid.lower().startswith("0x")
    canonical = txid[2:] if txid.lower().startswith("0x") else txid
    matches = []
    provider_errors: list[str] = []

    from engine.runtime import btc_engine, evm_engine, tron_engine

    resolvers = (
        (evm_engine.resolve_transaction, txid),
        (tron_engine.resolve_transaction, canonical),
        (btc_engine.resolve_transaction, canonical),
    )
    if evm_prefixed:
        resolvers = ((evm_engine.resolve_transaction, txid),)

    for resolver, resolver_txid in resolvers:
        try:
            matches.extend(resolver(resolver_txid))
        except Exception as exc:
            provider_errors.append(str(exc))

    if len(matches) > 1:
        families = sorted({match.chain.family for match in matches})
        raise AmbiguousSeedError(
            "Transaction hash matched multiple configured chains: " + ", ".join(families)
        )
    if not matches:
        if provider_errors:
            raise SeedResolutionError(provider_errors[0])
        raise UnsupportedSeedError("No configured provider confirmed this transaction hash.")
    match = matches[0]
    if match.amount_base is None or match.amount_base <= 0:
        raise SeedResolutionError("Resolved transaction amount must be positive.")
    if match.ts_ms is None or match.ts_ms <= 0:
        raise SeedResolutionError("Resolved transaction timestamp must be positive.")
    return ResolvedSeed(
        seed_kind="txid",
        chain=match.chain,
        asset=match.asset,
        address=match.address,
        payment_txid=match.txid,
        amount_base=match.amount_base,
        payment_ts_ms=match.ts_ms,
        provider=match.provider,
        resolution="transaction_hash",
    )


def _resolve_address_seed(
    address: str,
    *,
    amount: str | None,
    payment_ts_ist: str | None,
) -> ResolvedSeed:
    if not address:
        raise SeedResolutionError("Enter a wallet address.")
    family = detect_chain(address)
    if family == "unsupported":
        raise UnsupportedSeedError("The supplied seed is not a supported TRON, EVM or Bitcoin address.")
    if not amount:
        raise SeedResolutionError("An address seed requires the confirmed amount.")
    payment_ts_ms = _parse_ist_timestamp(payment_ts_ist)
    if payment_ts_ms is None:
        raise SeedResolutionError("An address seed requires the confirmed payment date and time in IST.")
    if family == "TRON":
        from engine.adapters import tron

        amount_base = _parse_amount_base(amount, decimals=6)
        return ResolvedSeed(
            seed_kind="address",
            chain=ChainRef("TRON", "mainnet"),
            asset=AssetRef("USDT", tron.TRON_MAINNET_USDT, 6),
            address=address,
            payment_txid=None,
            amount_base=amount_base,
            payment_ts_ms=payment_ts_ms,
            provider="trongrid",
            resolution="address_tuple",
        )
    if family == "EVM":
        amount_base = _parse_amount_base(amount, decimals=18)
        return ResolvedSeed(
            seed_kind="address",
            chain=ChainRef("EVM", "ethereum", 1),
            asset=AssetRef("ETH", None, 18),
            address=address,
            payment_txid=None,
            amount_base=amount_base,
            payment_ts_ms=payment_ts_ms,
            provider="etherscan",
            resolution="address_tuple",
        )
    amount_base = _parse_amount_base(amount, decimals=8)
    return ResolvedSeed(
        seed_kind="address",
        chain=ChainRef("BTC", "mainnet"),
        asset=AssetRef("BTC", None, 8),
        address=address,
        payment_txid=None,
        amount_base=amount_base,
        payment_ts_ms=payment_ts_ms,
        provider="esplora",
        resolution="address_tuple",
    )
