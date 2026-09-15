from __future__ import annotations

import secrets
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.engine_bridge import (
    AssetRef,
    ChainRef,
    TraceParams,
    TraceSeed,
    case_stage_for_terminal,
    run_trace,
    terminal_creates_finding,
    trace_cache_identity,
)
from app.models import (
    AttributedLot,
    CanonicalTraceEvent,
    Case,
    CaseStage,
    CustodyAssertion,
    Finding,
    FrontierItem,
    Notice,
    SourceCoverage,
    TraceEvent,
    TraceSnapshot,
)
from app.services.accounting import ValueOutcome, conservation_report
from app.services.demo import demo_case
from app.services.evidence_store import (
    persist_provider_coverage,
    provider_evidence_scope,
)
from app.services.explainability import trace_explainability
from app.services.hash import sha256_json
from app.services.time import now_ms
from app.settings import settings


def finding_review_specs() -> list[dict]:
    """Return the ordered, explicit review contract used by storage and the UI."""
    return [dict(spec) for spec in demo_case()["finding_review_checks"]]


def initial_finding_review_checks() -> dict[str, bool]:
    return {
        str(spec["key"]): bool(spec.get("default_checked", False))
        for spec in finding_review_specs()
    }


def ensure_finding_review_checks(session: Session, finding: Finding) -> Finding:
    """Migrate legacy positional checks without carrying unrelated assertions forward."""
    expected = initial_finding_review_checks()
    current = dict(finding.review_checks or {})
    if set(current) == set(expected):
        return finding
    finding.review_checks = expected
    session.add(finding)
    session.commit()
    session.refresh(finding)
    return finding


def seed_demo(session: Session) -> Case:
    data = demo_case()
    return case_from_complaint(session, data["case"])


def case_from_complaint(session: Session, record: dict) -> Case:
    existing = session.exec(select(Case).where(Case.ack_no == record["ack_no"])).first()
    if existing:
        return existing
    ts = now_ms()
    asset = record["asset"]
    chain = record["chain"]
    case = Case(
        ack_no=record["ack_no"],
        category=record["category"],
        jurisdiction=record["jurisdiction"],
        filed_ts_ms=record["filed_ts_ms"],
        amount_reported_base=record["amount_reported_base"],
        asset_symbol=asset["symbol"],
        asset_decimals=asset["decimals"],
        chain_family=chain["family"],
        chain_network=chain["network"],
        reported_address=record["reported_address"],
        payment_txid=record.get("payment_txid"),
        payment_ts_ms=record.get("victim_payment_ts_ms"),
        complainant_contact_redacted=record.get("complainant_contact_redacted"),
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def get_or_create_trace(
    session: Session,
    case: Case,
    *,
    trace_mode: Literal["auto", "fixture", "live"] = "auto",
    params: TraceParams | None = None,
    force_new: bool = False,
) -> tuple[TraceSnapshot, Finding | None]:
    seed = _trace_seed_from_case(case)
    params = params or TraceParams(trace_mode=trace_mode)
    if trace_mode != "auto" and params.trace_mode != trace_mode:
        raise ValueError("Trace parameter mode does not match the requested trace mode.")
    expected_cache_identity = trace_cache_identity(seed, params)
    if force_new:
        expected_cache_identity = sha256_json(
            {
                "base_cache_identity": expected_cache_identity,
                "retrace_ref": secrets.token_hex(16),
            }
        )
    else:
        existing = _trace_by_cache_identity(
            session,
            case=case,
            cache_identity=expected_cache_identity,
        )
        if existing:
            return _snapshot_with_optional_finding(session, existing)
    stale_existing = _latest_snapshot(session, case)

    if case.id is None:
        raise ValueError("Case must be persisted before tracing.")
    with provider_evidence_scope(root=settings.var_dir, case_id=case.id) as provider_evidence:
        result = run_trace(seed, params)
    _attach_provider_provenance(
        result,
        provider_evidence.records,
        stale_after_seconds=int(getattr(settings, "data_stale_after_seconds", 900)),
    )
    result["explainability"] = trace_explainability(result)
    result["cache_identity"] = expected_cache_identity
    result.pop("sha256", None)
    result["sha256"] = sha256_json(result)
    snapshot = TraceSnapshot(
        case_id=case.id,
        version=1,
        parent_snapshot_id=stale_existing.id if stale_existing else None,
        status=result.get("outcome", {}).get("status", "complete"),
        cache_identity=expected_cache_identity,
        result_json=result,
        sha256=result["sha256"],
        chain_family=result["chain"]["family"],
        chain_network=result["chain"]["network"],
        asset_symbol=result["asset"]["symbol"],
        asset_decimals=result["asset"]["decimals"],
        bridge_candidate=(result.get("bridge_candidates") or [None])[0],
        case_link_evidence=result["case_link_evidence"],
        started_ts_ms=result["started_ts"],
        closed_ts_ms=result["closed_ts"],
    )
    terminal = result["terminal"]
    finding = None
    try:
        session.add(snapshot)
        session.flush()
        if stale_existing:
            stale_existing.superseded_by_id = snapshot.id
            session.add(stale_existing)

        for seq, event in enumerate(_events_from_result(result), start=1):
            session.add(
                TraceEvent(
                    snapshot_id=snapshot.id,
                    seq=seq,
                    event_type=event["type"],
                    data=event["data"],
                    created_ts_ms=now_ms(),
                )
            )
        _add_evidence_projection(session, case=case, snapshot=snapshot, result=result)
        persist_provider_coverage(
            session,
            provider_evidence.records,
            snapshot_id=snapshot.id,
        )
        if terminal_creates_finding(terminal):
            review_checks = initial_finding_review_checks()
            finding = Finding(
                case_id=case.id,
                snapshot_id=snapshot.id,
                terminal_kind=terminal["kind"],
                custodian_key=terminal.get("custodian_key"),
                deposit_address=terminal.get("deposit_address"),
                amount_credited_base=terminal.get("amount_credited_base"),
                review_checks=review_checks,
                created_ts_ms=now_ms(),
            )
            session.add(finding)

        case.stage = CaseStage(case_stage_for_terminal(terminal))
        case.updated_ts_ms = now_ms()
        session.add(case)
        session.commit()
    except IntegrityError:
        session.rollback()
        concurrent = _trace_by_cache_identity(
            session,
            case=case,
            cache_identity=expected_cache_identity,
        )
        if concurrent:
            return _snapshot_with_optional_finding(session, concurrent)
        raise
    except Exception:
        session.rollback()
        raise
    session.refresh(snapshot)
    if finding:
        session.refresh(finding)
    return snapshot, finding


def _attach_provider_provenance(
    result: dict,
    records: list[dict],
    *,
    stale_after_seconds: int,
) -> None:
    mode = str(result.get("engine", {}).get("mode") or "fixture")
    if not records:
        result["provider_evidence"] = []
        result["data_freshness"] = {
            "mode": mode,
            "chain_data_as_of_ms": None,
            "oldest_retrieval_ts_ms": None,
            "stale_after_ms": max(0, stale_after_seconds) * 1000,
            "provider_request_count": 0,
        }
        return
    retrievals = [int(record["retrieval_ts_ms"]) for record in records]
    newest = max(retrievals)
    result["provider_evidence"] = [
        {
            "request_ref": record.get("request_ref"),
            "provider": record.get("provider"),
            "endpoint": record.get("endpoint"),
            "query": dict(record.get("query") or {}),
            "retrieval_ts_ms": int(record["retrieval_ts_ms"]),
            "raw_sha256": record.get("raw_sha256"),
            "schema_status": record.get("schema_status"),
            "provider_watermark": dict(record.get("provider_watermark") or {}),
        }
        for record in records
    ]
    result["data_freshness"] = {
        "mode": mode,
        "chain_data_as_of_ms": newest,
        "oldest_retrieval_ts_ms": min(retrievals),
        "stale_after_ms": max(0, stale_after_seconds) * 1000,
        "provider_request_count": len(records),
    }
    for collection_name in ("hops", "parked"):
        for item in result.get(collection_name) or []:
            if item.get("retrieval_ts_ms") is None:
                item["retrieval_ts_ms"] = newest


def _trace_by_cache_identity(
    session: Session,
    *,
    case: Case,
    cache_identity: str,
) -> TraceSnapshot | None:
    row = session.exec(
        select(TraceSnapshot)
        .where(TraceSnapshot.case_id == case.id)
        .where(TraceSnapshot.cache_identity == cache_identity)
        .order_by(TraceSnapshot.id.desc())
    ).first()
    if row:
        return row
    legacy_rows = session.exec(
        select(TraceSnapshot)
        .where(TraceSnapshot.case_id == case.id)
        .order_by(TraceSnapshot.id.desc())
    ).all()
    for legacy in legacy_rows:
        if legacy.result_json.get("cache_identity") == cache_identity:
            legacy.cache_identity = cache_identity
            session.add(legacy)
            session.commit()
            session.refresh(legacy)
            return legacy
    return None


def _latest_snapshot(session: Session, case: Case) -> TraceSnapshot | None:
    return session.exec(
        select(TraceSnapshot)
        .where(TraceSnapshot.case_id == case.id)
        .order_by(TraceSnapshot.id.desc())
    ).first()


def _snapshot_with_optional_finding(
    session: Session,
    snapshot: TraceSnapshot,
) -> tuple[TraceSnapshot, Finding | None]:
    finding = session.exec(select(Finding).where(Finding.snapshot_id == snapshot.id)).first()
    if finding:
        return snapshot, ensure_finding_review_checks(session, finding)
    return snapshot, None


def prepare_notice(session: Session, finding: Finding, created_by_pis: str) -> Notice:
    existing = session.exec(select(Notice).where(Notice.finding_id == finding.id)).first()
    if existing:
        return existing
    data = demo_case()
    notice = Notice(
        case_id=finding.case_id,
        finding_id=finding.id,
        notice_no=data["notice"]["notice_no"],
        deadline_hours=data["notice"]["deadline_hours"],
        created_by_pis=created_by_pis,
        created_ts_ms=now_ms(),
    )
    case = session.get(Case, finding.case_id)
    if case:
        case.stage = CaseStage.notice_draft
        case.updated_ts_ms = now_ms()
        session.add(case)
    session.add(notice)
    session.commit()
    session.refresh(notice)
    return notice


def _events_from_result(result: dict) -> list[dict]:
    if result.get("events"):
        return list(result["events"])
    events = []
    for hop in result["hops"]:
        events.append({"type": "hop", "data": hop})
    for parked in result["parked"]:
        events.append({"type": "parked", "data": parked})
    for dust in result["dust"]:
        events.append({"type": "dust", "data": dust})
    events.append(
        {"type": "candidate", "data": {"name": "Coinsphere Global Pte Ltd", "band": "high"}}
    )
    events.append({"type": "terminal", "data": result["terminal"]})
    events.append(
        {"type": "done", "data": {"sha256": result["sha256"], "closed_ts": result["closed_ts"]}}
    )
    return events


def _add_evidence_projection(
    session: Session,
    *,
    case: Case,
    snapshot: TraceSnapshot,
    result: dict,
) -> None:
    snapshot_id = _require_snapshot_id(snapshot)
    created_ts_ms = now_ms()
    asset_identifier = _asset_identifier(result)
    hops = list(result.get("hops") or [])
    parked = list(result.get("parked") or [])
    terminal = result.get("terminal") or {}
    source_events = [
        event for event in _events_from_result(result) if event.get("type") == "source"
    ]

    for index, hop in enumerate(hops):
        session.add(
            CanonicalTraceEvent(
                case_id=case.id,
                snapshot_id=snapshot_id,
                chain_family=result["chain"]["family"],
                chain_network=result["chain"]["network"],
                block_hash=hop.get("block_hash"),
                block_height=(
                    int(hop["block"])
                    if hop.get("block") is not None
                    else None
                ),
                txid=_first_txid(hop, fallback=f"fixture-hop-{hop.get('hop', index)}"),
                tx_index=index,
                event_index=hop.get("event_index") if hop.get("event_index") is not None else 0,
                output_index=None,
                asset_identifier=asset_identifier,
                source_address=hop.get("source_address")
                or (hops[index - 1]["address"] if index > 0 else None),
                destination_address=hop.get("address"),
                amount_base=_int_or_zero(hop.get("value_base")),
                success=True,
                finality="fixture_final"
                if result.get("engine", {}).get("mode") == "fixture"
                else "provider_confirmed",
                evidence_ref=_hop_evidence_ref(snapshot_id, hop, index),
                raw_sha256=str(hop.get("raw_sha256") or sha256_json(hop)),
                observed_ts_ms=hop.get("ts_ms"),
                created_ts_ms=created_ts_ms,
            )
        )

    _add_source_coverage(
        session,
        case=case,
        snapshot_id=snapshot_id,
        result=result,
        source_events=source_events,
        created_ts_ms=created_ts_ms,
    )
    _add_trace_lots_and_frontier(
        session,
        case=case,
        snapshot_id=snapshot_id,
        result=result,
        asset_identifier=asset_identifier,
        created_ts_ms=created_ts_ms,
    )
    _add_custody_assertion(
        session,
        case=case,
        snapshot_id=snapshot_id,
        result=result,
        terminal=terminal,
        created_ts_ms=created_ts_ms,
    )


def _add_source_coverage(
    session: Session,
    *,
    case: Case,
    snapshot_id: int,
    result: dict,
    source_events: list[dict],
    created_ts_ms: int,
) -> None:
    for event in source_events:
        data = dict(event.get("data") or {})
        completeness, gaps = _coverage_from_source_status(data.get("status"))
        session.add(
            SourceCoverage(
                case_id=case.id,
                snapshot_id=snapshot_id,
                provider=str(data.get("name") or "trace-engine"),
                chain_family=result["chain"]["family"],
                chain_network=result["chain"]["network"],
                query_range={
                    "kind": "trace_source",
                    "cache_identity": result.get("cache_identity"),
                },
                observed_watermark={
                    "status": data.get("status"),
                    "detail": data.get("detail"),
                },
                retrieval_ts_ms=created_ts_ms,
                completeness=completeness,
                gaps=gaps,
                conflicts=[],
            )
        )

    report = _conservation_for_result(result)
    if report:
        for asset_identifier, totals in sorted(report.items()):
            session.add(
                SourceCoverage(
                    case_id=case.id,
                    snapshot_id=snapshot_id,
                    provider="trace-accounting",
                    chain_family=result["chain"]["family"],
                    chain_network=result["chain"]["network"],
                    query_range={
                        "kind": "per_asset_conservation",
                        "asset_identifier": asset_identifier,
                    },
                    observed_watermark={"totals": dict(totals)},
                    retrieval_ts_ms=created_ts_ms,
                    completeness="balanced",
                    gaps=[],
                    conflicts=[],
                )
            )


def _add_trace_lots_and_frontier(
    session: Session,
    *,
    case: Case,
    snapshot_id: int,
    result: dict,
    asset_identifier: str,
    created_ts_ms: int,
) -> None:
    seed_ref = _seed_event_ref(result)
    terminal = result.get("terminal") or {}
    terminal_amount = terminal.get("amount_credited_base")
    terminal_address = terminal.get("deposit_address")
    if terminal_creates_finding(terminal) and terminal_address and terminal_amount is not None:
        session.add(
            AttributedLot(
                case_id=case.id,
                snapshot_id=snapshot_id,
                seed_event_ref=seed_ref,
                asset_identifier=asset_identifier,
                current_address=terminal_address,
                remaining_amount_base=int(terminal_amount),
                arrival_cursor={"terminal_kind": terminal.get("kind")},
                allocation_policy=str(
                    result.get("params", {}).get("strategy") or "dominant_fund_flow"
                ),
                allocation_version="trinetra.allocation/1",
                ancestry=[
                    {"evidence_ref": _terminal_evidence_ref(snapshot_id), "kind": "terminal"}
                ],
                rounding_state={"residual_numerator": 0},
                state="custody_candidate",
                created_ts_ms=created_ts_ms,
                updated_ts_ms=created_ts_ms,
            )
        )

    for index, hop in enumerate(result.get("hops") or []):
        if hop.get("frontier_state") != "queued":
            continue
        event_ref = _hop_evidence_ref(snapshot_id, hop, index)
        lot = AttributedLot(
            case_id=case.id,
            snapshot_id=snapshot_id,
            seed_event_ref=seed_ref,
            asset_identifier=asset_identifier,
            current_address=hop["address"],
            remaining_amount_base=int(hop["value_base"]),
            arrival_cursor={
                "txid": _first_txid(hop, fallback="unknown-live-hop"),
                "source_address": hop.get("source_address"),
                "ts_ms": hop.get("ts_ms"),
                "event_index": hop.get("event_index"),
            },
            allocation_policy=str(result.get("params", {}).get("strategy") or "dominant_fund_flow"),
            allocation_version="trinetra.allocation/1",
            ancestry=[
                {
                    "evidence_ref": event_ref,
                    "kind": "live_frontier",
                    "source_address": hop.get("source_address"),
                    "destination_address": hop.get("address"),
                }
            ],
            rounding_state=dict(hop.get("allocation") or {}),
            state="queued",
            created_ts_ms=created_ts_ms,
            updated_ts_ms=created_ts_ms,
        )
        session.add(lot)
        session.flush()
        session.add(
            FrontierItem(
                case_id=case.id,
                snapshot_id=snapshot_id,
                lot_id=lot.id,
                event_ref=event_ref,
                priority_base=int(hop["value_base"]),
                priority_reason=str(result.get("params", {}).get("strategy") or "dominant_fund_flow"),
                depth=int(hop.get("hop") or 1),
                state="queued",
                deferral_reason=None,
                resume_cursor={
                    "txid": _first_txid(hop, fallback="unknown-live-hop"),
                    "address": hop.get("address"),
                    "source_address": hop.get("source_address"),
                    "value_base": hop.get("value_base"),
                    "ts_ms": hop.get("ts_ms"),
                    "event_index": hop.get("event_index"),
                },
                created_ts_ms=created_ts_ms,
                updated_ts_ms=created_ts_ms,
            )
        )

    for parked in result.get("parked") or []:
        event_ref = _parked_evidence_ref(snapshot_id, parked)
        deferral_reason = _parked_deferral_reason(parked)
        lot = AttributedLot(
            case_id=case.id,
            snapshot_id=snapshot_id,
            seed_event_ref=seed_ref,
            asset_identifier=asset_identifier,
            current_address=parked["address"],
            remaining_amount_base=int(parked["value_base"]),
            arrival_cursor={
                "branch_id": parked.get("branch_id"),
                "from_hop": parked.get("from_hop"),
            },
            allocation_policy=str(result.get("params", {}).get("strategy") or "dominant_fund_flow"),
            allocation_version="trinetra.allocation/1",
            ancestry=[
                {
                    "evidence_ref": event_ref,
                    "kind": "parked_branch",
                    "source_address": parked.get("source_address"),
                    "destination_address": parked.get("address"),
                }
            ],
            rounding_state=dict(parked.get("allocation") or {"residual_numerator": 0}),
            state="deferred",
            created_ts_ms=created_ts_ms,
            updated_ts_ms=created_ts_ms,
        )
        session.add(lot)
        session.flush()
        session.add(
            FrontierItem(
                case_id=case.id,
                snapshot_id=snapshot_id,
                lot_id=lot.id,
                event_ref=event_ref,
                priority_base=int(parked["value_base"]),
                priority_reason=str(deferral_reason or "deferred"),
                depth=int(parked.get("from_hop") or 0) + 1,
                state="deferred",
                deferral_reason=deferral_reason,
                resume_cursor={
                    "branch_id": parked.get("branch_id"),
                    "address": parked.get("address"),
                    "source_address": parked.get("source_address"),
                    "txid": parked.get("txid"),
                    "event_index": parked.get("event_index"),
                    "from_hop": parked.get("from_hop"),
                    "value_base": parked.get("value_base"),
                    "ts_ms": parked.get("ts_ms"),
                    "retrieval_ts_ms": parked.get("retrieval_ts_ms"),
                    "retry_attempts": parked.get("retry_attempts"),
                    "next_retry_ts_ms": parked.get("next_retry_ts_ms"),
                    "provider_retry_after_ms": parked.get("provider_retry_after_ms"),
                    "provider": parked.get("provider"),
                },
                created_ts_ms=created_ts_ms,
                updated_ts_ms=created_ts_ms,
            )
        )


def _add_custody_assertion(
    session: Session,
    *,
    case: Case,
    snapshot_id: int,
    result: dict,
    terminal: dict,
    created_ts_ms: int,
) -> None:
    if not terminal_creates_finding(terminal):
        return
    deposit_address = terminal.get("deposit_address")
    amount_base = terminal.get("amount_credited_base")
    if not deposit_address:
        return
    session.add(
        CustodyAssertion(
            case_id=case.id,
            snapshot_id=snapshot_id,
            provider_key=str(terminal.get("custodian_key") or "unknown"),
            chain_family=result["chain"]["family"],
            chain_network=result["chain"]["network"],
            deposit_address=deposit_address,
            exact_credit_ref=_terminal_evidence_ref(snapshot_id),
            account_reference=None,
            service_role="exchange_deposit_account",
            provenance=_custody_assertion_provenance(terminal),
            dispute_status="undisputed",
            current_balance_base=None,
            recoverable_amount_base=int(amount_base) if amount_base is not None else None,
            created_ts_ms=created_ts_ms,
        )
    )


def _custody_assertion_provenance(terminal: dict) -> dict:
    provenance = dict(terminal.get("provenance") or {})
    if terminal.get("kind") == "vasp_deposit":
        provenance.setdefault("source", "fixture-terminal")
    else:
        provenance.setdefault("source", "verified-custody-terminal")
    if terminal.get("hot_wallet") is not None:
        provenance.setdefault("hot_wallet", terminal.get("hot_wallet"))
    provenance.setdefault(
        "note",
        "Deposit address assignment is separate from the hot wallet label.",
    )
    return provenance


def _conservation_for_result(result: dict) -> dict[str, dict[str, int]]:
    amount = result.get("case", {}).get("amount_reported_base")
    if amount is None or amount < 0:
        return {}
    asset_identifier = _asset_identifier(result)
    outcomes: list[ValueOutcome] = []
    terminal = result.get("terminal") or {}
    terminal_amount = terminal.get("amount_credited_base")
    if terminal_creates_finding(terminal) and terminal_amount is not None:
        outcomes.append(
            ValueOutcome(
                asset_identifier=asset_identifier,
                amount_base=int(terminal_amount),
                bucket="custody",
                evidence_ref="terminal-custody",
            )
        )
    stationary_amount = terminal.get("stationary_amount_base")
    if stationary_amount is not None and int(stationary_amount) > 0:
        outcomes.append(
            ValueOutcome(
                asset_identifier=asset_identifier,
                amount_base=int(stationary_amount),
                bucket="stationary",
                evidence_ref="terminal-stationary",
            )
        )
    for index, hop in enumerate(result.get("hops") or []):
        if hop.get("frontier_state") != "queued":
            continue
        outcomes.append(
            ValueOutcome(
                asset_identifier=asset_identifier,
                amount_base=_int_or_zero(hop.get("value_base")),
                bucket="deferred",
                evidence_ref=f"hop-{hop.get('event_ref') or hop.get('hop', index)}",
            )
        )
    for parked in result.get("parked") or []:
        outcomes.append(
            ValueOutcome(
                asset_identifier=asset_identifier,
                amount_base=_int_or_zero(parked.get("value_base")),
                bucket="deferred",
                evidence_ref=f"parked-{parked.get('branch_id')}",
            )
        )
    assigned = sum(outcome.amount_base for outcome in outcomes)
    remainder = int(amount) - assigned
    if remainder < 0:
        return {}
    if remainder:
        outcomes.append(
            ValueOutcome(
                asset_identifier=asset_identifier,
                amount_base=remainder,
                bucket="unresolved",
                evidence_ref="unresolved-remainder",
            )
        )
    return conservation_report({asset_identifier: int(amount)}, outcomes)


def _asset_identifier(result: dict) -> str:
    chain = result.get("chain") or {}
    asset = result.get("asset") or {}
    family = chain.get("family") or "unknown"
    network = chain.get("network") or "unknown"
    symbol = asset.get("symbol") or "unknown"
    contract = asset.get("contract")
    return f"{family}:{network}:{symbol}:{contract}" if contract else f"{family}:{network}:{symbol}"


def _coverage_from_source_status(status: object) -> tuple[str, list[dict[str, str]]]:
    if status == "cached":
        return "fixture_cached", []
    if status == "verified":
        return "queried_verified", []
    return "closed_without_coverage", [{"reason": "not_queried"}]


def _first_txid(hop: dict, *, fallback: str) -> str:
    txids = hop.get("txids")
    if txids is None:
        txids = []
    return str(txids[0]) if txids else fallback


def _int_or_zero(value: object) -> int:
    return int(value) if value is not None else 0


def _hop_evidence_ref(snapshot_id: int, hop: dict, index: int) -> str:
    if hop.get("event_ref"):
        return str(hop["event_ref"])
    return f"snapshot:{snapshot_id}:hop:{hop.get('hop', index)}"


def _parked_evidence_ref(snapshot_id: int, parked: dict) -> str:
    if parked.get("event_ref"):
        return str(parked["event_ref"])
    return f"snapshot:{snapshot_id}:parked:{parked.get('branch_id')}"


def _parked_deferral_reason(parked: dict) -> str:
    return str(parked.get("deferral_reason") or parked.get("reason") or "breadth_cap")


def _terminal_evidence_ref(snapshot_id: int) -> str:
    return f"snapshot:{snapshot_id}:terminal"


def _seed_event_ref(result: dict) -> str:
    txid = result.get("case", {}).get("payment_txid")
    if txid is None:
        txid = "unknown-seed"
    return f"seed:{txid}"


def _require_snapshot_id(snapshot: TraceSnapshot) -> int:
    if snapshot.id is None:
        raise RuntimeError("Trace snapshot must be flushed before evidence projection.")
    return snapshot.id


def _trace_seed_from_case(case: Case) -> TraceSeed:
    return TraceSeed(
        ack_no=case.ack_no,
        address=case.reported_address,
        payment_ts_ms=case.payment_ts_ms,
        amount_base=case.amount_reported_base,
        payment_txid=case.payment_txid,
        chain=ChainRef(case.chain_family, case.chain_network),
        asset=AssetRef(case.asset_symbol, None, case.asset_decimals),
    )
