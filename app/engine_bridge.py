from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Iterator, Literal

from app.services.demo import demo_case
from app.services.hash import sha256_json
from app.services.time import now_ms


@dataclass(frozen=True)
class ChainRef:
    family: Literal["TRON", "EVM", "BTC"]
    network: str
    chain_id: int | None = None


@dataclass(frozen=True)
class AssetRef:
    symbol: str
    contract: str | None
    decimals: int


@dataclass(frozen=True)
class NormalizedTransfer:
    txid: str
    ts_ms: int
    chain: ChainRef
    asset: AssetRef
    source: str
    destination: str
    amount_base: int
    direction: Literal["in", "out"]


@dataclass(frozen=True)
class TraceSeed:
    address: str
    payment_ts_ms: int | None
    amount_base: int | None
    chain: ChainRef | None = None
    asset: AssetRef | None = None
    payment_txid: str | None = None


@dataclass(frozen=True)
class TraceParams:
    max_depth: int = 5
    time_window_hours: int = 8
    value_floor_share: Decimal = Decimal("0.02")
    breadth_cap: int = 3
    address_budget: int = 60
    strategy: Literal["dominant_fund_flow", "value_weighted"] = "dominant_fund_flow"


@dataclass(frozen=True)
class ChainActivity:
    chain: ChainRef
    active: bool
    last_seen_ms: int | None
    transfer_count: int
    basis: str


TraceEvent = dict

EVM_CHAINS = [
    ChainRef("EVM", "ethereum", 1),
    ChainRef("EVM", "bnb-smart-chain", 56),
    ChainRef("EVM", "polygon", 137),
    ChainRef("EVM", "base", 8453),
    ChainRef("EVM", "arbitrum-one", 42161),
]


def engine_mode() -> Literal["fixture", "live"]:
    mode = os.getenv("TRINETRA_MODE", "fixture").lower()
    return "live" if mode == "live" else "fixture"


def detect_chain(seed: str) -> Literal["TRON", "EVM", "BTC", "unsupported"]:
    if re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{25,40}", seed):
        return "TRON"
    if re.fullmatch(r"0x[a-fA-F0-9]{40}", seed):
        return "EVM"
    if re.fullmatch(r"(bc1|[13])[a-zA-HJ-NP-Z0-9]{20,90}", seed):
        return "BTC"
    return "unsupported"


def resolve_chain_activity(seed: str) -> list[ChainActivity]:
    family = detect_chain(seed)
    case = demo_case()["case"]
    if seed == case["reported_address"]:
        return [
            ChainActivity(
                ChainRef("TRON", "mainnet"),
                True,
                case["victim_payment_ts_ms"],
                demo_case()["fixture_counts"]["transfers"],
                "canonical fixture activity",
            )
        ]
    if family == "EVM":
        return [
            ChainActivity(chain, False, None, 0, "provider lookup not configured in fixture mode")
            for chain in EVM_CHAINS
        ]
    if family == "BTC":
        return [
            ChainActivity(
                ChainRef("BTC", "mainnet"),
                False,
                None,
                0,
                "Esplora lookup not configured in fixture mode",
            )
        ]
    return []


def trace(seed: TraceSeed, params: TraceParams) -> Iterator[TraceEvent]:
    family = detect_chain(seed.address)
    if family != "TRON":
        yield {
            "type": "terminal",
            "data": {
                "kind": "unsupported_chain",
                "family": family,
                "note": f"{family} address detected; adapter requires live configuration.",
            },
        }
        yield {"type": "done", "data": {"snapshot_id": None, "sha256": None, "closed_ts": now_ms()}}
        return

    case = demo_case()
    yield {
        "type": "source",
        "data": {
            "name": "fixture-ledger",
            "status": "cached",
            "detail": "Deterministic seed-38 TRON transfer fixture",
        },
    }
    yield {
        "type": "source",
        "data": {
            "name": "attribution-registry",
            "status": "cached",
            "detail": "Fixture labels with provenance markers",
        },
    }
    for hop in case["dominant_path"]:
        yield {"type": "work", "data": {"message": f"Scoring outbound transfers at hop {hop['hop']}"}}
        yield {
            "type": "hop",
            "data": {
                **hop,
                "chain": case["case"]["chain"],
                "asset": case["case"]["asset"],
                "classification": hop["class"],
            },
        }
        for branch in case["parked"]:
            if branch["from_hop"] == hop["hop"]:
                yield {"type": "parked", "data": branch}
        for dust in case["dust"]:
            if dust["from_hop"] == hop["hop"]:
                yield {"type": "dust", "data": dust}
        yield {
            "type": "stats",
            "data": {
                "addresses_visited": hop["hop"] + 1,
                "transfers_read": min((hop["hop"] + 1) * 136, case["fixture_counts"]["transfers"]),
                "retained_share_bp": hop["share_bp"],
                "branches_parked": len([b for b in case["parked"] if b["from_hop"] <= hop["hop"]]),
            },
        }
    terminal = case["terminal"]
    yield {
        "type": "candidate",
        "data": {
            "name": case["entity"]["name"],
            "entity_key": case["entity"]["key"],
            "band": "high",
            "amount_base": terminal["amount_credited_base"],
            "basis": "deposit fingerprint and labelled sweep destination",
            "at_hop": 4,
        },
    }
    yield {"type": "bridge_candidate", "data": _bridge_candidate()}
    yield {"type": "terminal", "data": terminal}
    yield {
        "type": "done",
        "data": {
            "snapshot_id": None,
            "sha256": None,
            "closed_ts": case["trace_closed_ts_ms"],
        },
    }


def run_trace(seed: TraceSeed, params: TraceParams) -> dict:
    case = demo_case()
    events = list(trace(seed, params))
    started_ts = case["case"]["victim_payment_ts_ms"]
    closed_ts = events[-1]["data"]["closed_ts"]
    result = {
        "schema": "trinetra.snapshot/2",
        "case": {
            "ack_no": case["case"]["ack_no"],
            "reported_address": seed.address,
            "payment_txid": seed.payment_txid or case["case"]["payment_txid"],
            "payment_ts": seed.payment_ts_ms or case["case"]["victim_payment_ts_ms"],
            "amount_reported_base": seed.amount_base or case["case"]["amount_reported_base"],
        },
        "params": _params_json(params),
        "engine": {
            "mode": engine_mode(),
            "version": "engine-v1.0-fixture",
            "registry_revision": "fixture-2026-09-12",
        },
        "chain": case["case"]["chain"],
        "asset": case["case"]["asset"],
        "seed_match": {"matched": True, "txid": case["case"]["payment_txid"], "delta_bp": 0},
        "hops": [event["data"] for event in events if event["type"] == "hop"],
        "parked": [event["data"] for event in events if event["type"] == "parked"],
        "dust": [event["data"] for event in events if event["type"] == "dust"],
        "bridge_candidates": [event["data"] for event in events if event["type"] == "bridge_candidate"],
        "classifications": {
            hop["address"]: classify(hop["address"], ChainRef("TRON", "mainnet"))
            for hop in case["dominant_path"]
        },
        "terminal": case["terminal"],
        "stats": {
            "addresses_visited": len(case["dominant_path"]),
            "transfers_read": case["fixture_counts"]["transfers"],
            "retained_share_bp": case["terminal"]["retained_share_bp"],
            "branches_parked": len(case["parked"]),
        },
        "started_ts": started_ts,
        "closed_ts": closed_ts,
        "continuation": None,
        "case_link_evidence": linked_cases(case["terminal"]["deposit_address"], []),
        "fee_accounting": {"amount_base": 0, "asset": case["case"]["asset"]["symbol"]},
    }
    result["sha256"] = sha256_json(result)
    return result


def classify(address: str, chain: ChainRef | None = None) -> dict:
    case = demo_case()
    deposit = case["terminal"]["deposit_address"]
    if address == deposit:
        vasp = resolve_vasp(address, chain or ChainRef("TRON", "mainnet"))
        return {
            "address": address,
            "class": "exchange_deposit_address",
            "posterior": 0.91,
            "band": "high",
            "fingerprint": case["classification_fingerprint"],
            "typology": "consolidation",
            "vasp": vasp,
        }
    hop = next((item for item in case["dominant_path"] if item["address"] == address), None)
    if hop:
        return {
            "address": address,
            "class": hop["class"],
            "posterior": hop["confidence"],
            "band": hop["band"],
            "fingerprint": [],
            "typology": hop["typology"],
            "vasp": None,
        }
    return {
        "address": address,
        "class": "unknown",
        "posterior": 0.1,
        "band": "low",
        "fingerprint": [],
        "typology": "unknown",
        "vasp": None,
    }


def resolve_vasp(address: str, chain: ChainRef | None = None) -> dict | None:
    case = demo_case()
    if address != case["terminal"]["deposit_address"] and address != case["hot_wallet"]["address"]:
        return None
    return {
        "entity_key": case["entity"]["key"],
        "entity_name": case["entity"]["name"],
        "rung": 3 if address == case["terminal"]["deposit_address"] else 1,
        "band": "high",
        "basis": "sweep destination labelled" if address == case["terminal"]["deposit_address"] else "registry label",
        "fiu_ind_reg": case["entity"]["fiu_ind_reg"],
        "fiu_status": case["entity"]["fiu_status"],
        "registry_revision": "fixture-2026-09-12",
    }


def linked_cases(address: str, snapshots: list[dict]) -> list[dict]:
    links = []
    for link in demo_case()["linked_cases"]:
        if address == link["shared_address"]:
            links.append(link)
    for snapshot in snapshots:
        terminal = snapshot.get("terminal", {})
        if terminal.get("deposit_address") == address:
            links.append({"ack_no": snapshot.get("case", {}).get("ack_no"), "basis": "Stored snapshot"})
    return links


def explorer_url(kind: str, value: str, chain: ChainRef | str | None = None) -> str:
    family = chain.family if isinstance(chain, ChainRef) else chain or "TRON"
    if family == "TRON":
        prefix = "transaction" if kind == "tx" else "address"
        return f"https://tronscan.org/#/{prefix}/{value}"
    if family == "BTC":
        return f"https://blockstream.info/{'tx' if kind == 'tx' else 'address'}/{value}"
    return f"https://etherscan.io/{'tx' if kind == 'tx' else 'address'}/{value}"


def _params_json(params: TraceParams) -> dict:
    raw = asdict(params)
    raw["value_floor_share"] = str(params.value_floor_share)
    return raw


def _bridge_candidate() -> dict:
    return {
        "kind": "bridge_candidate",
        "label": "No source-chain bridge exit on dominant path",
        "rank": None,
        "confidence": "none",
        "requires_officer_selection": True,
        "note": "Bridge continuation candidates stay separate from immutable source-chain evidence.",
    }
