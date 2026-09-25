from __future__ import annotations

from dataclasses import dataclass

from app.engine_bridge import AssetRef, ChainRef, TraceParams, TraceResult, TraceSeed
from app.services.time import now_ms


class BtcTraceUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class BtcResolution:
    chain: ChainRef
    address: str
    txid: str | None
    amount_base: int | None
    ts_ms: int | None
    asset: AssetRef
    provider: str


def resolve_transaction(_txid: str) -> list[BtcResolution]:
    """Return confirmed Bitcoin seed matches.

    Bitcoin tracing needs UTXO/outpoint semantics and remains gated until the
    provider contract and verification tests are in place.
    """

    return []


def run_trace(seed: TraceSeed, params: TraceParams, *, family: str, case: dict) -> TraceResult:
    closed_ts = now_ms()
    terminal = {
        "kind": "unsupported_chain",
        "family": family,
        "note": "Bitcoin outpoint tracing remains unavailable until its UTXO gates pass.",
    }
    return TraceResult(
        status="unsupported",
        case_stage="trace_unsupported",
        chain={"family": "BTC", "network": "mainnet", "chain_id": None},
        asset={
            "symbol": seed.asset.symbol if seed.asset else "BTC",
            "contract": None,
            "decimals": seed.asset.decimals if seed.asset else 8,
        },
        terminal=terminal,
        events=[
            {
                "type": "source",
                "data": {
                    "name": "btc-engine",
                    "status": "closed",
                    "detail": "Bitcoin provider tracing is gated and unavailable.",
                },
            },
            {"type": "terminal", "data": terminal},
            {"type": "done", "data": {"snapshot_id": None, "sha256": None, "closed_ts": closed_ts}},
        ],
        started_ts_ms=seed.payment_ts_ms if seed.payment_ts_ms is not None else closed_ts,
        closed_ts_ms=closed_ts,
        creates_finding=False,
        seed_match={
            "matched": False,
            "reason": "unsupported_chain",
            "txid": seed.payment_txid,
        },
    )

