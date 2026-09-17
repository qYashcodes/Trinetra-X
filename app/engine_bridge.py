from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR
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
    ack_no: str | None = None


@dataclass(frozen=True)
class TraceParams:
    max_depth: int = 5
    time_window_hours: int = 8
    value_floor_share: Decimal = Decimal("0.02")
    breadth_cap: int = 3
    address_budget: int = 60
    strategy: Literal["dominant_fund_flow", "value_weighted"] = "dominant_fund_flow"
    trace_mode: Literal["auto", "fixture", "live"] = "auto"
    include_unconfirmed: bool = False


@dataclass(frozen=True)
class ChainActivity:
    chain: ChainRef
    active: bool
    last_seen_ms: int | None
    transfer_count: int
    basis: str


@dataclass(frozen=True)
class TraceResult:
    status: Literal["complete", "failed", "unsupported", "incomplete", "custody_candidate"]
    case_stage: str
    chain: dict
    asset: dict
    terminal: dict
    events: list[dict]
    started_ts_ms: int
    closed_ts_ms: int
    creates_finding: bool
    seed_match: dict


TraceEvent = dict

EVM_CHAINS = [
    ChainRef("EVM", "ethereum", 1),
    ChainRef("EVM", "bnb-smart-chain", 56),
    ChainRef("EVM", "polygon", 137),
    ChainRef("EVM", "base", 8453),
    ChainRef("EVM", "arbitrum-one", 42161),
]

CUSTODY_TERMINALS = {"vasp_deposit", "verified_custody"}
CASE_STAGE_BY_TERMINAL = {
    "vasp_deposit": "custody_found",
    "verified_custody": "custody_found",
    "custody_candidate": "custody_candidate",
    "live_seed_verified": "trace_incomplete",
    "unsupported_chain": "trace_unsupported",
    "invalid_seed": "trace_failed",
    "seed_mismatch": "trace_failed",
    "provider_error": "trace_failed",
    "stationary_funds": "stationary_observed",
    "depth_exhausted": "trace_incomplete",
    "trail_dissipated": "trace_incomplete",
    "bridge": "trace_incomplete",
    "mixer": "trace_incomplete",
    "privacy_boundary": "trace_incomplete",
    "unknown_operation": "trace_incomplete",
    "error": "trace_failed",
}


def engine_mode() -> Literal["fixture", "live"]:
    mode = os.getenv("TRINETRA_MODE", "fixture").lower()
    return "live" if mode == "live" else "fixture"


def _effective_engine_mode(params: TraceParams | None = None) -> Literal["fixture", "live"]:
    if params and params.trace_mode in {"fixture", "live"}:
        return params.trace_mode
    return engine_mode()


def terminal_creates_finding(terminal: dict) -> bool:
    return (
        terminal.get("kind") in CUSTODY_TERMINALS
        and bool(terminal.get("deposit_address"))
        and terminal.get("amount_credited_base") is not None
    )


def case_stage_for_terminal(terminal: dict) -> str:
    return CASE_STAGE_BY_TERMINAL.get(str(terminal.get("kind", "")), "trace_incomplete")


def trace_cache_identity(seed: TraceSeed, params: TraceParams) -> str:
    mode = _effective_engine_mode(params)
    engine_version = _engine_version_label(params)
    coverage = _coverage_label(params)
    return sha256_json(
        {
            "seed": _seed_cache_json(seed),
            "params": _params_json(params),
            "engine_mode": mode,
            "engine_version": engine_version,
            "schema": "trinetra.snapshot/2",
            "source_revision": "fixture-2026-09-12",
            "registry_revision": "fixture-2026-09-12",
            "coverage": coverage,
        }
    )


def _engine_version_label(params: TraceParams | None = None) -> str:
    if _effective_engine_mode(params) == "fixture":
        return "engine-v1.0-fixture"
    if _live_tron_trace_enabled():
        return "engine-v1.3-live-tron-trace"
    return "engine-v1.1-live-tron-seed" if _live_tron_provider_enabled() else "engine-v1.0-live-gated"


def _coverage_label(params: TraceParams | None = None) -> str:
    if _effective_engine_mode(params) == "fixture":
        return "fixture-canonical-seed-only"
    if _live_tron_trace_enabled():
        return "live-tron-bounded-trace"
    return "live-tron-seed-verification" if _live_tron_provider_enabled() else "live-gated"


def _live_tron_provider_enabled() -> bool:
    try:
        from app.services.feature_flags import feature_flags

        return bool(feature_flags()["live_tron_provider"]["enabled"])
    except Exception:
        return False


def _live_tron_trace_enabled() -> bool:
    try:
        from app.services.feature_flags import feature_flags

        return bool(feature_flags()["live_tron_trace"]["enabled"])
    except Exception:
        return False


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
    yield from run_trace_result(seed, params).events


def run_trace_result(seed: TraceSeed, params: TraceParams) -> TraceResult:
    case = demo_case()
    family = detect_chain(seed.address)

    if _effective_engine_mode(params) == "live":
        return _live_trace_result(seed, params, family=family, case=case)

    terminal = _fixture_seed_terminal(seed, family, case)
    if terminal:
        return _closed_result(seed, params, terminal=terminal, family=family, case=case)

    events = _fixture_events(case)
    terminal = case["terminal"]
    return TraceResult(
        status="complete",
        case_stage=case_stage_for_terminal(terminal),
        chain=case["case"]["chain"],
        asset=case["case"]["asset"],
        terminal=terminal,
        events=events,
        started_ts_ms=case["case"]["victim_payment_ts_ms"],
        closed_ts_ms=case["trace_closed_ts_ms"],
        creates_finding=terminal_creates_finding(terminal),
        seed_match={"matched": True, "txid": case["case"]["payment_txid"], "delta_bp": 0},
    )


def _fixture_events(case: dict) -> list[TraceEvent]:
    events: list[TraceEvent] = []

    events.append(
        {
        "type": "source",
        "data": {
            "name": "fixture-ledger",
            "status": "cached",
            "detail": "Deterministic seed-38 TRON transfer fixture",
        },
        }
    )
    events.append(
        {
        "type": "source",
        "data": {
            "name": "attribution-registry",
            "status": "cached",
            "detail": "Fixture labels with provenance markers",
        },
        }
    )
    for hop in case["dominant_path"]:
        events.append({"type": "work", "data": {"message": f"Scoring outbound transfers at hop {hop['hop']}"}})
        events.append(
            {
            "type": "hop",
            "data": {
                **hop,
                "chain": case["case"]["chain"],
                "asset": case["case"]["asset"],
                "classification": hop["class"],
            },
            }
        )
        for branch in case["parked"]:
            if branch["from_hop"] == hop["hop"]:
                events.append({"type": "parked", "data": branch})
        for dust in case["dust"]:
            if dust["from_hop"] == hop["hop"]:
                events.append({"type": "dust", "data": dust})
        events.append(
            {
            "type": "stats",
            "data": {
                "addresses_visited": hop["hop"] + 1,
                "transfers_read": min((hop["hop"] + 1) * 136, case["fixture_counts"]["transfers"]),
                "retained_share_bp": hop["share_bp"],
                "branches_parked": len([b for b in case["parked"] if b["from_hop"] <= hop["hop"]]),
            },
            }
        )
    terminal = case["terminal"]
    events.append(
        {
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
    )
    events.append({"type": "bridge_candidate", "data": _bridge_candidate()})
    events.append({"type": "terminal", "data": terminal})
    events.append(
        {
        "type": "done",
        "data": {
            "snapshot_id": None,
            "sha256": None,
            "closed_ts": case["trace_closed_ts_ms"],
        },
        }
    )
    return events


def run_trace(seed: TraceSeed, params: TraceParams) -> dict:
    case = demo_case()
    trace_result = run_trace_result(seed, params)
    events = trace_result.events
    terminal = trace_result.terminal
    started_ts = trace_result.started_ts_ms
    closed_ts = trace_result.closed_ts_ms
    resolved_txid = trace_result.seed_match.get("txid")
    resolved_ts = trace_result.seed_match.get("ts_ms")
    resolved_amount = trace_result.seed_match.get("amount_base")
    supplied_txid = (
        resolved_txid
        if resolved_txid is not None
        else seed.payment_txid
        if seed.payment_txid is not None
        else case["case"]["payment_txid"]
    )
    supplied_ts = (
        resolved_ts
        if resolved_ts is not None
        else seed.payment_ts_ms
        if seed.payment_ts_ms is not None
        else case["case"]["victim_payment_ts_ms"]
    )
    supplied_amount = (
        resolved_amount
        if resolved_amount is not None
        else seed.amount_base
        if seed.amount_base is not None
        else case["case"]["amount_reported_base"]
    )
    result = {
        "schema": "trinetra.snapshot/2",
        "case": {
            "ack_no": seed.ack_no or case["case"]["ack_no"],
            "reported_address": seed.address,
            "payment_txid": supplied_txid,
            "payment_ts": supplied_ts,
            "amount_reported_base": supplied_amount,
        },
        "params": _params_json(params),
        "engine": {
            "mode": _effective_engine_mode(params),
            "version": _engine_version_label(params),
            "registry_revision": "fixture-2026-09-12",
        },
        "chain": trace_result.chain,
        "asset": trace_result.asset,
        "seed_match": trace_result.seed_match,
        "hops": [event["data"] for event in events if event["type"] == "hop"],
        "parked": [event["data"] for event in events if event["type"] == "parked"],
        "dust": [event["data"] for event in events if event["type"] == "dust"],
        "bridge_candidates": [event["data"] for event in events if event["type"] == "bridge_candidate"],
        "unconfirmed_observations": [
            event["data"] for event in events if event["type"] == "unconfirmed"
        ],
        "classifications": {
            hop["address"]: classify(hop["address"], ChainRef("TRON", "mainnet"))
            for hop in case["dominant_path"]
        }
        if trace_result.creates_finding
        else {},
        "terminal": terminal,
        "stats": _stats_from_events(events, case, trace_result.creates_finding),
        "started_ts": started_ts,
        "closed_ts": closed_ts,
        "continuation": None,
        "case_link_evidence": linked_cases(terminal.get("deposit_address", ""), [])
        if terminal.get("deposit_address")
        else [],
        "fee_accounting": {"amount_base": 0, "asset": trace_result.asset.get("symbol", "unknown")},
        "cache_identity": trace_cache_identity(seed, params),
        "events": events,
        "outcome": {
            "status": trace_result.status,
            "stage": trace_result.case_stage,
            "creates_finding": trace_result.creates_finding,
        },
    }
    result["sha256"] = sha256_json(result)
    return result


def classify(address: str, chain: ChainRef | None = None) -> dict:
    case = demo_case()
    if chain and chain.family != "TRON":
        return _unknown_classification(address)
    deposit = case["terminal"]["deposit_address"]
    if address == deposit:
        vasp = resolve_vasp(address, chain or ChainRef("TRON", "mainnet"))
        if not vasp:
            return _unknown_classification(address)
        return {
            "address": address,
            "class": "exchange_deposit_address",
            "posterior": None,
            "probability_enabled": False,
            "score_kind": "fixture_evidence_band",
            "band": "high",
            "fingerprint": case["classification_fingerprint"],
            "typology": "consolidation",
            "evidence_class": "service_role",
            "vasp": vasp,
        }
    hop = next((item for item in case["dominant_path"] if item["address"] == address), None)
    if hop:
        return {
            "address": address,
            "class": hop["class"],
            "posterior": None,
            "probability_enabled": False,
            "score_kind": "fixture_evidence_band",
            "band": hop["band"],
            "fingerprint": [],
            "typology": hop["typology"],
            "evidence_class": "fund_exposure",
            "vasp": None,
        }
    return _unknown_classification(address)


def resolve_vasp(address: str, chain: ChainRef | None = None) -> dict | None:
    case = demo_case()
    if chain and chain.family != "TRON":
        return None
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


def trace_params_from_json(
    raw: dict | None,
    *,
    trace_mode: Literal["auto", "fixture", "live"] | None = None,
) -> TraceParams:
    values = dict(raw or {})
    mode = trace_mode or str(values.get("trace_mode") or "auto")
    if mode not in {"auto", "fixture", "live"}:
        raise ValueError("Unsupported trace mode.")
    strategy = str(values.get("strategy") or "dominant_fund_flow")
    if strategy not in {"dominant_fund_flow", "value_weighted"}:
        raise ValueError("Unsupported trace strategy.")
    return TraceParams(
        max_depth=int(values.get("max_depth", 5)),
        time_window_hours=int(values.get("time_window_hours", 8)),
        value_floor_share=Decimal(str(values.get("value_floor_share", "0.02"))),
        breadth_cap=int(values.get("breadth_cap", 3)),
        address_budget=int(values.get("address_budget", 60)),
        strategy=strategy,
        trace_mode=mode,
        include_unconfirmed=bool(values.get("include_unconfirmed", False)),
    )


def _seed_cache_json(seed: TraceSeed) -> dict:
    return {
        "ack_no": seed.ack_no,
        "address": seed.address,
        "payment_ts_ms": seed.payment_ts_ms,
        "amount_base": seed.amount_base,
        "payment_txid": seed.payment_txid,
        "chain": asdict(seed.chain) if seed.chain else None,
        "asset": asdict(seed.asset) if seed.asset else None,
    }


def _unknown_classification(address: str) -> dict:
    return {
        "address": address,
        "class": "unknown",
        "posterior": None,
        "probability_enabled": False,
        "score_kind": "not_scored",
        "band": "low",
        "fingerprint": [],
        "typology": "unknown",
        "evidence_class": "unknown",
        "vasp": None,
    }


def _fixture_seed_terminal(seed: TraceSeed, family: str, case: dict) -> dict | None:
    fixture_case = case["case"]
    if family == "unsupported":
        return {
            "kind": "invalid_seed",
            "reason": "address_encoding",
            "family": family,
            "note": "The supplied seed is not a supported TRON, EVM or Bitcoin address.",
        }
    if family != "TRON":
        return {
            "kind": "unsupported_chain",
            "family": family,
            "note": f"{family} address detected; adapter requires live configuration.",
        }
    if seed.chain and (
        seed.chain.family != fixture_case["chain"]["family"]
        or seed.chain.network != fixture_case["chain"]["network"]
    ):
        return {
            "kind": "seed_mismatch",
            "reason": "chain_network",
            "family": family,
            "note": "Fixture mode accepts only the declared TRON mainnet fixture seed.",
        }
    if seed.asset and (
        seed.asset.symbol != fixture_case["asset"]["symbol"]
        or (
            seed.asset.contract is not None
            and seed.asset.contract != fixture_case["asset"]["contract"]
        )
        or seed.asset.decimals != fixture_case["asset"]["decimals"]
    ):
        return {
            "kind": "seed_mismatch",
            "reason": "asset_identity",
            "family": family,
            "note": "Fixture mode accepts only the declared USDT-TRC20 asset.",
        }
    if seed.address != fixture_case["reported_address"]:
        return {
            "kind": "seed_mismatch",
            "reason": "fixture_seed",
            "family": family,
            "note": "The TRON address is not the declared fixture seed; no demo path was replayed.",
        }
    if seed.amount_base is not None and seed.amount_base <= 0:
        return {
            "kind": "invalid_seed",
            "reason": "amount",
            "family": family,
            "supplied_amount_base": seed.amount_base,
            "note": "Seed amount must be a positive integer base-unit value.",
        }
    if seed.payment_ts_ms is not None and seed.payment_ts_ms <= 0:
        return {
            "kind": "invalid_seed",
            "reason": "timestamp",
            "family": family,
            "supplied_payment_ts_ms": seed.payment_ts_ms,
            "note": "Seed timestamp must be a positive UTC epoch millisecond value.",
        }
    if seed.amount_base is not None and seed.amount_base != fixture_case["amount_reported_base"]:
        return {
            "kind": "seed_mismatch",
            "reason": "amount",
            "family": family,
            "note": "Supplied amount does not match the declared fixture payment.",
        }
    if seed.payment_ts_ms is not None and seed.payment_ts_ms != fixture_case["victim_payment_ts_ms"]:
        return {
            "kind": "seed_mismatch",
            "reason": "timestamp",
            "family": family,
            "note": "Supplied timestamp does not match the declared fixture payment.",
        }
    if seed.payment_txid is not None and seed.payment_txid != fixture_case["payment_txid"]:
        return {
            "kind": "seed_mismatch",
            "reason": "payment_txid",
            "family": family,
            "note": "Supplied payment transaction does not match the declared fixture payment.",
        }
    return None


def _live_trace_result(
    seed: TraceSeed,
    params: TraceParams,
    *,
    family: str,
    case: dict,
) -> TraceResult:
    validation_terminal = _live_seed_terminal(seed, family)
    missing_required_seed = validation_terminal and validation_terminal.get("reason") in {
        "amount_required",
        "timestamp_required",
        "payment_txid_required",
    }
    if validation_terminal and not missing_required_seed:
        return _closed_result(
            seed,
            params,
            terminal=validation_terminal,
            family=family,
            case=case,
        )

    from app.services.feature_flags import feature_flags

    flags = feature_flags()
    live_flag = flags["live_tron_provider"]
    live_trace_flag = flags["live_tron_trace"]
    if not live_flag["enabled"]:
        return _closed_result(
            seed,
            params,
            terminal={
                "kind": "provider_error",
                "reason": "live_gate_unmet",
                "family": family,
                "note": live_flag["blocked_reason"],
            },
            family=family,
            case=case,
        )
    if validation_terminal:
        return _closed_result(
            seed,
            params,
            terminal=validation_terminal,
            family=family,
            case=case,
        )

    from engine.adapters import tron

    amount_base = int(seed.amount_base)
    window_ms = params.time_window_hours * 60 * 60 * 1000
    contract_address = (
        seed.asset.contract
        if seed.asset and seed.asset.contract
        else tron.TRON_MAINNET_USDT
    )
    try:
        if seed.payment_txid is not None:
            payment_txid = str(seed.payment_txid)
            seed_event = tron.verify_seed_transfer(
                txid=payment_txid,
                recipient=seed.address,
                amount_base=amount_base,
                contract_address=contract_address,
            )
            fetch_kwargs: dict[str, object] = {
                "contract_address": contract_address,
                "order_by": "block_timestamp,desc",
            }
            if seed.payment_ts_ms is not None:
                fetch_kwargs["min_timestamp"] = max(0, int(seed.payment_ts_ms) - window_ms)
                fetch_kwargs["max_timestamp"] = int(seed.payment_ts_ms) + window_ms
            transfers = tron.fetch_trc20_transfers(seed.address, **fetch_kwargs)
        else:
            payment_txid = ""
            payment_ts_ms = int(seed.payment_ts_ms)
            transfers = tron.fetch_trc20_transfers(
                seed.address,
                min_timestamp=payment_ts_ms,
                max_timestamp=payment_ts_ms,
                contract_address=contract_address,
                only_to=True,
            )
        normalised = [tron.normalise(row) for row in transfers]
        seed_rows = [
            row
            for row in normalised
            if str(row.get("destination") or "") == seed.address
            and _int_or_zero(row.get("amount_base")) == amount_base
            and (not payment_txid or str(row.get("txid") or "") == payment_txid)
            and (
                seed.payment_ts_ms is None
                or _int_or_zero(row.get("ts_ms")) == int(seed.payment_ts_ms)
            )
        ]
        if not seed_rows:
            return _closed_result(
                seed,
                params,
                terminal={
                    "kind": "seed_mismatch",
                    "reason": "confirmed_transfer_not_found",
                    "family": family,
                    "note": (
                        "No confirmed USDT transfer matched the supplied address, amount, "
                        "transaction hash and timestamp fields."
                    ),
                },
                family=family,
                case=case,
            )
        resolved_seed = sorted(
            seed_rows,
            key=lambda row: (int(row["ts_ms"]), str(row["txid"])),
            reverse=True,
        )[0]
        payment_txid = str(resolved_seed["txid"])
        payment_ts_ms = int(resolved_seed["ts_ms"])
        if seed.payment_txid is None:
            seed_event = tron.verify_seed_transfer(
                txid=payment_txid,
                recipient=seed.address,
                amount_base=amount_base,
                contract_address=contract_address,
            )
            transfers = tron.fetch_trc20_transfers(
                seed.address,
                min_timestamp=max(0, payment_ts_ms - window_ms),
                max_timestamp=payment_ts_ms + window_ms,
                contract_address=contract_address,
            )
            normalised = [tron.normalise(row) for row in transfers]
        unconfirmed_events = _live_unconfirmed_events(
            tron,
            seed=seed,
            params=params,
            contract_address=contract_address,
            payment_ts_ms=payment_ts_ms,
            window_ms=window_ms,
        )
    except (tron.ProviderConfigurationError, tron.ProviderResponseError) as exc:
        reason = getattr(exc, "error_kind", "provider_configuration")
        return _closed_result(
            seed,
            params,
            terminal={
                "kind": "provider_error",
                "reason": reason,
                "family": family,
                "note": str(exc),
            },
            family=family,
            case=case,
        )

    if live_trace_flag["enabled"]:
        try:
            return _live_tron_bounded_trace_result(
                seed,
                params,
                family=family,
                case=case,
                contract_address=contract_address,
                seed_event=seed_event,
                seed_transfers=normalised,
                payment_txid=payment_txid,
                payment_ts_ms=payment_ts_ms,
                amount_base=amount_base,
                window_ms=window_ms,
                unconfirmed_events=unconfirmed_events,
            )
        except (tron.ProviderConfigurationError, tron.ProviderResponseError) as exc:
            reason = getattr(exc, "error_kind", "provider_configuration")
            return _closed_result(
                seed,
                params,
                terminal={
                    "kind": "provider_error",
                    "reason": reason,
                    "family": family,
                    "note": str(exc),
                },
                family=family,
                case=case,
            )

    outgoing = _live_outgoing_rows(
        normalised,
        source_address=seed.address,
        min_ts_ms=payment_ts_ms,
    )
    attributed_edges, stationary_amount_base = _attribute_live_outgoing_edges(
        outgoing,
        amount_base=amount_base,
    )
    scheduled_edges, deferred_edges = _schedule_live_edges(
        attributed_edges,
        params=params,
        amount_base=amount_base,
    )
    terminal = {
        "kind": "depth_exhausted" if scheduled_edges else "stationary_funds" if not outgoing else "trail_dissipated",
        "reason": "live_tron_seed_verified",
        "family": family,
        "note": (
            "The TRON seed transfer was verified through the configured provider; "
            "full live traversal remains gated, so no custody finding is created."
        ),
        "verified_seed_txid": payment_txid,
        "verified_amount_base": amount_base,
        "provider": "trongrid",
        "outgoing_observed": len(outgoing),
        "scheduled_frontier": len(scheduled_edges),
        "deferred_frontier": len(deferred_edges),
        "stationary_amount_base": stationary_amount_base,
    }
    closed_ts = now_ms()
    events: list[TraceEvent] = [
        {
            "type": "source",
            "data": {
                "name": "trongrid",
                "status": "verified",
                "detail": "Credentialed live TRON TRC-20 history and seed-event lookup.",
            },
        },
        {
            "type": "work",
            "data": {
                "message": "Verified supplied TRC-20 seed transfer",
                "txid": payment_txid,
                "contract_address": contract_address,
                "event_name": seed_event.get("event_name") or seed_event.get("event"),
            },
        },
        {
            "type": "stats",
            "data": {
                "addresses_visited": 1,
                "transfers_read": len(normalised),
                "retained_share_bp": _share_bp(stationary_amount_base, amount_base),
                "branches_parked": len(deferred_edges),
                "frontier_queued": len(scheduled_edges),
            },
        },
        *[
            {
                "type": "hop",
                "data": _live_hop_event(
                    edge,
                    rank=index + 1,
                    seed_address=seed.address,
                    seed_amount_base=amount_base,
                ),
            }
            for index, edge in enumerate(scheduled_edges)
        ],
        *[
            {"type": "parked", "data": _live_parked_event(edge)}
            for edge in deferred_edges
        ],
        *unconfirmed_events,
        {"type": "terminal", "data": terminal},
        {
            "type": "done",
            "data": {"snapshot_id": None, "sha256": None, "closed_ts": closed_ts},
        },
    ]
    return TraceResult(
        status="incomplete",
        case_stage=case_stage_for_terminal(terminal),
        chain=_chain_json_for_seed(seed, family),
        asset=_asset_json_for_seed(seed, case),
        terminal=terminal,
        events=events,
        started_ts_ms=payment_ts_ms,
        closed_ts_ms=closed_ts,
        creates_finding=False,
        seed_match={
            "matched": True,
            "txid": payment_txid,
            "ts_ms": payment_ts_ms,
            "amount_base": amount_base,
            "retrieval_ts_ms": resolved_seed.get("retrieval_ts_ms"),
            "provider": "trongrid",
            "resolution": "transaction_hash" if seed.payment_txid is not None else "address_tuple",
        },
    )


def _live_unconfirmed_events(
    tron_module: object,
    *,
    seed: TraceSeed,
    params: TraceParams,
    contract_address: str,
    payment_ts_ms: int,
    window_ms: int,
) -> list[TraceEvent]:
    if not params.include_unconfirmed:
        return []
    try:
        rows = tron_module.fetch_unconfirmed_trc20_transfers(
            seed.address,
            min_timestamp=payment_ts_ms,
            max_timestamp=payment_ts_ms + window_ms,
            contract_address=contract_address,
        )
        normalized = [tron_module.normalise(row) for row in rows]
    except (tron_module.ProviderConfigurationError, tron_module.ProviderResponseError) as exc:
        return [
            {
                "type": "unconfirmed",
                "data": {
                    "status": "unavailable",
                    "warning": (
                        "Unconfirmed observations are leads only and never enter attribution, "
                        "canonical evidence or terminal decisions."
                    ),
                    "error_kind": exc.__class__.__name__,
                },
            }
        ]
    warning = (
        "Unconfirmed observation only. It does not enter attribution, canonical evidence "
        "or terminal decisions and may disappear or change."
    )
    return [
        {
            "type": "unconfirmed",
            "data": {
                "status": "observed",
                "warning": warning,
                "txid": row.get("txid"),
                "source_address": row.get("source"),
                "destination_address": row.get("destination"),
                "amount_base": row.get("amount_base"),
                "ts_ms": row.get("ts_ms"),
                "retrieval_ts_ms": row.get("retrieval_ts_ms"),
                "finality": "unconfirmed",
            },
        }
        for row in normalized
    ]


def _live_tron_bounded_trace_result(
    seed: TraceSeed,
    params: TraceParams,
    *,
    family: str,
    case: dict,
    contract_address: str,
    seed_event: dict,
    seed_transfers: list[dict],
    payment_txid: str,
    payment_ts_ms: int,
    amount_base: int,
    window_ms: int,
    unconfirmed_events: list[TraceEvent],
) -> TraceResult:
    from engine.adapters import tron

    trace_window_end_ms = payment_ts_ms + window_ms
    work_events: list[TraceEvent] = []
    hop_events: list[TraceEvent] = []
    parked_events: list[TraceEvent] = []
    pending: list[dict] = []
    hops_seen = 0
    transfers_read = len(seed_transfers)
    addresses_queried = 1
    stationary_total = 0
    observed_outgoing = 0

    root_visited = {seed.address.lower()}
    root_outgoing = _live_outgoing_rows(
        seed_transfers,
        source_address=seed.address,
        min_ts_ms=payment_ts_ms,
    )
    root_edges, root_stationary = _attribute_live_outgoing_edges(
        root_outgoing,
        amount_base=amount_base,
    )
    stationary_total += root_stationary
    observed_outgoing += len(root_outgoing)
    root_scheduled, root_deferred = _schedule_live_edges(
        root_edges,
        params=params,
        amount_base=amount_base,
        next_depth=1,
        visited_addresses=root_visited,
        time_window_end_ms=trace_window_end_ms,
        address_budget_remaining=max(0, params.address_budget - addresses_queried),
    )
    pending.extend(
        _prepare_live_child_edges(
            root_scheduled,
            depth=1,
            visited_addresses=root_visited,
        )
    )
    parked_events.extend(
        {"type": "parked", "data": _live_parked_event(edge)}
        for edge in _prepare_live_child_edges(
            root_deferred,
            depth=1,
            visited_addresses=root_visited,
        )
    )

    while pending:
        pending = _rank_live_pending(pending, strategy=params.strategy)
        pending_view = [
            {
                "rank": index + 1,
                "event_ref": item.get("stable_id"),
                "destination": item.get("destination"),
                "attributed_base": _int_or_zero(item.get("attributed_base")),
                "depth": int(item.get("depth") or 0),
            }
            for index, item in enumerate(pending)
        ]
        edge = pending.pop(0)
        edge["global_pending_decision"] = {
            "strategy": params.strategy,
            "selected_rank": 1,
            "pending_count": len(pending_view),
            "ranking": pending_view,
        }
        depth = int(edge.get("depth") or 1)
        destination = str(edge.get("destination") or "")
        if not destination:
            edge["deferral_reason"] = "address_budget"
            parked_events.append({"type": "parked", "data": _live_parked_event(edge)})
            continue

        if depth >= params.max_depth:
            edge["frontier_state"] = "queued"
            hops_seen += 1
            hop_events.append(
                {
                    "type": "hop",
                    "data": _live_hop_event(
                        edge,
                        rank=hops_seen,
                        seed_address=str(edge.get("source") or ""),
                        seed_amount_base=amount_base,
                    ),
                }
            )
            continue

        if addresses_queried >= params.address_budget:
            edge["deferral_reason"] = "address_budget"
            parked_events.append({"type": "parked", "data": _live_parked_event(edge)})
            continue

        edge["frontier_state"] = "expanded"
        hops_seen += 1
        hop_events.append(
            {
                "type": "hop",
                "data": _live_hop_event(
                    edge,
                    rank=hops_seen,
                    seed_address=str(edge.get("source") or ""),
                    seed_amount_base=amount_base,
                ),
            }
        )

        edge_ts = edge.get("ts_ms")
        min_timestamp = int(edge_ts) if edge_ts is not None else payment_ts_ms
        work_events.append(
            {
                "type": "work",
                "data": {
                    "message": "Expanding live TRON frontier address",
                    "address": destination,
                    "depth": depth,
                    "min_timestamp": min_timestamp,
                },
            }
        )
        try:
            child_rows = [
                tron.normalise(row)
                for row in tron.fetch_trc20_transfers(
                    destination,
                    min_timestamp=min_timestamp,
                    max_timestamp=trace_window_end_ms,
                    contract_address=contract_address,
                )
            ]
        except tron.ProviderResponseError as exc:
            retry_after_ms = getattr(exc, "retry_after_ms", None)
            retry_delay_ms = max(
                60_000,
                int(retry_after_ms) if retry_after_ms is not None else 0,
            )
            edge["frontier_state"] = "deferred"
            edge["deferral_reason"] = "provider_backoff"
            edge["provider"] = "trongrid"
            edge["retry_attempts"] = 1
            edge["provider_retry_after_ms"] = retry_after_ms
            edge["next_retry_ts_ms"] = now_ms() + retry_delay_ms
            hop_events[-1]["data"]["frontier_state"] = "deferred"
            hop_events[-1]["data"]["deferral_reason"] = "provider_backoff"
            hop_events[-1]["data"]["next_retry_ts_ms"] = edge["next_retry_ts_ms"]
            parked_events.append({"type": "parked", "data": _live_parked_event(edge)})
            continue
        transfers_read += len(child_rows)
        addresses_queried += 1
        child_outgoing = _live_outgoing_rows(
            child_rows,
            source_address=destination,
            min_ts_ms=min_timestamp,
        )
        observed_outgoing += len(child_outgoing)
        child_edges, child_stationary = _attribute_live_outgoing_edges(
            child_outgoing,
            amount_base=_int_or_zero(edge.get("attributed_base")),
        )
        stationary_total += child_stationary
        child_visited = set(edge.get("_visited_addresses") or root_visited)
        child_visited.add(destination.lower())
        child_scheduled, child_deferred = _schedule_live_edges(
            child_edges,
            params=params,
            amount_base=_int_or_zero(edge.get("attributed_base")),
            next_depth=depth + 1,
            visited_addresses=child_visited,
            time_window_end_ms=trace_window_end_ms,
            address_budget_remaining=max(0, params.address_budget - addresses_queried),
        )
        pending.extend(
            _prepare_live_child_edges(
                child_scheduled,
                depth=depth + 1,
                visited_addresses=child_visited,
            )
        )
        parked_events.extend(
            {"type": "parked", "data": _live_parked_event(row)}
            for row in _prepare_live_child_edges(
                child_deferred,
                depth=depth + 1,
                visited_addresses=child_visited,
            )
        )

    queued_frontier = sum(
        1
        for event in hop_events
        if event["data"].get("frontier_state") == "queued"
    )
    terminal_kind = (
        "depth_exhausted"
        if queued_frontier or parked_events
        else "stationary_funds"
        if stationary_total > 0
        else "trail_dissipated"
    )
    terminal = {
        "kind": terminal_kind,
        "reason": "live_tron_trace_bounded",
        "family": family,
        "note": (
            "The TRON seed transfer and bounded multi-hop USDT traversal were verified "
            "through the configured provider; custody findings remain disabled without "
            "provider-certified attribution."
        ),
        "verified_seed_txid": payment_txid,
        "verified_amount_base": amount_base,
        "provider": "trongrid",
        "addresses_queried": addresses_queried,
        "outgoing_observed": observed_outgoing,
        "scheduled_frontier": queued_frontier,
        "deferred_frontier": len(parked_events),
        "stationary_amount_base": stationary_total,
        "trace_window_end_ms": trace_window_end_ms,
        "custody_evaluation": "disabled_without_provider_attribution",
    }
    closed_ts = now_ms()
    events: list[TraceEvent] = [
        {
            "type": "source",
            "data": {
                "name": "trongrid",
                "status": "verified",
                "detail": "Credentialed live TRON TRC-20 seed verification and bounded multi-hop traversal.",
            },
        },
        {
            "type": "work",
            "data": {
                "message": "Verified supplied TRC-20 seed transfer",
                "txid": payment_txid,
                "contract_address": contract_address,
                "event_name": seed_event.get("event_name") or seed_event.get("event"),
            },
        },
        *work_events,
        *hop_events,
        *parked_events,
        {
            "type": "stats",
            "data": {
                "addresses_visited": addresses_queried,
                "transfers_read": transfers_read,
                "retained_share_bp": _share_bp(stationary_total, amount_base),
                "branches_parked": len(parked_events),
                "frontier_queued": queued_frontier,
                "expanded_hops": sum(
                    1
                    for event in hop_events
                    if event["data"].get("frontier_state") == "expanded"
                ),
            },
        },
        *unconfirmed_events,
        {"type": "terminal", "data": terminal},
        {
            "type": "done",
            "data": {"snapshot_id": None, "sha256": None, "closed_ts": closed_ts},
        },
    ]
    return TraceResult(
        status="incomplete",
        case_stage=case_stage_for_terminal(terminal),
        chain=_chain_json_for_seed(seed, family),
        asset=_asset_json_for_seed(seed, case),
        terminal=terminal,
        events=events,
        started_ts_ms=payment_ts_ms,
        closed_ts_ms=closed_ts,
        creates_finding=False,
        seed_match={
            "matched": True,
            "txid": payment_txid,
            "ts_ms": payment_ts_ms,
            "amount_base": amount_base,
            "retrieval_ts_ms": next(
                (
                    row.get("retrieval_ts_ms")
                    for row in seed_transfers
                    if str(row.get("txid")) == payment_txid
                ),
                None,
            ),
            "provider": "trongrid",
            "trace": "bounded_multi_hop",
            "resolution": "transaction_hash" if seed.payment_txid is not None else "address_tuple",
        },
    )


def _prepare_live_child_edges(
    edges: list[dict],
    *,
    depth: int,
    visited_addresses: set[str],
) -> list[dict]:
    prepared: list[dict] = []
    for edge in edges:
        item = dict(edge)
        item["depth"] = depth
        item["_visited_addresses"] = set(visited_addresses)
        prepared.append(item)
    return prepared


def _rank_live_pending(
    pending: list[dict],
    *,
    strategy: Literal["dominant_fund_flow", "value_weighted"],
) -> list[dict]:
    if strategy == "value_weighted":
        return sorted(
            pending,
            key=lambda edge: (
                -_int_or_zero(edge.get("attributed_base")),
                int(edge.get("depth") or 0),
                int(edge.get("event_order") or 0),
                str(edge.get("stable_id") or ""),
            ),
        )
    return sorted(
        pending,
        key=lambda edge: (
            int(edge.get("depth") or 0),
            -_int_or_zero(edge.get("attributed_base")),
            int(edge.get("event_order") or 0),
            str(edge.get("stable_id") or ""),
        ),
    )


def _live_outgoing_rows(
    transfers: list[dict],
    *,
    source_address: str,
    min_ts_ms: int,
) -> list[dict]:
    return sorted(
        [
            row
            for row in transfers
            if str(row.get("source", "")).lower() == source_address.lower()
            and _int_or_zero(row.get("ts_ms")) >= min_ts_ms
        ],
        key=lambda row: (
            _int_or_zero(row.get("ts_ms")),
            _str_or_empty(row.get("txid")),
            _int_or_zero(row.get("event_index")),
        ),
    )


def _attribute_live_outgoing_edges(
    outgoing: list[dict],
    *,
    amount_base: int,
) -> tuple[list[dict], int]:
    from app.services.allocation import allocate_proportional

    outgoing_total = sum(max(0, _int_or_zero(row.get("amount_base"))) for row in outgoing)
    balance_base = max(amount_base, outgoing_total)
    attributed_base = amount_base
    residual_numerator = 0
    attributed_edges: list[dict] = []

    for index, row in enumerate(outgoing):
        outgoing_base = max(0, _int_or_zero(row.get("amount_base")))
        edge = dict(row)
        edge["event_order"] = index
        edge["stable_id"] = _live_edge_ref(edge, index)
        if balance_base <= 0 or attributed_base <= 0:
            edge["attributed_base"] = 0
            edge["residual_numerator"] = residual_numerator
            edge["allocation"] = {
                "policy": "integer_proportional",
                "version": "trinetra.allocation/1",
                "incoming_attributed_base": attributed_base,
                "observed_outgoing_base": outgoing_base,
                "denominator_base": balance_base,
                "numerator_base": None,
                "outgoing_attributed_base": 0,
                "residual_before": residual_numerator,
                "residual_numerator": residual_numerator,
                "remaining_balance_base": balance_base,
                "remaining_attributed_base": attributed_base,
                "initial_balance_basis": {
                    "incoming_attributed_base": amount_base,
                    "observed_outgoing_total_base": outgoing_total,
                    "denominator_base": max(amount_base, outgoing_total),
                },
            }
            attributed_edges.append(edge)
            continue
        balance_before = balance_base
        attributed_before = attributed_base
        residual_before = residual_numerator
        numerator = outgoing_base * attributed_before + residual_before
        step = allocate_proportional(
            balance_base=balance_before,
            attributed_base=attributed_before,
            outgoing_base=outgoing_base,
            residual_numerator=residual_before,
        )
        edge["attributed_base"] = step.outgoing_attributed_base
        edge["residual_numerator"] = step.residual_numerator
        edge["allocation"] = {
            "policy": "integer_proportional",
            "version": "trinetra.allocation/1",
            "incoming_attributed_base": attributed_before,
            "observed_outgoing_base": outgoing_base,
            "denominator_base": balance_before,
            "numerator_base": numerator,
            "outgoing_attributed_base": step.outgoing_attributed_base,
            "residual_before": residual_before,
            "residual_numerator": step.residual_numerator,
            "remaining_balance_base": step.remaining_balance_base,
            "remaining_attributed_base": step.remaining_attributed_base,
            "initial_balance_basis": {
                "incoming_attributed_base": amount_base,
                "observed_outgoing_total_base": outgoing_total,
                "denominator_base": max(amount_base, outgoing_total),
            },
        }
        attributed_edges.append(edge)
        balance_base = step.remaining_balance_base
        attributed_base = step.remaining_attributed_base
        residual_numerator = step.residual_numerator

    return attributed_edges, attributed_base


def _schedule_live_edges(
    attributed_edges: list[dict],
    *,
    params: TraceParams,
    amount_base: int,
    next_depth: int = 1,
    visited_addresses: set[str] | None = None,
    time_window_end_ms: int | None = None,
    address_budget_remaining: int | None = None,
) -> tuple[list[dict], list[dict]]:
    from app.services.allocation import FrontierCandidate, schedule_candidates

    if not attributed_edges:
        return [], []

    visited = {address.lower() for address in visited_addresses or set()}
    budget_remaining = (
        params.address_budget if address_budget_remaining is None else address_budget_remaining
    )
    value_floor_base = int(
        (Decimal(amount_base) * params.value_floor_share).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )
    immediate_deferrals: dict[str, str] = {}
    candidates: list[FrontierCandidate] = []
    for edge in attributed_edges:
        attributed = int(edge["attributed_base"])
        destination = str(edge.get("destination") or "")
        edge_ts = edge.get("ts_ms")
        if next_depth > params.max_depth:
            immediate_deferrals[edge["stable_id"]] = "depth_budget"
        elif destination and destination.lower() in visited:
            immediate_deferrals[edge["stable_id"]] = "cycle_detected"
        elif (
            time_window_end_ms is not None
            and edge_ts is not None
            and int(edge_ts) > time_window_end_ms
        ):
            immediate_deferrals[edge["stable_id"]] = "time_window"
        elif attributed <= 0 or attributed < value_floor_base:
            immediate_deferrals[edge["stable_id"]] = "value_floor"
        else:
            candidates.append(
                FrontierCandidate(
                    stable_id=edge["stable_id"],
                    attributed_base=attributed,
                    event_order=int(edge["event_order"]),
                )
            )
    scheduled, breadth_deferred = schedule_candidates(
        candidates,
        strategy=params.strategy,
        breadth_cap=min(params.breadth_cap, max(0, budget_remaining)),
    )
    scheduled_ids = {item.stable_id for item in scheduled}
    breadth_deferred_ids = {item.stable_id for item in breadth_deferred}

    queued_edges: list[dict] = []
    deferred_edges: list[dict] = []
    eligible_rank = {
        candidate.stable_id: index + 1
        for index, candidate in enumerate(
            sorted(
                candidates,
                key=lambda candidate: (
                    -candidate.attributed_base,
                    candidate.event_order,
                    candidate.stable_id,
                ),
            )
        )
    }
    for edge in attributed_edges:
        attributed = int(edge["attributed_base"])
        item = dict(edge)
        if item["stable_id"] in immediate_deferrals:
            item["deferral_reason"] = immediate_deferrals[item["stable_id"]]
            deferred_edges.append(item)
        elif item["stable_id"] in scheduled_ids:
            item["frontier_state"] = "queued"
            queued_edges.append(item)
        elif item["stable_id"] in breadth_deferred_ids:
            item["deferral_reason"] = "address_budget" if budget_remaining <= 0 else "breadth_cap"
            deferred_edges.append(item)
        elif budget_remaining <= 0 and attributed > 0:
            item["deferral_reason"] = "address_budget"
            deferred_edges.append(item)
        else:
            item["deferral_reason"] = "address_budget"
            deferred_edges.append(item)
        item["scheduling"] = {
            "strategy": params.strategy,
            "local_candidate_rank": eligible_rank.get(item["stable_id"]),
            "eligible_candidate_count": len(candidates),
            "breadth_cap": params.breadth_cap,
            "address_budget_remaining": budget_remaining,
            "decision": item.get("frontier_state") or "deferred",
            "deferral_reason": item.get("deferral_reason"),
        }
    return queued_edges, deferred_edges


def _live_hop_event(
    edge: dict,
    *,
    rank: int,
    seed_address: str,
    seed_amount_base: int,
) -> dict:
    attributed = _int_or_zero(edge.get("attributed_base"))
    txid = edge.get("txid")
    return {
        "hop": int(edge.get("depth") or rank),
        "trace_rank": rank,
        "address": edge.get("destination"),
        "source_address": seed_address,
        "value_base": attributed,
        "observed_amount_base": _int_or_zero(edge.get("amount_base")),
        "share_bp": _share_bp(attributed, seed_amount_base),
        "ts_ms": edge.get("ts_ms"),
        "event_index": edge.get("event_index"),
        "block": edge.get("block"),
        "retrieval_ts_ms": edge.get("retrieval_ts_ms"),
        "raw_sha256": edge.get("raw_sha256"),
        "provider_request_ref": edge.get("provider_request_ref"),
        "txids": [txid] if txid is not None else [],
        "class": "live_frontier_candidate",
        "band": "unscored",
        "typology": "observed_outgoing_transfer",
        "classification": "frontier",
        "event_ref": edge["stable_id"],
        "frontier_state": edge.get("frontier_state") or "queued",
        "allocation": dict(edge.get("allocation") or {
            "policy": "integer_proportional",
            "residual_numerator": edge.get("residual_numerator", 0),
        }),
        "scheduling": dict(edge.get("scheduling") or {}),
        "global_pending_decision": dict(edge.get("global_pending_decision") or {}),
    }


def _live_parked_event(edge: dict) -> dict:
    attributed = _int_or_zero(edge.get("attributed_base"))
    return {
        "branch_id": edge["stable_id"],
        "address": edge.get("destination"),
        "source_address": edge.get("source"),
        "from_hop": max(0, int(edge.get("depth") or 1) - 1),
        "value_base": attributed,
        "observed_amount_base": _int_or_zero(edge.get("amount_base")),
        "txid": edge.get("txid"),
        "ts_ms": edge.get("ts_ms"),
        "event_index": edge.get("event_index"),
        "block": edge.get("block"),
        "retrieval_ts_ms": edge.get("retrieval_ts_ms"),
        "raw_sha256": edge.get("raw_sha256"),
        "provider_request_ref": edge.get("provider_request_ref"),
        "reason": edge.get("deferral_reason"),
        "deferral_reason": edge.get("deferral_reason"),
        "next_retry_ts_ms": edge.get("next_retry_ts_ms"),
        "retry_attempts": edge.get("retry_attempts"),
        "provider_retry_after_ms": edge.get("provider_retry_after_ms"),
        "provider": edge.get("provider"),
        "event_ref": edge["stable_id"],
        "allocation": dict(edge.get("allocation") or {
            "policy": "integer_proportional",
            "residual_numerator": edge.get("residual_numerator", 0),
        }),
        "scheduling": dict(edge.get("scheduling") or {}),
    }


def _live_edge_ref(edge: dict, index: int) -> str:
    txid_value = edge.get("txid")
    txid = str(txid_value) if txid_value is not None else f"unknown-{index}"
    event_index = edge.get("event_index")
    suffix = event_index if event_index is not None else index
    return f"live:tron:{txid}:{suffix}"


def _int_or_zero(value: object) -> int:
    return 0 if value is None else int(value)


def _str_or_empty(value: object) -> str:
    return "" if value is None else str(value)


def _share_bp(part: int, whole: int) -> int:
    if whole <= 0:
        return 0
    return max(0, min(10000, (int(part) * 10000) // int(whole)))


def _live_seed_terminal(seed: TraceSeed, family: str) -> dict | None:
    if family == "unsupported":
        return {
            "kind": "invalid_seed",
            "reason": "address_encoding",
            "family": family,
            "note": "The supplied seed is not a supported TRON, EVM or Bitcoin address.",
        }
    if family != "TRON":
        return {
            "kind": "unsupported_chain",
            "family": family,
            "note": f"{family} tracing remains unavailable until its live evidence gates pass.",
        }
    if seed.chain and (seed.chain.family != "TRON" or seed.chain.network != "mainnet"):
        return {
            "kind": "unsupported_chain",
            "family": family,
            "note": "Live TRON tracing is currently gated to TRON mainnet.",
        }
    if seed.asset and (
        seed.asset.symbol != "USDT"
        or seed.asset.decimals != 6
        or (
            seed.asset.contract is not None
            and seed.asset.contract != "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        )
    ):
        return {
            "kind": "unsupported_chain",
            "family": family,
            "note": "Live TRON tracing is currently gated to USDT-TRC20.",
        }
    if seed.amount_base is None:
        return {
            "kind": "invalid_seed",
            "reason": "amount_required",
            "family": family,
            "note": "Live TRON seed verification requires the payment amount in base units.",
        }
    if seed.amount_base <= 0:
        return {
            "kind": "invalid_seed",
            "reason": "amount",
            "family": family,
            "supplied_amount_base": seed.amount_base,
            "note": "Seed amount must be a positive integer base-unit value.",
        }
    if seed.payment_ts_ms is None and not seed.payment_txid:
        return {
            "kind": "invalid_seed",
            "reason": "timestamp_required",
            "family": family,
            "note": "An address seed requires the confirmed payment timestamp in UTC milliseconds.",
        }
    if seed.payment_ts_ms is not None and seed.payment_ts_ms <= 0:
        return {
            "kind": "invalid_seed",
            "reason": "timestamp",
            "family": family,
            "supplied_payment_ts_ms": seed.payment_ts_ms,
            "note": "Seed timestamp must be a positive UTC epoch millisecond value.",
        }
    if seed.payment_txid is not None and not re.fullmatch(r"[a-fA-F0-9]{64}", seed.payment_txid):
        return {
            "kind": "invalid_seed",
            "reason": "payment_txid",
            "family": family,
            "note": "Payment transaction hash must be a 64-character hex string.",
        }
    return None


def _closed_result(
    seed: TraceSeed,
    params: TraceParams,
    *,
    terminal: dict,
    family: str,
    case: dict,
) -> TraceResult:
    closed_ts = now_ms()
    chain = _chain_json_for_seed(seed, family)
    asset = _asset_json_for_seed(seed, case)
    events = [
        {
            "type": "source",
            "data": {
                "name": "trace-engine",
                "status": "closed",
                "detail": "No fixture path replayed for this seed.",
            },
        },
        {
            "type": "terminal",
            "data": terminal,
        },
        {
            "type": "done",
            "data": {"snapshot_id": None, "sha256": None, "closed_ts": closed_ts},
        },
    ]
    status = "unsupported" if terminal.get("kind") == "unsupported_chain" else "failed"
    return TraceResult(
        status=status,
        case_stage=case_stage_for_terminal(terminal),
        chain=chain,
        asset=asset,
        terminal=terminal,
        events=events,
        started_ts_ms=seed.payment_ts_ms if seed.payment_ts_ms is not None else closed_ts,
        closed_ts_ms=closed_ts,
        creates_finding=False,
        seed_match={
            "matched": False,
            "reason": terminal.get("reason") or terminal.get("kind"),
            "txid": seed.payment_txid,
        },
    )


def _chain_json_for_seed(seed: TraceSeed, family: str) -> dict:
    if seed.chain:
        return asdict(seed.chain)
    if family == "TRON":
        return {"family": "TRON", "network": "mainnet", "chain_id": None}
    if family == "EVM":
        return {"family": "EVM", "network": "unknown", "chain_id": None}
    if family == "BTC":
        return {"family": "BTC", "network": "mainnet", "chain_id": None}
    return {"family": "unsupported", "network": "unknown", "chain_id": None}


def _asset_json_for_seed(seed: TraceSeed, case: dict) -> dict:
    if seed.asset:
        return asdict(seed.asset)
    if detect_chain(seed.address) == "TRON":
        return case["case"]["asset"]
    return {"symbol": "unknown", "contract": None, "decimals": 0}


def _stats_from_events(events: list[dict], case: dict, fixture_success: bool) -> dict:
    stats = [event["data"] for event in events if event["type"] == "stats"]
    if stats:
        return stats[-1]
    if fixture_success:
        return {
            "addresses_visited": len(case["dominant_path"]),
            "transfers_read": case["fixture_counts"]["transfers"],
            "retained_share_bp": case["terminal"]["retained_share_bp"],
            "branches_parked": len(case["parked"]),
        }
    return {
        "addresses_visited": 0,
        "transfers_read": 0,
        "retained_share_bp": 0,
        "branches_parked": 0,
    }


def _bridge_candidate() -> dict:
    return {
        "kind": "bridge_candidate",
        "label": "No source-chain bridge exit on dominant path",
        "rank": None,
        "confidence": "none",
        "requires_officer_selection": True,
        "note": "Bridge continuation candidates stay separate from immutable source-chain evidence.",
    }
