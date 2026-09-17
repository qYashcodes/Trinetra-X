from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from sqlmodel import Session, select

from app.engine_bridge import TraceParams, trace_params_from_json
from app.models import (
    AttributedLot,
    CanonicalTraceEvent,
    FrontierItem,
    SourceCoverage,
    TraceSnapshot,
)
from app.services.allocation import FrontierCandidate, allocate_proportional, schedule_candidates
from app.services.evidence_store import persist_provider_coverage, provider_evidence_scope
from app.services.frontier import lease_frontier_batch
from app.services.hash import sha256_json
from app.services.time import now_ms
from app.settings import settings
from engine.adapters import tron


class LiveTronResumeError(ValueError):
    pass


@dataclass(frozen=True)
class LiveFrontierExpansion:
    canonical_events: list[CanonicalTraceEvent]
    child_lots: list[AttributedLot]
    child_frontier: list[FrontierItem]
    source_coverage: SourceCoverage
    stationary_amount_base: int


@dataclass(frozen=True)
class LiveTronWorkerCycle:
    expanded: list[LiveFrontierExpansion]
    deferred: list[FrontierItem]
    released_retries: list[FrontierItem]
    failures: list[dict]


def run_live_tron_frontier_cycle(
    session: Session,
    *,
    case_id: int,
    worker_id: str,
    limit: int,
    params: TraceParams | None = None,
    max_attempts: int = 3,
    backoff_ms: int = 60_000,
) -> LiveTronWorkerCycle:
    if limit < 0:
        raise LiveTronResumeError("Worker cycle limit must be non-negative.")
    if max_attempts <= 0 or backoff_ms < 0:
        raise LiveTronResumeError("Retry attempts must be positive and backoff non-negative.")
    released = release_due_frontier_retries(session, case_id=case_id)
    leased = lease_frontier_batch(session, case_id=case_id, worker_id=worker_id, limit=limit)
    expanded: list[LiveFrontierExpansion] = []
    deferred: list[FrontierItem] = []
    failures: list[dict] = []
    for item in leased:
        with provider_evidence_scope(
            root=settings.var_dir,
            case_id=item.case_id,
            snapshot_id=item.snapshot_id,
        ) as provider_evidence:
            try:
                expanded.append(expand_live_tron_frontier_item(session, item, params=params))
            except (
                tron.ProviderConfigurationError,
                tron.ProviderResponseError,
                LiveTronResumeError,
            ) as exc:
                item_id = item.id
                event_ref = item.event_ref
                session.rollback()
                if item_id is None:
                    failures.append(
                        {
                            "event_ref": event_ref,
                            "error_kind": exc.__class__.__name__,
                            "error": str(exc),
                        }
                    )
                else:
                    current = session.get(FrontierItem, item_id)
                    if current is None:
                        failures.append(
                            {
                                "event_ref": event_ref,
                                "error_kind": exc.__class__.__name__,
                                "error": str(exc),
                            }
                        )
                    else:
                        deferred_item = defer_frontier_for_retry(
                            session,
                            current,
                            error=exc,
                            max_attempts=max_attempts,
                            backoff_ms=backoff_ms,
                        )
                        deferred.append(deferred_item)
                        failures.append(
                            {
                                "event_ref": deferred_item.event_ref,
                                "error_kind": exc.__class__.__name__,
                                "error": str(exc),
                                "retry_attempts": dict(deferred_item.resume_cursor or {}).get(
                                    "retry_attempts"
                                ),
                                "retry_exhausted": bool(
                                    dict(deferred_item.resume_cursor or {}).get(
                                        "retry_exhausted"
                                    )
                                ),
                            }
                        )
            finally:
                if provider_evidence.records:
                    persist_provider_coverage(
                        session,
                        provider_evidence.records,
                        snapshot_id=item.snapshot_id,
                    )
                    session.commit()
    return LiveTronWorkerCycle(
        expanded=expanded,
        deferred=deferred,
        released_retries=released,
        failures=failures,
    )


def defer_frontier_for_retry(
    session: Session,
    item: FrontierItem,
    *,
    error: Exception,
    max_attempts: int = 3,
    backoff_ms: int = 60_000,
) -> FrontierItem:
    if item.id is None:
        raise LiveTronResumeError("Frontier item must be persisted before retry deferral.")
    if max_attempts <= 0 or backoff_ms < 0:
        raise LiveTronResumeError("Retry attempts must be positive and backoff non-negative.")
    cursor = dict(item.resume_cursor or {})
    attempts = int(cursor.get("retry_attempts") or 0) + 1
    now = now_ms()
    exponential_delay_ms = backoff_ms * (2 ** max(0, attempts - 1))
    provider_delay_ms = getattr(error, "retry_after_ms", None)
    effective_delay_ms = max(
        exponential_delay_ms,
        int(provider_delay_ms) if provider_delay_ms is not None else 0,
    )
    next_retry_ts_ms = now + effective_delay_ms
    retry_exhausted = attempts >= max_attempts
    cursor.update(
        {
            "retry_attempts": attempts,
            "last_error_kind": error.__class__.__name__,
            "last_error": str(error),
            "last_retry_ts_ms": now,
            "next_retry_ts_ms": next_retry_ts_ms,
            "retry_delay_ms": effective_delay_ms,
            "provider_retry_after_ms": provider_delay_ms,
        }
    )
    if retry_exhausted:
        cursor["retry_exhausted"] = True
    item.state = "deferred"
    item.deferral_reason = "provider_backoff"
    item.resume_cursor = cursor
    item.updated_ts_ms = now
    session.add(item)
    session.add(
        _provider_backoff_coverage(
            item=item,
            error=error,
            attempts=attempts,
            next_retry_ts_ms=next_retry_ts_ms,
            retry_exhausted=retry_exhausted,
            created_ts_ms=now,
        )
    )
    session.commit()
    session.refresh(item)
    return item


def _provider_backoff_coverage(
    *,
    item: FrontierItem,
    error: Exception,
    attempts: int,
    next_retry_ts_ms: int,
    retry_exhausted: bool,
    created_ts_ms: int,
) -> SourceCoverage:
    cursor = dict(item.resume_cursor or {})
    return SourceCoverage(
        case_id=item.case_id,
        snapshot_id=item.snapshot_id,
        provider="live-tron-frontier",
        chain_family="TRON",
        chain_network="mainnet",
        query_range={
            "kind": "provider_backoff",
            "event_ref": item.event_ref,
            "lot_id": item.lot_id,
            "address": cursor.get("address") or cursor.get("destination_address"),
        },
        observed_watermark={
            "error_kind": error.__class__.__name__,
            "error": str(error),
            "retry_attempts": attempts,
            "last_retry_ts_ms": created_ts_ms,
            "next_retry_ts_ms": next_retry_ts_ms,
            "retry_exhausted": retry_exhausted,
        },
        retrieval_ts_ms=created_ts_ms,
        completeness="provider_backoff",
        gaps=[{"reason": "provider_backoff", "event_ref": item.event_ref}],
        conflicts=[],
    )


def release_due_frontier_retries(
    session: Session,
    *,
    case_id: int,
    now_ts_ms: int | None = None,
    limit: int | None = None,
) -> list[FrontierItem]:
    now = now_ms() if now_ts_ms is None else now_ts_ms
    rows = session.exec(
        select(FrontierItem)
        .where(FrontierItem.case_id == case_id)
        .where(FrontierItem.state == "deferred")
        .where(FrontierItem.deferral_reason == "provider_backoff")
        .order_by(FrontierItem.updated_ts_ms.asc(), FrontierItem.id.asc())
    ).all()
    released: list[FrontierItem] = []
    for row in rows:
        cursor = dict(row.resume_cursor or {})
        if cursor.get("retry_exhausted"):
            continue
        retry_ts = cursor.get("next_retry_ts_ms")
        if retry_ts is not None and int(retry_ts) > now:
            continue
        cursor["retry_released_ts_ms"] = now
        row.state = "queued"
        row.deferral_reason = None
        row.resume_cursor = cursor
        row.updated_ts_ms = now
        session.add(row)
        released.append(row)
        if limit is not None and len(released) >= limit:
            break
    if released:
        session.commit()
        for row in released:
            session.refresh(row)
    return released


def expand_live_tron_frontier_item(
    session: Session,
    item: FrontierItem,
    *,
    params: TraceParams | None = None,
    contract_address: str | None = None,
) -> LiveFrontierExpansion:
    """Expand one leased TRON frontier item without making custody claims."""

    if params is None and item.snapshot_id is not None:
        snapshot = session.get(TraceSnapshot, item.snapshot_id)
        params = trace_params_from_json(
            snapshot.result_json.get("params") if snapshot else None,
            trace_mode="live",
        )
    params = params or TraceParams(trace_mode="live")
    if item.state != "leased":
        raise LiveTronResumeError("Only leased frontier items can be expanded.")
    if item.lot_id is None:
        raise LiveTronResumeError("Frontier item must reference an attributed lot.")
    lot = session.get(AttributedLot, item.lot_id)
    if lot is None:
        raise LiveTronResumeError("Attributed lot for frontier item was not found.")
    if lot.remaining_amount_base < 0:
        raise LiveTronResumeError("Attributed lot amount cannot be negative.")

    address = lot.current_address
    contract = contract_address or _contract_from_asset_identifier(lot.asset_identifier)
    cursor = dict(item.resume_cursor or {})
    min_timestamp = cursor.get("ts_ms")
    if min_timestamp is None:
        min_timestamp = cursor.get("observed_ts_ms")
    raw_rows = tron.fetch_trc20_transfers(
        address,
        min_timestamp=int(min_timestamp) if min_timestamp is not None else None,
        contract_address=contract,
    )
    outgoing = _outgoing_rows(raw_rows, address)
    attributed_edges, stationary_amount_base = _attribute_edges(
        outgoing,
        attributed_base=lot.remaining_amount_base,
    )
    scheduled_ids, deferral_by_id = _frontier_schedule(
        attributed_edges,
        params=params,
        next_depth=item.depth + 1,
        value_floor_base=_value_floor(lot.remaining_amount_base, params.value_floor_share),
        visited_addresses=_visited_addresses_for_lot(lot),
    )

    now = now_ms()
    canonical_events = [
        _ensure_canonical_event(
            session,
            item=item,
            lot=lot,
            edge=edge,
            tx_index=index,
            created_ts_ms=now,
        )
        for index, edge in enumerate(attributed_edges)
    ]
    child_lots: list[AttributedLot] = []
    child_frontier: list[FrontierItem] = []
    for edge in attributed_edges:
        attributed = int(edge["attributed_base"])
        if attributed <= 0:
            continue
        event_ref = edge["event_ref"]
        state = "queued" if event_ref in scheduled_ids else "deferred"
        child_lot = AttributedLot(
            case_id=item.case_id,
            snapshot_id=item.snapshot_id,
            seed_event_ref=lot.seed_event_ref,
            asset_identifier=lot.asset_identifier,
            current_address=str(edge["destination"]),
            remaining_amount_base=attributed,
            arrival_cursor={
                "source_address": address,
                "txid": edge["txid"],
                "event_index": edge.get("event_index"),
                "ts_ms": edge.get("ts_ms"),
            },
            allocation_policy=params.strategy,
            allocation_version="trinetra.allocation/1",
            ancestry=[
                *list(lot.ancestry or []),
                {
                    "evidence_ref": event_ref,
                    "kind": "live_resume_frontier",
                    "source_address": address,
                    "destination_address": edge.get("destination"),
                },
            ],
            rounding_state={"residual_numerator": edge.get("residual_numerator", 0)},
            state=state,
            created_ts_ms=now,
            updated_ts_ms=now,
        )
        session.add(child_lot)
        session.flush()
        child_lots.append(child_lot)
        child_frontier.append(
            _upsert_child_frontier(
                session,
                parent=item,
                child_lot=child_lot,
                edge=edge,
                state=state,
                deferral_reason=deferral_by_id.get(event_ref),
                params=params,
                created_ts_ms=now,
            )
        )

    lot.remaining_amount_base = stationary_amount_base
    lot.state = "stationary_observed" if stationary_amount_base else "expanded"
    lot.updated_ts_ms = now
    item.state = "completed"
    item.updated_ts_ms = now
    item.resume_cursor = {
        **cursor,
        "outcome_ref": f"live-tron-expand:{item.event_ref or item.id}",
        "expanded_ts_ms": now,
        "observed_outgoing": len(attributed_edges),
        "stationary_amount_base": stationary_amount_base,
    }
    coverage = _source_coverage(
        item=item,
        lot=lot,
        provider="trongrid",
        contract=contract,
        observed=len(attributed_edges),
        queued=sum(1 for row in child_frontier if row.state == "queued"),
        deferred=sum(1 for row in child_frontier if row.state == "deferred"),
        stationary_amount_base=stationary_amount_base,
        created_ts_ms=now,
    )
    session.add(lot)
    session.add(item)
    session.add(coverage)
    session.commit()
    for row in canonical_events + child_lots + child_frontier + [coverage, item, lot]:
        session.refresh(row)
    return LiveFrontierExpansion(
        canonical_events=canonical_events,
        child_lots=child_lots,
        child_frontier=child_frontier,
        source_coverage=coverage,
        stationary_amount_base=stationary_amount_base,
    )


def _outgoing_rows(raw_rows: list[dict], address: str) -> list[dict]:
    normalised = [tron.normalise(row) for row in raw_rows]
    return sorted(
        [
            row
            for row in normalised
            if str(row.get("source", "")).lower() == address.lower()
        ],
        key=lambda row: (
            _int_or_zero(row.get("ts_ms")),
            _str_or_empty(row.get("txid")),
            _int_or_zero(row.get("event_index")),
        ),
    )


def _visited_addresses_for_lot(lot: AttributedLot) -> set[str]:
    addresses = {lot.current_address.lower()}
    for step in lot.ancestry or []:
        for key in ("address", "current_address", "source_address", "destination_address"):
            value = step.get(key) if isinstance(step, dict) else None
            if isinstance(value, str) and value.strip():
                addresses.add(value.lower())
    return addresses


def _attribute_edges(
    outgoing: list[dict],
    *,
    attributed_base: int,
) -> tuple[list[dict], int]:
    outgoing_total = sum(max(0, _int_or_zero(row.get("amount_base"))) for row in outgoing)
    balance_base = max(attributed_base, outgoing_total)
    remaining_attributed = attributed_base
    residual_numerator = 0
    edges: list[dict] = []
    for index, row in enumerate(outgoing):
        outgoing_base = max(0, _int_or_zero(row.get("amount_base")))
        edge = dict(row)
        edge["event_ref"] = _event_ref(edge, index)
        edge["event_order"] = index
        if balance_base <= 0 or remaining_attributed <= 0:
            edge["attributed_base"] = 0
            edge["residual_numerator"] = residual_numerator
            edges.append(edge)
            continue
        step = allocate_proportional(
            balance_base=balance_base,
            attributed_base=remaining_attributed,
            outgoing_base=outgoing_base,
            residual_numerator=residual_numerator,
        )
        edge["attributed_base"] = step.outgoing_attributed_base
        edge["residual_numerator"] = step.residual_numerator
        edges.append(edge)
        balance_base = step.remaining_balance_base
        remaining_attributed = step.remaining_attributed_base
        residual_numerator = step.residual_numerator
    return edges, remaining_attributed


def _frontier_schedule(
    edges: list[dict],
    *,
    params: TraceParams,
    next_depth: int,
    value_floor_base: int,
    visited_addresses: set[str],
) -> tuple[set[str], dict[str, str]]:
    deferrals: dict[str, str] = {}
    candidates: list[FrontierCandidate] = []
    for edge in edges:
        event_ref = edge["event_ref"]
        attributed = int(edge["attributed_base"])
        destination = str(edge.get("destination") or "")
        if next_depth > params.max_depth:
            deferrals[event_ref] = "depth_budget"
        elif destination.lower() in visited_addresses:
            deferrals[event_ref] = "cycle_detected"
        elif attributed <= 0 or attributed < value_floor_base:
            deferrals[event_ref] = "value_floor"
        else:
            candidates.append(
                FrontierCandidate(
                    stable_id=event_ref,
                    attributed_base=attributed,
                    event_order=int(edge["event_order"]),
                )
            )

    scheduled, deferred = schedule_candidates(
        candidates,
        strategy=params.strategy,
        breadth_cap=min(params.breadth_cap, params.address_budget),
    )
    scheduled_ids = {row.stable_id for row in scheduled}
    for row in deferred:
        deferrals[row.stable_id] = "breadth_cap"
    return scheduled_ids, deferrals


def _ensure_canonical_event(
    session: Session,
    *,
    item: FrontierItem,
    lot: AttributedLot,
    edge: dict,
    tx_index: int,
    created_ts_ms: int,
) -> CanonicalTraceEvent:
    existing = session.exec(
        select(CanonicalTraceEvent)
        .where(CanonicalTraceEvent.snapshot_id == item.snapshot_id)
        .where(CanonicalTraceEvent.evidence_ref == edge["event_ref"])
    ).first()
    if existing:
        return existing
    row = CanonicalTraceEvent(
        case_id=item.case_id,
        snapshot_id=item.snapshot_id,
        chain_family="TRON",
        chain_network="mainnet",
        txid=str(edge["txid"]),
        tx_index=tx_index,
        event_index=edge.get("event_index"),
        output_index=None,
        asset_identifier=lot.asset_identifier,
        source_address=lot.current_address,
        destination_address=edge.get("destination"),
        amount_base=_int_or_zero(edge.get("amount_base")),
        success=True,
        finality="provider_confirmed",
        evidence_ref=edge["event_ref"],
        raw_sha256=sha256_json(edge),
        observed_ts_ms=edge.get("ts_ms"),
        created_ts_ms=created_ts_ms,
    )
    session.add(row)
    return row


def _upsert_child_frontier(
    session: Session,
    *,
    parent: FrontierItem,
    child_lot: AttributedLot,
    edge: dict,
    state: str,
    deferral_reason: str | None,
    params: TraceParams,
    created_ts_ms: int,
) -> FrontierItem:
    existing = session.exec(
        select(FrontierItem)
        .where(FrontierItem.snapshot_id == parent.snapshot_id)
        .where(FrontierItem.event_ref == edge["event_ref"])
    ).first()
    parent_cursor = dict(parent.resume_cursor or {})
    cursor = {
        "source_address": parent_cursor.get("address")
        or parent_cursor.get("destination_address"),
        "address": edge.get("destination"),
        "txid": edge.get("txid"),
        "event_index": edge.get("event_index"),
        "ts_ms": edge.get("ts_ms"),
        "value_base": edge.get("attributed_base"),
    }
    if existing:
        existing.lot_id = child_lot.id
        existing.priority_base = int(edge["attributed_base"])
        existing.priority_reason = params.strategy
        existing.depth = parent.depth + 1
        existing.state = state
        existing.deferral_reason = deferral_reason
        existing.resume_cursor = cursor
        existing.updated_ts_ms = created_ts_ms
        session.add(existing)
        return existing
    row = FrontierItem(
        case_id=parent.case_id,
        snapshot_id=parent.snapshot_id,
        lot_id=child_lot.id,
        event_ref=edge["event_ref"],
        priority_base=int(edge["attributed_base"]),
        priority_reason=params.strategy,
        depth=parent.depth + 1,
        state=state,
        deferral_reason=deferral_reason,
        resume_cursor=cursor,
        created_ts_ms=created_ts_ms,
        updated_ts_ms=created_ts_ms,
    )
    session.add(row)
    return row


def _source_coverage(
    *,
    item: FrontierItem,
    lot: AttributedLot,
    provider: str,
    contract: str,
    observed: int,
    queued: int,
    deferred: int,
    stationary_amount_base: int,
    created_ts_ms: int,
) -> SourceCoverage:
    return SourceCoverage(
        case_id=item.case_id,
        snapshot_id=item.snapshot_id,
        provider="live-tron-frontier",
        chain_family="TRON",
        chain_network="mainnet",
        query_range={
            "kind": "frontier_expansion",
            "provider": provider,
            "address": lot.current_address,
            "contract_address": contract,
        },
        observed_watermark={
            "observed_outgoing": observed,
            "queued": queued,
            "deferred": deferred,
            "stationary_amount_base": stationary_amount_base,
        },
        retrieval_ts_ms=created_ts_ms,
        completeness="frontier_expanded",
        gaps=[],
        conflicts=[],
    )


def _value_floor(amount_base: int, floor_share: Decimal) -> int:
    return int((Decimal(amount_base) * floor_share).to_integral_value(rounding=ROUND_FLOOR))


def _event_ref(edge: dict, index: int) -> str:
    event_index = edge.get("event_index")
    suffix = event_index if event_index is not None else index
    txid = edge.get("txid")
    txid_text = str(txid) if txid is not None else "unknown"
    return f"live:tron:{txid_text}:{suffix}"


def _int_or_zero(value: object) -> int:
    return 0 if value is None else int(value)


def _str_or_empty(value: object) -> str:
    return "" if value is None else str(value)


def _contract_from_asset_identifier(asset_identifier: str) -> str:
    parts = asset_identifier.split(":")
    return parts[3] if len(parts) >= 4 and parts[3] else tron.TRON_MAINNET_USDT
