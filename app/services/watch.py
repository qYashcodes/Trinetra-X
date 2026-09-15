from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Literal

from sqlmodel import Session, select

from app.models import (
    CanonicalTraceEvent,
    Case,
    CaseStage,
    Finding,
    SourceCoverage,
    TraceEvent,
    TraceSnapshot,
    WalletWatch,
    WatchAlert,
    WatchAlertEvent,
    WatchGraphExtension,
)
from app.services.audit import append_audit_event, read_audit_events
from app.services.evidence_store import persist_provider_coverage, provider_evidence_scope
from app.services.explainability import trace_explainability
from app.services.hash import sha256_json
from app.services.provider_budget import provider_request_context
from app.services.time import now_ms
from app.settings import settings
from engine.adapters import tron


AlertPriority = Literal["highest", "high", "medium", "low"]
WatchTier = Literal["hot", "standard", "cold"]

PRIORITY_ORDER = {"highest": 4, "high": 3, "medium": 2, "low": 1}
TIER_ORDER = {"hot": 0, "standard": 1, "cold": 2}
CLOSED_CASE_STAGES = {CaseStage.restraint_confirmed, CaseStage.closed_no_custody}
ALERT_COPY = {
    "registry_deposit_observed": (
        "A confirmed transfer into a reviewed registry-listed deposit address was observed. "
        "A custody path may now be reviewable; no finding was created."
    ),
    "adverse_source_match": (
        "The watched address appears in a sanctions or reported-abuse source supplied to this "
        "observation. Officer review is required."
    ),
    "protocol_boundary_observed": (
        "A confirmed transfer into a recorded protocol or bridge boundary was observed."
    ),
    "material_outbound_observed": "A confirmed outbound transfer above the configured material-value threshold was observed.",
    "dormant_activity_observed": "Confirmed activity was observed after the watched address had been dormant.",
    "outbound_observed": "A confirmed outbound transfer from the watched address was observed.",
}


class WatchError(ValueError):
    pass


@dataclass(frozen=True)
class WatchlistSyncResult:
    created: int
    updated: int
    active: int
    capacity_wait: int
    paused: int


@dataclass(frozen=True)
class WatchPollResult:
    watch_id: int
    status: str
    observed_events: int
    alerts_created: int
    snapshot_ids: tuple[int, ...]
    error_kind: str | None = None


@dataclass(frozen=True)
class WatchCycleResult:
    enabled: bool
    sync: WatchlistSyncResult | None
    watches_polled: int
    observed_events: int
    alerts_created: int
    snapshot_ids: tuple[int, ...]
    deferred_by_budget: int
    failures: tuple[dict[str, Any], ...]


def synchronise_automatic_watches(
    session: Session,
    *,
    threshold_base: int | None = None,
    cap: int | None = None,
    current_ts_ms: int | None = None,
) -> WatchlistSyncResult:
    threshold = settings.watch_auto_min_base if threshold_base is None else threshold_base
    watch_cap = settings.watchlist_cap if cap is None else cap
    if threshold < 0 or watch_cap < 0:
        raise WatchError("Watch threshold and cap must be non-negative.")
    current = now_ms() if current_ts_ms is None else current_ts_ms
    created = 0
    updated = 0
    cases = session.exec(select(Case).order_by(Case.id)).all()
    for case in cases:
        latest = _latest_snapshot(session, int(case.id))
        existing_case_watches = session.exec(
            select(WalletWatch).where(WalletWatch.case_id == case.id)
        ).all()
        if case.stage in CLOSED_CASE_STAGES:
            for watch in existing_case_watches:
                if watch.status != "paused":
                    watch.tier = "cold"
                    watch.updated_ts_ms = current
                    session.add(watch)
                    updated += 1
            continue
        if latest is None:
            continue
        for address, amount_base in _watch_candidates(case, latest):
            if amount_base < threshold:
                continue
            watch = _find_watch(
                session,
                case_id=int(case.id),
                address=address,
                asset_identifier=_asset_identifier(case, latest),
            )
            if watch is None:
                watch = WalletWatch(
                    case_id=int(case.id),
                    snapshot_id=latest.id,
                    chain_family=case.chain_family,
                    chain_network=case.chain_network,
                    asset_identifier=_asset_identifier(case, latest),
                    asset_symbol=case.asset_symbol,
                    asset_decimals=case.asset_decimals,
                    address=address,
                    source="automatic",
                    pinned=False,
                    status="active",
                    tier=_tier_for(case, amount_base, None, current),
                    attributed_value_base=amount_base,
                    next_poll_ts_ms=current,
                    created_ts_ms=current,
                    updated_ts_ms=current,
                )
                created += 1
            else:
                watch.snapshot_id = latest.id
                watch.attributed_value_base = max(watch.attributed_value_base, amount_base)
                if watch.status != "paused":
                    watch.tier = _tier_for(
                        case,
                        watch.attributed_value_base,
                        watch.last_movement_ts_ms,
                        current,
                    )
                watch.updated_ts_ms = current
                updated += 1
            session.add(watch)
    session.flush()
    active, waiting, paused = _rebalance_watchlist(session, cap=watch_cap, current_ts_ms=current)
    session.commit()
    return WatchlistSyncResult(
        created=created,
        updated=updated,
        active=len(active),
        capacity_wait=len(waiting),
        paused=len(paused),
    )


def create_manual_watch(
    session: Session,
    *,
    case: Case,
    address: str,
    actor_pis: str,
    cap: int | None = None,
    current_ts_ms: int | None = None,
) -> WalletWatch:
    normalized = address.strip()
    if case.chain_family != "TRON" or case.chain_network != "mainnet" or not tron.matches(normalized):
        raise WatchError("Stage 6 manual watches support TRON mainnet addresses only.")
    current = now_ms() if current_ts_ms is None else current_ts_ms
    latest = _latest_snapshot(session, int(case.id))
    asset_identifier = _asset_identifier(case, latest)
    watch = _find_watch(
        session,
        case_id=int(case.id),
        address=normalized,
        asset_identifier=asset_identifier,
    )
    highest_priority = max(
        session.exec(select(WalletWatch.officer_priority)).all() or [0]
    )
    if watch is None:
        watch = WalletWatch(
            case_id=int(case.id),
            snapshot_id=latest.id if latest else None,
            chain_family=case.chain_family,
            chain_network=case.chain_network,
            asset_identifier=asset_identifier,
            asset_symbol=case.asset_symbol,
            asset_decimals=case.asset_decimals,
            address=normalized,
            source="manual",
            pinned=True,
            officer_priority=int(highest_priority) + 1,
            status="active",
            tier=_tier_for(case, case.amount_reported_base, None, current),
            attributed_value_base=case.amount_reported_base,
            next_poll_ts_ms=current,
            created_by_pis=actor_pis,
            created_ts_ms=current,
            updated_ts_ms=current,
        )
    else:
        watch.source = "manual"
        watch.pinned = True
        watch.officer_priority = int(highest_priority) + 1
        watch.status = "active"
        watch.capacity_reason = None
        watch.created_by_pis = watch.created_by_pis or actor_pis
        watch.updated_ts_ms = current
    session.add(watch)
    session.flush()
    _rebalance_watchlist(
        session,
        cap=settings.watchlist_cap if cap is None else cap,
        current_ts_ms=current,
    )
    session.commit()
    session.refresh(watch)
    append_audit_event(
        actor_pis,
        "watch.manual_pin",
        f"watch:{watch.id}",
        {"case_id": case.id, "address": normalized, "status": watch.status},
    )
    return watch


def prioritize_watch(
    session: Session,
    watch: WalletWatch,
    *,
    actor_pis: str,
    cap: int | None = None,
    current_ts_ms: int | None = None,
) -> WalletWatch:
    current = now_ms() if current_ts_ms is None else current_ts_ms
    highest_priority = max(
        session.exec(select(WalletWatch.officer_priority)).all() or [0]
    )
    watch.pinned = True
    watch.officer_priority = int(highest_priority) + 1
    if watch.status == "paused":
        watch.status = "capacity_wait"
    watch.updated_ts_ms = current
    session.add(watch)
    _rebalance_watchlist(
        session,
        cap=settings.watchlist_cap if cap is None else cap,
        current_ts_ms=current,
    )
    session.commit()
    session.refresh(watch)
    append_audit_event(
        actor_pis,
        "watch.prioritize",
        f"watch:{watch.id}",
        {"status": watch.status, "officer_priority": watch.officer_priority},
    )
    return watch


def set_watch_paused(
    session: Session,
    watch: WalletWatch,
    *,
    paused: bool,
    actor_pis: str,
    cap: int | None = None,
    current_ts_ms: int | None = None,
) -> WalletWatch:
    current = now_ms() if current_ts_ms is None else current_ts_ms
    watch.status = "paused" if paused else "capacity_wait"
    watch.capacity_reason = "officer_paused" if paused else None
    watch.updated_ts_ms = current
    session.add(watch)
    _rebalance_watchlist(
        session,
        cap=settings.watchlist_cap if cap is None else cap,
        current_ts_ms=current,
    )
    session.commit()
    session.refresh(watch)
    append_audit_event(
        actor_pis,
        "watch.pause" if paused else "watch.resume",
        f"watch:{watch.id}",
        {"status": watch.status},
    )
    return watch


def transition_alert(
    session: Session,
    alert: WatchAlert,
    *,
    action: str,
    actor_pis: str,
    reason: str | None = None,
    snoozed_until_ms: int | None = None,
    current_ts_ms: int | None = None,
) -> WatchAlert:
    current = now_ms() if current_ts_ms is None else current_ts_ms
    before = alert.state
    normalized_reason = (reason or "").strip() or None
    if action == "read" and before == "unread":
        alert.state = "read"
        alert.read_by_pis = actor_pis
        alert.read_ts_ms = current
    elif action == "acknowledge" and before == "read":
        alert.state = "acknowledged"
        alert.acknowledged_by_pis = actor_pis
        alert.acknowledged_ts_ms = current
        _acknowledge_graph_extension(session, alert, actor_pis=actor_pis, current_ts_ms=current)
    elif action == "dismiss" and before == "acknowledged":
        if normalized_reason is None:
            raise WatchError("A dismissal reason is required.")
        alert.state = "dismissed"
        alert.dismissed_by_pis = actor_pis
        alert.dismissed_ts_ms = current
        alert.dismissal_reason = normalized_reason
    elif action == "resolve" and before == "acknowledged":
        alert.state = "resolved"
        alert.resolved_by_pis = actor_pis
        alert.resolved_ts_ms = current
    elif action == "snooze" and before in {"unread", "read", "acknowledged"}:
        if snoozed_until_ms is None or snoozed_until_ms <= current:
            raise WatchError("Snooze time must be in the future.")
        alert.snooze_return_state = before
        alert.snoozed_until_ms = snoozed_until_ms
        alert.state = "snoozed"
    else:
        raise WatchError(f"Alert transition {before} -> {action} is not permitted.")
    alert.updated_ts_ms = current
    session.add(alert)
    session.flush()
    session.add(
        WatchAlertEvent(
            alert_id=int(alert.id),
            actor_pis=actor_pis,
            action=action,
            from_state=before,
            to_state=alert.state,
            reason=normalized_reason,
            details={"snoozed_until_ms": snoozed_until_ms} if action == "snooze" else {},
            created_ts_ms=current,
        )
    )
    session.commit()
    session.refresh(alert)
    append_audit_event(
        actor_pis,
        f"watch_alert.{action}",
        f"alert:{alert.id}",
        {"from_state": before, "to_state": alert.state, "reason": normalized_reason},
    )
    return alert


def release_snoozed_alerts(
    session: Session,
    *,
    current_ts_ms: int | None = None,
) -> list[WatchAlert]:
    current = now_ms() if current_ts_ms is None else current_ts_ms
    rows = session.exec(
        select(WatchAlert)
        .where(WatchAlert.state == "snoozed")
        .where(WatchAlert.snoozed_until_ms <= current)
        .order_by(WatchAlert.snoozed_until_ms, WatchAlert.id)
    ).all()
    for alert in rows:
        before = alert.state
        alert.state = alert.snooze_return_state or "read"
        alert.snoozed_until_ms = None
        alert.snooze_return_state = None
        alert.updated_ts_ms = current
        session.add(alert)
        session.add(
            WatchAlertEvent(
                alert_id=int(alert.id),
                actor_pis="system:watch-worker",
                action="snooze_expired",
                from_state=before,
                to_state=alert.state,
                created_ts_ms=current,
            )
        )
    if rows:
        session.commit()
        for alert in rows:
            session.refresh(alert)
            append_audit_event(
                "system:watch-worker",
                "watch_alert.snooze_expired",
                f"alert:{alert.id}",
                {"to_state": alert.state},
            )
    return list(rows)


def reassign_alert(
    session: Session,
    alert: WatchAlert,
    *,
    actor_pis: str,
    target_pis: str,
    actor_unit: str,
    target_unit: str,
    current_ts_ms: int | None = None,
) -> WatchAlert:
    if actor_unit != target_unit:
        raise WatchError("Alerts may be reassigned only within the officer's unit.")
    current = now_ms() if current_ts_ms is None else current_ts_ms
    prior = alert.assigned_to_pis
    alert.assigned_to_pis = target_pis
    alert.assigned_unit = target_unit
    alert.updated_ts_ms = current
    session.add(alert)
    session.flush()
    session.add(
        WatchAlertEvent(
            alert_id=int(alert.id),
            actor_pis=actor_pis,
            action="reassign",
            from_state=alert.state,
            to_state=alert.state,
            details={"from_pis": prior, "to_pis": target_pis, "unit": target_unit},
            created_ts_ms=current,
        )
    )
    session.commit()
    session.refresh(alert)
    append_audit_event(
        actor_pis,
        "watch_alert.reassign",
        f"alert:{alert.id}",
        {"from_pis": prior, "to_pis": target_pis, "unit": target_unit},
    )
    return alert


def run_wallet_watch_cycle(
    session: Session,
    *,
    worker_id: str,
    limit: int | None = None,
    enabled: bool,
    current_ts_ms: int | None = None,
) -> WatchCycleResult:
    if not worker_id.strip():
        raise WatchError("Worker id is required for watch polling.")
    if not enabled:
        return WatchCycleResult(False, None, 0, 0, 0, (), 0, ())
    current = now_ms() if current_ts_ms is None else current_ts_ms
    poll_limit = settings.watch_poll_batch_size if limit is None else limit
    if poll_limit < 0:
        raise WatchError("Watch poll limit must be non-negative.")
    sync = synchronise_automatic_watches(session, current_ts_ms=current)
    release_snoozed_alerts(session, current_ts_ms=current)
    due = [
        watch
        for watch in session.exec(
            select(WalletWatch)
            .where(WalletWatch.status == "active")
            .where(WalletWatch.next_poll_ts_ms <= current)
        ).all()
    ]
    due.sort(
        key=lambda watch: (
            TIER_ORDER.get(watch.tier, 9),
            -int(watch.pinned),
            -watch.officer_priority,
            -watch.attributed_value_base,
            int(watch.id),
        )
    )
    results = [
        poll_wallet_watch(session, watch, worker_id=worker_id, current_ts_ms=current)
        for watch in due[:poll_limit]
    ]
    return WatchCycleResult(
        enabled=True,
        sync=sync,
        watches_polled=len(results),
        observed_events=sum(result.observed_events for result in results),
        alerts_created=sum(result.alerts_created for result in results),
        snapshot_ids=tuple(snapshot_id for result in results for snapshot_id in result.snapshot_ids),
        deferred_by_budget=sum(result.error_kind == "interactive_trace_reserve" for result in results),
        failures=tuple(
            {"watch_id": result.watch_id, "error_kind": result.error_kind}
            for result in results
            if result.error_kind
        ),
    )


def poll_wallet_watch(
    session: Session,
    watch: WalletWatch,
    *,
    worker_id: str,
    current_ts_ms: int | None = None,
) -> WatchPollResult:
    if watch.id is None or watch.status != "active":
        raise WatchError("Only persisted active watches may be polled.")
    if watch.chain_family != "TRON" or watch.chain_network != "mainnet":
        raise WatchError("Stage 6 polling supports TRON mainnet watches only.")
    current = now_ms() if current_ts_ms is None else current_ts_ms
    cursor = dict(watch.poll_cursor or {})
    last_observed = cursor.get("last_observed_ts_ms")
    prior_refs = set(cursor.get("recent_event_refs") or [])
    baseline_complete = bool(cursor.get("baseline_complete"))
    latest = _latest_snapshot(session, watch.case_id)
    case = session.get(Case, watch.case_id)
    if case is None:
        raise WatchError("Watch case was not found.")
    snapshot_ids: list[int] = []
    audit_rows: list[tuple[WatchAlert, TraceSnapshot, int]] = []
    with provider_evidence_scope(
        root=settings.var_dir,
        case_id=watch.case_id,
        snapshot_id=latest.id if latest else None,
    ) as capture:
        try:
            with provider_request_context("watch_poll"):
                raw_events = tron.fetch_trc20_transfers(
                    watch.address,
                    min_timestamp=int(last_observed) + 1 if last_observed is not None else None,
                    max_timestamp=current,
                    contract_address=(
                        watch.asset_identifier
                        if watch.asset_identifier.startswith("T")
                        else tron.TRON_MAINNET_USDT
                    ),
                )
            normalized = [_normalise_watch_event(raw) for raw in raw_events]
            normalized.sort(
                key=lambda event: (
                    int(event["ts_ms"]),
                    str(event["txid"]),
                    int(event.get("event_index") or 0),
                )
            )
            event_refs = [_event_ref(event) for event in normalized]
            new_events = (
                [event for event in normalized if _event_ref(event) not in prior_refs]
                if baseline_complete
                else []
            )
            was_dormant = bool(
                watch.last_movement_ts_ms is not None
                and current - watch.last_movement_ts_ms
                >= settings.watch_dormant_after_seconds * 1000
            )
            for event in new_events:
                trigger = classify_watch_trigger(
                    watch,
                    event,
                    was_dormant=was_dormant,
                    material_transfer_base=settings.watch_material_transfer_base,
                )
                watch.last_movement_ts_ms = max(
                    watch.last_movement_ts_ms or 0,
                    int(event["ts_ms"]),
                )
                watch.tier = "hot"
                if trigger is None:
                    continue
                alert, new_snapshot, prior_export_count = _create_alert_and_extension(
                    session,
                    case=case,
                    watch=watch,
                    event=event,
                    trigger=trigger,
                    current_ts_ms=current,
                )
                snapshot_ids.append(int(new_snapshot.id))
                audit_rows.append((alert, new_snapshot, prior_export_count))
            observed_times = [int(event["ts_ms"]) for event in normalized]
            if observed_times:
                cursor["last_observed_ts_ms"] = max(observed_times)
            cursor["recent_event_refs"] = event_refs[-200:]
            cursor["baseline_complete"] = True
            cursor["last_worker_id"] = worker_id
            watch.poll_cursor = cursor
            watch.last_checked_ts_ms = current
            watch.last_error_kind = None
            watch.last_error_ts_ms = None
            watch.next_poll_ts_ms = current + _poll_interval_ms(watch.tier)
            watch.updated_ts_ms = current
            session.add(watch)
            if capture.records:
                coverage_snapshot_id = snapshot_ids[-1] if snapshot_ids else (latest.id if latest else None)
                persist_provider_coverage(
                    session,
                    capture.records,
                    snapshot_id=coverage_snapshot_id,
                )
            session.commit()
        except (tron.ProviderConfigurationError, tron.ProviderResponseError) as exc:
            session.rollback()
            current_watch = session.get(WalletWatch, watch.id)
            if current_watch is None:
                raise
            current_watch.last_checked_ts_ms = current
            current_watch.last_error_kind = str(
                getattr(exc, "error_kind", None) or exc.__class__.__name__
            )
            current_watch.last_error_ts_ms = current
            current_watch.next_poll_ts_ms = current + max(
                _poll_interval_ms(current_watch.tier),
                int(getattr(exc, "retry_after_ms", None) or 0),
            )
            current_watch.updated_ts_ms = current
            session.add(current_watch)
            if capture.records:
                persist_provider_coverage(
                    session,
                    capture.records,
                    snapshot_id=latest.id if latest else None,
                )
            session.commit()
            return WatchPollResult(
                watch_id=int(current_watch.id),
                status="deferred",
                observed_events=0,
                alerts_created=0,
                snapshot_ids=(),
                error_kind=current_watch.last_error_kind,
            )
    for alert, new_snapshot, prior_export_count in audit_rows:
        append_audit_event(
            "system:watch-worker",
            "case.watch_graph_extended",
            case.ack_no,
            {
                "watch_id": watch.id,
                "alert_id": alert.id,
                "event_ref": alert.event_ref,
                "snapshot_id": new_snapshot.id,
                "parent_snapshot_id": new_snapshot.parent_snapshot_id,
                "prior_export_count": prior_export_count,
                "observation_only": True,
            },
        )
    return WatchPollResult(
        watch_id=int(watch.id),
        status="baseline" if not baseline_complete else "complete",
        observed_events=len(new_events),
        alerts_created=len(audit_rows),
        snapshot_ids=tuple(snapshot_ids),
    )


def classify_watch_trigger(
    watch: WalletWatch,
    event: dict[str, Any],
    *,
    was_dormant: bool,
    material_transfer_base: int,
) -> dict[str, Any] | None:
    reasons: list[dict[str, str]] = []
    if event.get("registry_deposit_reviewed") is True:
        reasons.append({"kind": "registry_deposit_observed", "priority": "highest"})
    if event.get("sanctions_match") is True or event.get("reported_abuse_match") is True:
        reasons.append({"kind": "adverse_source_match", "priority": "highest"})
    if event.get("protocol_boundary") is True:
        reasons.append({"kind": "protocol_boundary_observed", "priority": "high"})
    outbound = str(event.get("source") or "") == watch.address
    if outbound and int(event.get("amount_base") or 0) >= material_transfer_base:
        reasons.append({"kind": "material_outbound_observed", "priority": "high"})
    if was_dormant:
        reasons.append({"kind": "dormant_activity_observed", "priority": "medium"})
    if outbound:
        reasons.append({"kind": "outbound_observed", "priority": "low"})
    if not reasons:
        return None
    reasons.sort(key=lambda row: -PRIORITY_ORDER[row["priority"]])
    selected = reasons[0]
    return {
        "kind": selected["kind"],
        "priority": selected["priority"],
        "copy": ALERT_COPY[selected["kind"]],
        "reasons": reasons,
    }


def watch_status(session: Session) -> dict[str, Any]:
    watches = session.exec(select(WalletWatch).order_by(WalletWatch.id)).all()
    alerts = session.exec(select(WatchAlert).order_by(WatchAlert.id)).all()
    return {
        "schema": "trinetra.wallet_watch_status/1",
        "watchlist": {
            "cap": settings.watchlist_cap,
            "active": sum(watch.status == "active" for watch in watches),
            "capacity_wait": sum(watch.status == "capacity_wait" for watch in watches),
            "paused": sum(watch.status == "paused" for watch in watches),
            "by_tier": {
                tier: sum(watch.status == "active" and watch.tier == tier for watch in watches)
                for tier in ("hot", "standard", "cold")
            },
        },
        "alerts": {
            state: sum(alert.state == state for alert in alerts)
            for state in ("unread", "read", "acknowledged", "snoozed", "dismissed", "resolved")
        },
        "delivery": "in_app_only",
        "email_enabled": False,
        "messaging_enabled": False,
    }


def _create_alert_and_extension(
    session: Session,
    *,
    case: Case,
    watch: WalletWatch,
    event: dict[str, Any],
    trigger: dict[str, Any],
    current_ts_ms: int,
) -> tuple[WatchAlert, TraceSnapshot, int]:
    event_ref = _event_ref(event)
    existing = session.exec(
        select(WatchAlert)
        .where(WatchAlert.watch_id == watch.id)
        .where(WatchAlert.event_ref == event_ref)
    ).first()
    if existing:
        snapshot = session.get(TraceSnapshot, existing.snapshot_id)
        if snapshot is None:
            raise WatchError("Existing watch alert has no retained snapshot.")
        return existing, snapshot, 0
    alert = WatchAlert(
        watch_id=int(watch.id),
        case_id=int(case.id),
        event_ref=event_ref,
        trigger_kind=str(trigger["kind"]),
        trigger_reasons=list(trigger["reasons"]),
        priority=str(trigger["priority"]),
        state="unread",
        source_address=str(event["source"]),
        destination_address=str(event["destination"]),
        amount_base=int(event["amount_base"]),
        asset_identifier=watch.asset_identifier,
        txid=str(event["txid"]),
        event_index=_optional_int(event.get("event_index")),
        block_height=_optional_int(event.get("block")),
        observed_ts_ms=int(event["ts_ms"]),
        retrieval_ts_ms=_optional_int(event.get("retrieval_ts_ms")),
        provider_request_ref=event.get("provider_request_ref"),
        raw_sha256=event.get("raw_sha256"),
        observation_only=True,
        assigned_unit=case.jurisdiction,
        created_ts_ms=current_ts_ms,
        updated_ts_ms=current_ts_ms,
    )
    session.add(alert)
    session.flush()
    alert.node_ref = f"watch-{alert.id}"
    parent = _latest_snapshot(session, int(case.id))
    if parent is None:
        raise WatchError("A watch hit cannot extend a case without a trace snapshot.")
    extension = {
        "schema": "trinetra.watch_graph_extension/1",
        "node_ref": alert.node_ref,
        "event_ref": event_ref,
        "watch_id": watch.id,
        "alert_id": alert.id,
        "source_address": event["source"],
        "destination_address": event["destination"],
        "amount_base": int(event["amount_base"]),
        "asset_symbol": watch.asset_symbol,
        "txid": event["txid"],
        "event_index": event.get("event_index"),
        "block_height": event.get("block"),
        "observed_ts_ms": int(event["ts_ms"]),
        "retrieval_ts_ms": event.get("retrieval_ts_ms"),
        "provider_request_ref": event.get("provider_request_ref"),
        "raw_sha256": event.get("raw_sha256"),
        "trigger_kind": trigger["kind"],
        "priority": trigger["priority"],
        "observation": trigger["copy"],
        "observation_only": True,
        "new_since_last_view": True,
    }
    result = deepcopy(parent.result_json)
    result.setdefault("watch_extensions", []).append(extension)
    result["watch_update"] = {
        "parent_snapshot_id": parent.id,
        "event_ref": event_ref,
        "observation_only": True,
        "created_ts_ms": current_ts_ms,
    }
    result["closed_ts"] = current_ts_ms
    result["explainability"] = trace_explainability(result)
    result.pop("sha256", None)
    result["sha256"] = sha256_json(result)
    new_snapshot = TraceSnapshot(
        case_id=int(case.id),
        version=parent.version + 1,
        snapshot_schema=parent.snapshot_schema,
        status=parent.status,
        cache_identity=sha256_json(
            {"kind": "watch_extension", "parent_sha256": parent.sha256, "event_ref": event_ref}
        ),
        parent_snapshot_id=parent.id,
        result_json=result,
        sha256=result["sha256"],
        chain_family=parent.chain_family,
        chain_network=parent.chain_network,
        asset_symbol=parent.asset_symbol,
        asset_decimals=parent.asset_decimals,
        bridge_candidate=deepcopy(parent.bridge_candidate),
        case_link_evidence=deepcopy(parent.case_link_evidence),
        started_ts_ms=current_ts_ms,
        closed_ts_ms=current_ts_ms,
    )
    session.add(new_snapshot)
    session.flush()
    parent.superseded_by_id = new_snapshot.id
    session.add(parent)
    alert.snapshot_id = new_snapshot.id
    session.add(alert)
    session.add(
        CanonicalTraceEvent(
            case_id=int(case.id),
            snapshot_id=int(new_snapshot.id),
            chain_family=watch.chain_family,
            chain_network=watch.chain_network,
            block_height=_optional_int(event.get("block")),
            txid=str(event["txid"]),
            tx_index=None,
            event_index=_optional_int(event.get("event_index")),
            output_index=None,
            asset_identifier=watch.asset_identifier,
            source_address=str(event["source"]),
            destination_address=str(event["destination"]),
            amount_base=int(event["amount_base"]),
            success=True,
            finality="provider_confirmed",
            evidence_ref=event_ref,
            raw_sha256=str(event.get("raw_sha256") or sha256_json(event)),
            observed_ts_ms=int(event["ts_ms"]),
            created_ts_ms=current_ts_ms,
        )
    )
    session.add(
        TraceEvent(
            snapshot_id=int(new_snapshot.id),
            seq=1,
            event_type="watch_observation",
            data=extension,
            created_ts_ms=current_ts_ms,
        )
    )
    session.add(
        TraceEvent(
            snapshot_id=int(new_snapshot.id),
            seq=2,
            event_type="done",
            data={"sha256": new_snapshot.sha256, "closed_ts": current_ts_ms},
            created_ts_ms=current_ts_ms,
        )
    )
    graph_extension = WatchGraphExtension(
        case_id=int(case.id),
        watch_id=int(watch.id),
        alert_id=int(alert.id),
        source_snapshot_id=parent.id,
        new_snapshot_id=int(new_snapshot.id),
        event_ref=event_ref,
        node_ref=str(alert.node_ref),
        event_json=extension,
        new_since_last_view=True,
        created_ts_ms=current_ts_ms,
    )
    session.add(graph_extension)
    case.updated_ts_ms = current_ts_ms
    session.add(case)
    prior_exports = read_audit_events(
        subject=case.ack_no,
        actions={"artifact.export_manifest", "artifact.export_bundle"},
    )
    return alert, new_snapshot, len(prior_exports)


def _acknowledge_graph_extension(
    session: Session,
    alert: WatchAlert,
    *,
    actor_pis: str,
    current_ts_ms: int,
) -> None:
    extension = session.exec(
        select(WatchGraphExtension).where(WatchGraphExtension.alert_id == alert.id)
    ).first()
    if extension is None:
        return
    extension.new_since_last_view = False
    extension.acknowledged_by_pis = actor_pis
    extension.acknowledged_ts_ms = current_ts_ms
    session.add(extension)


def _rebalance_watchlist(
    session: Session,
    *,
    cap: int,
    current_ts_ms: int,
) -> tuple[list[WalletWatch], list[WalletWatch], list[WalletWatch]]:
    if cap < 0:
        raise WatchError("Watchlist cap must be non-negative.")
    rows = session.exec(select(WalletWatch).order_by(WalletWatch.id)).all()
    paused = [watch for watch in rows if watch.status == "paused"]
    candidates = [watch for watch in rows if watch.status != "paused"]
    candidates.sort(
        key=lambda watch: (
            -int(watch.pinned),
            -watch.officer_priority,
            TIER_ORDER.get(watch.tier, 9),
            -watch.attributed_value_base,
            watch.created_ts_ms,
            int(watch.id),
        )
    )
    active = candidates[:cap]
    waiting = candidates[cap:]
    for watch in active:
        watch.status = "active"
        watch.capacity_reason = None
        watch.next_poll_ts_ms = (
            current_ts_ms if watch.next_poll_ts_ms is None else watch.next_poll_ts_ms
        )
        watch.updated_ts_ms = current_ts_ms
        session.add(watch)
    for watch in waiting:
        watch.status = "capacity_wait"
        watch.capacity_reason = "watchlist_cap"
        watch.updated_ts_ms = current_ts_ms
        session.add(watch)
    return active, waiting, paused


def _watch_candidates(case: Case, snapshot: TraceSnapshot) -> list[tuple[str, int]]:
    values: dict[str, int] = {case.reported_address: case.amount_reported_base}
    for hop in snapshot.result_json.get("hops") or []:
        address = str(hop.get("address") or "").strip()
        amount = hop.get("value_base")
        if address and amount is not None:
            values[address] = max(values.get(address, 0), int(amount))
    return sorted(values.items(), key=lambda item: (-item[1], item[0]))


def _tier_for(
    case: Case,
    attributed_value_base: int,
    last_movement_ts_ms: int | None,
    current_ts_ms: int,
) -> WatchTier:
    if case.stage in CLOSED_CASE_STAGES:
        return "cold"
    if (
        attributed_value_base >= settings.watch_hot_value_base
        or (
            last_movement_ts_ms is not None
            and current_ts_ms - last_movement_ts_ms
            < settings.watch_dormant_after_seconds * 1000
        )
    ):
        return "hot"
    return "standard"


def _poll_interval_ms(tier: str) -> int:
    seconds = {
        "hot": settings.watch_hot_interval_seconds,
        "standard": settings.watch_standard_interval_seconds,
        "cold": settings.watch_cold_interval_seconds,
    }.get(tier, settings.watch_standard_interval_seconds)
    return max(1, seconds) * 1000


def _find_watch(
    session: Session,
    *,
    case_id: int,
    address: str,
    asset_identifier: str,
) -> WalletWatch | None:
    return session.exec(
        select(WalletWatch)
        .where(WalletWatch.case_id == case_id)
        .where(WalletWatch.address == address)
        .where(WalletWatch.asset_identifier == asset_identifier)
    ).first()


def _latest_snapshot(session: Session, case_id: int) -> TraceSnapshot | None:
    return session.exec(
        select(TraceSnapshot)
        .where(TraceSnapshot.case_id == case_id)
        .order_by(TraceSnapshot.id.desc())
    ).first()


def _asset_identifier(case: Case, snapshot: TraceSnapshot | None) -> str:
    if snapshot:
        contract = (snapshot.result_json.get("asset") or {}).get("contract")
        if contract:
            return str(contract)
    if case.chain_family == "TRON" and case.asset_symbol == "USDT":
        return tron.TRON_MAINNET_USDT
    return case.asset_symbol


def _normalise_watch_event(raw: dict[str, Any]) -> dict[str, Any]:
    event = tron.normalise(raw)
    for key in (
        "registry_deposit_reviewed",
        "sanctions_match",
        "reported_abuse_match",
        "protocol_boundary",
    ):
        if raw.get(key) is not None:
            event[key] = raw[key]
    return event


def _event_ref(event: dict[str, Any]) -> str:
    return (
        f"TRON:mainnet:{event['txid']}:"
        f"{int(event.get('event_index') or 0)}:{event['destination']}"
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def serialize_cycle(result: WatchCycleResult) -> dict[str, Any]:
    payload = asdict(result)
    if result.sync is not None:
        payload["sync"] = asdict(result.sync)
    return payload
