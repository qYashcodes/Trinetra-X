from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlmodel import Session, select

from app.models import FrontierItem
from app.services.allocation import FrontierCandidate, schedule_candidates
from app.services.time import now_ms

DeferralReason = Literal[
    "value_floor",
    "depth_budget",
    "time_window",
    "address_budget",
    "breadth_cap",
    "cycle_detected",
    "provider_backoff",
]


class FrontierError(ValueError):
    pass


@dataclass(frozen=True)
class ObservedOutgoing:
    event_ref: str
    destination_address: str
    attributed_base: int
    event_order: int
    next_depth: int
    observed_ts_ms: int | None = None
    stable_id: str | None = None
    resume_cursor: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FrontierPersistenceResult:
    queued: list[FrontierItem]
    deferred: list[FrontierItem]


def persist_frontier_observations(
    session: Session,
    *,
    case_id: int,
    snapshot_id: int | None,
    lot_id: int | None,
    observed_edges: list[ObservedOutgoing],
    strategy: Literal["dominant_fund_flow", "value_weighted"],
    breadth_cap: int,
    address_budget_remaining: int,
    value_floor_base: int,
    max_depth: int,
    visited_addresses: set[str] | None = None,
    time_window_end_ms: int | None = None,
) -> FrontierPersistenceResult:
    if breadth_cap < 0 or address_budget_remaining < 0 or value_floor_base < 0 or max_depth < 0:
        raise FrontierError("Frontier budgets and floors must be non-negative.")

    visited = {address.lower() for address in visited_addresses or set()}
    immediate_deferrals: dict[str, DeferralReason] = {}
    candidates: list[FrontierCandidate] = []

    for edge in observed_edges:
        _validate_observed(edge)
        reason = _first_deferral_reason(
            edge,
            value_floor_base=value_floor_base,
            max_depth=max_depth,
            visited_addresses=visited,
            time_window_end_ms=time_window_end_ms,
        )
        stable_id = edge.stable_id or edge.event_ref
        if reason:
            immediate_deferrals[edge.event_ref] = reason
        else:
            candidates.append(
                FrontierCandidate(
                    stable_id=stable_id,
                    attributed_base=edge.attributed_base,
                    event_order=edge.event_order,
                )
            )

    breadth_queued, breadth_deferred = schedule_candidates(
        candidates,
        strategy=strategy,
        breadth_cap=breadth_cap,
    )
    queued_candidate_ids = {item.stable_id for item in breadth_queued[:address_budget_remaining]}
    address_deferred_ids = {
        item.stable_id
        for item in breadth_queued[address_budget_remaining:]
    }
    breadth_deferred_ids = {item.stable_id for item in breadth_deferred}

    existing = _existing_frontier_by_ref(
        session,
        case_id=case_id,
        snapshot_id=snapshot_id,
        event_refs=[edge.event_ref for edge in observed_edges],
    )
    queued_rows: list[FrontierItem] = []
    deferred_rows: list[FrontierItem] = []
    now = now_ms()

    for edge in observed_edges:
        stable_id = edge.stable_id or edge.event_ref
        state: Literal["queued", "deferred"] = "queued"
        reason: DeferralReason | None = None
        if edge.event_ref in immediate_deferrals:
            state = "deferred"
            reason = immediate_deferrals[edge.event_ref]
        elif stable_id in address_deferred_ids:
            state = "deferred"
            reason = "address_budget"
        elif stable_id in breadth_deferred_ids:
            state = "deferred"
            reason = "breadth_cap"
        elif stable_id not in queued_candidate_ids:
            state = "deferred"
            reason = "address_budget"

        row = existing.get(edge.event_ref)
        if row is None:
            row = FrontierItem(
                case_id=case_id,
                snapshot_id=snapshot_id,
                lot_id=lot_id,
                event_ref=edge.event_ref,
                priority_base=edge.attributed_base,
                priority_reason=strategy,
                depth=edge.next_depth,
                state=state,
                deferral_reason=reason,
                resume_cursor=_resume_cursor(edge, strategy=strategy),
                created_ts_ms=now,
                updated_ts_ms=now,
            )
        else:
            row.priority_base = edge.attributed_base
            row.priority_reason = strategy
            row.depth = edge.next_depth
            row.state = state
            row.deferral_reason = reason
            row.resume_cursor = _resume_cursor(edge, strategy=strategy)
            row.updated_ts_ms = now
        session.add(row)
        if state == "queued":
            queued_rows.append(row)
        else:
            deferred_rows.append(row)

    session.commit()
    for row in queued_rows + deferred_rows:
        session.refresh(row)
    return FrontierPersistenceResult(queued=queued_rows, deferred=deferred_rows)


def next_frontier_batch(
    session: Session,
    *,
    case_id: int,
    limit: int,
) -> list[FrontierItem]:
    if limit < 0:
        raise FrontierError("Batch limit must be non-negative.")
    return list(
        session.exec(
            select(FrontierItem)
            .where(FrontierItem.case_id == case_id)
            .where(FrontierItem.state == "queued")
            .order_by(
                FrontierItem.priority_base.desc(),
                FrontierItem.depth.asc(),
                FrontierItem.id.asc(),
            )
            .limit(limit)
        )
    )


def lease_frontier_batch(
    session: Session,
    *,
    case_id: int,
    worker_id: str,
    limit: int,
) -> list[FrontierItem]:
    if not worker_id.strip():
        raise FrontierError("Worker id is required for frontier leasing.")
    rows = next_frontier_batch(session, case_id=case_id, limit=limit)
    leased_ts_ms = now_ms()
    for row in rows:
        row.state = "leased"
        row.lease_version += 1
        cursor = dict(row.resume_cursor or {})
        cursor["leased_by"] = worker_id
        cursor["leased_ts_ms"] = leased_ts_ms
        row.resume_cursor = cursor
        row.updated_ts_ms = leased_ts_ms
        session.add(row)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def recover_stale_frontier_leases(
    session: Session,
    *,
    lease_timeout_ms: int,
    now_ts_ms: int | None = None,
) -> list[FrontierItem]:
    if lease_timeout_ms < 0:
        raise FrontierError("Lease timeout must be non-negative.")
    current = now_ms() if now_ts_ms is None else now_ts_ms
    cutoff = current - lease_timeout_ms
    rows = session.exec(
        select(FrontierItem)
        .where(FrontierItem.state == "leased")
        .order_by(FrontierItem.updated_ts_ms.asc(), FrontierItem.id.asc())
    ).all()
    recovered: list[FrontierItem] = []
    for row in rows:
        cursor = dict(row.resume_cursor or {})
        leased_ts_ms = cursor.get("leased_ts_ms")
        effective_lease_ts = (
            int(leased_ts_ms) if leased_ts_ms is not None else row.updated_ts_ms
        )
        if effective_lease_ts > cutoff:
            continue
        history = list(cursor.get("lease_recovery_history") or [])
        history.append(
            {
                "leased_by": cursor.get("leased_by"),
                "leased_ts_ms": effective_lease_ts,
                "recovered_ts_ms": current,
                "lease_version": row.lease_version,
            }
        )
        cursor["lease_recovery_history"] = history
        cursor["last_lease_recovery_ts_ms"] = current
        row.resume_cursor = cursor
        row.state = "queued"
        row.deferral_reason = None
        row.updated_ts_ms = current
        session.add(row)
        recovered.append(row)
    if recovered:
        session.commit()
        for row in recovered:
            session.refresh(row)
    return recovered


def complete_frontier_item(
    session: Session,
    item: FrontierItem,
    *,
    outcome_ref: str,
) -> FrontierItem:
    if item.state != "leased":
        raise FrontierError("Only leased frontier items can be completed.")
    if not outcome_ref.strip():
        raise FrontierError("Completion outcome reference is required.")
    cursor = dict(item.resume_cursor or {})
    cursor["outcome_ref"] = outcome_ref
    item.resume_cursor = cursor
    item.state = "completed"
    item.updated_ts_ms = now_ms()
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def release_frontier_item(
    session: Session,
    item: FrontierItem,
    *,
    deferral_reason: DeferralReason | None = None,
) -> FrontierItem:
    if item.state != "leased":
        raise FrontierError("Only leased frontier items can be released.")
    item.state = "deferred" if deferral_reason else "queued"
    item.deferral_reason = deferral_reason
    item.updated_ts_ms = now_ms()
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _existing_frontier_by_ref(
    session: Session,
    *,
    case_id: int,
    snapshot_id: int | None,
    event_refs: list[str],
) -> dict[str, FrontierItem]:
    if not event_refs:
        return {}
    rows = session.exec(
        select(FrontierItem)
        .where(FrontierItem.case_id == case_id)
        .where(FrontierItem.snapshot_id == snapshot_id)
        .where(FrontierItem.event_ref.in_(event_refs))
    ).all()
    return {str(row.event_ref): row for row in rows if row.event_ref}


def _first_deferral_reason(
    edge: ObservedOutgoing,
    *,
    value_floor_base: int,
    max_depth: int,
    visited_addresses: set[str],
    time_window_end_ms: int | None,
) -> DeferralReason | None:
    if edge.destination_address.lower() in visited_addresses:
        return "cycle_detected"
    if edge.next_depth > max_depth:
        return "depth_budget"
    if (
        time_window_end_ms is not None
        and edge.observed_ts_ms is not None
        and edge.observed_ts_ms > time_window_end_ms
    ):
        return "time_window"
    if edge.attributed_base < value_floor_base:
        return "value_floor"
    return None


def _resume_cursor(edge: ObservedOutgoing, *, strategy: str) -> dict:
    cursor = dict(edge.resume_cursor)
    cursor.update(
        {
            "event_ref": edge.event_ref,
            "destination_address": edge.destination_address,
            "event_order": edge.event_order,
            "attributed_base": edge.attributed_base,
            "strategy": strategy,
        }
    )
    if edge.observed_ts_ms is not None:
        cursor["observed_ts_ms"] = edge.observed_ts_ms
    return cursor


def _validate_observed(edge: ObservedOutgoing) -> None:
    if not edge.event_ref.strip():
        raise FrontierError("Observed edge event reference is required.")
    if not edge.destination_address.strip():
        raise FrontierError("Observed edge destination address is required.")
    if edge.attributed_base < 0 or edge.event_order < 0 or edge.next_depth < 0:
        raise FrontierError("Observed edge amounts and cursors must be non-negative.")
