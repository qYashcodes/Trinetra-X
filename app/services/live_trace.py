from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlmodel import Session, select

from app.engine_bridge import TraceParams
from app.models import CanonicalTraceEvent, FrontierItem, SourceCoverage, TraceSnapshot
from app.services.behavior import assign_evidence_band, band_methodology, extract_behavioral_features
from app.services.capabilities import capability_matrix
from app.services.time import IST, now_ms


class LiveTraceInputError(ValueError):
    pass


DEFERRAL_LABELS = {
    "value_floor": "Below the attributed-value floor",
    "depth_budget": "Hop-depth bound reached",
    "time_window": "Outside the selected time window",
    "address_budget": "Address-expansion budget reached",
    "breadth_cap": "Onward-branch cap reached",
    "cycle_detected": "Cycle already observed in this path",
    "provider_backoff": "Provider requested a timed retry",
}

HEURISTIC_DISCLAIMER = (
    "projected based on behavioural heuristics but needs officer input before giving a final verdict"
)


def parse_amount_base(value: str, *, decimals: int = 6) -> int:
    try:
        amount = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise LiveTraceInputError("Enter a valid USDT amount.") from exc
    scaled = amount * (Decimal(10) ** decimals)
    if amount <= 0 or scaled != scaled.to_integral_value():
        raise LiveTraceInputError(
            f"Amount must be positive with no more than {decimals} decimal places."
        )
    return int(scaled)


def parse_ist_timestamp(value: str | None) -> int | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LiveTraceInputError("Enter a valid payment date and time in IST.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return int(parsed.timestamp() * 1000)


def parse_trace_params(values: dict[str, Any]) -> TraceParams:
    try:
        max_depth = int(values.get("max_depth", 5))
        time_window_hours = int(values.get("time_window_hours", 8))
        value_floor_share = Decimal(str(values.get("value_floor_share", "0.02")))
        breadth_cap = int(values.get("breadth_cap", 3))
        address_budget = int(values.get("address_budget", 60))
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise LiveTraceInputError("Traversal bounds must be valid numbers.") from exc
    strategy = str(values.get("strategy") or "dominant_fund_flow")
    if not 1 <= max_depth <= 10:
        raise LiveTraceInputError("Hop depth must be between 1 and 10.")
    if not 1 <= time_window_hours <= 168:
        raise LiveTraceInputError("Time window must be between 1 and 168 hours.")
    if value_floor_share < 0 or value_floor_share > Decimal("1"):
        raise LiveTraceInputError("Value floor share must be between 0 and 1.")
    if not 1 <= breadth_cap <= 10:
        raise LiveTraceInputError("Branch cap must be between 1 and 10.")
    if not 1 <= address_budget <= 500:
        raise LiveTraceInputError("Address budget must be between 1 and 500.")
    if strategy not in {"dominant_fund_flow", "value_weighted"}:
        raise LiveTraceInputError("Select a supported traversal strategy.")
    return TraceParams(
        max_depth=max_depth,
        time_window_hours=time_window_hours,
        value_floor_share=value_floor_share,
        breadth_cap=breadth_cap,
        address_budget=address_budget,
        strategy=strategy,
        trace_mode="live",
        include_unconfirmed=bool(values.get("include_unconfirmed")),
    )


def snapshot_runtime_status(session: Session, snapshot: TraceSnapshot) -> dict[str, Any]:
    frontier = session.exec(
        select(FrontierItem)
        .where(FrontierItem.snapshot_id == snapshot.id)
        .order_by(FrontierItem.depth.asc(), FrontierItem.id.asc())
    ).all()
    rows = []
    for item in frontier:
        cursor = dict(item.resume_cursor or {})
        reason = item.deferral_reason
        rows.append(
            {
                "id": item.id,
                "state": item.state,
                "depth": item.depth,
                "address": cursor.get("address") or cursor.get("destination_address"),
                "value_base": item.priority_base,
                "deferral_reason": reason,
                "deferral_label": DEFERRAL_LABELS.get(reason, "Deferred for review") if reason else None,
                "next_retry_ts_ms": cursor.get("next_retry_ts_ms"),
                "retry_attempts": cursor.get("retry_attempts"),
                "retry_exhausted": bool(cursor.get("retry_exhausted")),
                "provider": cursor.get("provider"),
            }
        )
    canonical_events = session.exec(
        select(CanonicalTraceEvent)
        .where(CanonicalTraceEvent.snapshot_id == snapshot.id)
        .order_by(
            CanonicalTraceEvent.tx_index,
            CanonicalTraceEvent.event_index,
            CanonicalTraceEvent.id,
        )
    ).all()
    source_coverage = session.exec(
        select(SourceCoverage)
        .where(SourceCoverage.snapshot_id == snapshot.id)
        .order_by(SourceCoverage.retrieval_ts_ms, SourceCoverage.id)
    ).all()
    freshness = dict(snapshot.result_json.get("data_freshness") or {})
    as_of = freshness.get("chain_data_as_of_ms")
    stale_after_value = freshness.get("stale_after_ms")
    stale_after = 0 if stale_after_value is None else int(stale_after_value)
    stale_cutoff = now_ms() - stale_after
    stale = bool(as_of is not None and stale_after >= 0 and now_ms() > int(as_of) + stale_after)
    return {
        "snapshot_id": snapshot.id,
        "snapshot_status": snapshot.status,
        "mode": snapshot.result_json.get("engine", {}).get("mode", "fixture"),
        "frontier": rows,
        "frontier_counts": {
            state: sum(row["state"] == state for row in rows)
            for state in ("queued", "leased", "deferred", "completed")
        },
        "provider_backoff": [
            row for row in rows if row["deferral_reason"] == "provider_backoff"
        ],
        "stop_summary": _stop_summary(snapshot, rows),
        "provider_evidence": _provider_evidence_summary(canonical_events, source_coverage),
        "behavioral_projection": _behavioral_projection(snapshot, canonical_events),
        "capability_snapshot": _capability_snapshot(),
        "data_freshness": {
            **freshness,
            "stale": stale,
            "stale_cutoff_ms": stale_cutoff,
        },
        "unconfirmed_observations": list(
            snapshot.result_json.get("unconfirmed_observations") or []
        ),
    }


def _stop_summary(snapshot: TraceSnapshot, frontier_rows: list[dict[str, Any]]) -> dict[str, Any]:
    terminal = dict(snapshot.result_json.get("terminal") or {})
    provider_backoff = [
        row for row in frontier_rows if row.get("deferral_reason") == "provider_backoff"
    ]
    retry_ready = [
        row
        for row in provider_backoff
        if row.get("next_retry_ts_ms") is not None
        and int(row["next_retry_ts_ms"]) <= now_ms()
    ]
    if provider_backoff:
        return {
            "label": "Provider retry ready" if retry_ready else "Provider retry pending",
            "tone": "warning",
            "primary_reason": "provider_backoff",
            "detail": (
                "A confirmed onward branch is retained, but TronGrid asked TRINETRA to retry it later. "
                "The snapshot stays incomplete until the branch is resumed."
            ),
            "terminal_kind": terminal.get("kind"),
            "terminal_label": _labelize(str(terminal.get("kind") or "unknown")),
            "active_deferrals": len(provider_backoff),
        }
    queued = [row for row in frontier_rows if row.get("state") == "queued"]
    if queued:
        return {
            "label": "More frontier work queued",
            "tone": "info",
            "primary_reason": "queued_frontier",
            "detail": (
                "The trace recorded confirmed onward work that was not expanded in this snapshot's "
                "sealed bounds."
            ),
            "terminal_kind": terminal.get("kind"),
            "terminal_label": _labelize(str(terminal.get("kind") or "unknown")),
            "active_deferrals": len(queued),
        }
    return {
        "label": _labelize(str(terminal.get("kind") or "unknown")),
        "tone": "neutral",
        "primary_reason": str(terminal.get("kind") or "unknown"),
        "detail": str(terminal.get("note") or "Trace closed without a stronger outcome."),
        "terminal_kind": terminal.get("kind"),
        "terminal_label": _labelize(str(terminal.get("kind") or "unknown")),
        "active_deferrals": 0,
    }


def _provider_evidence_summary(
    canonical_events: list[CanonicalTraceEvent],
    source_coverage: list[SourceCoverage],
) -> dict[str, Any]:
    raw_hashes = {
        row.raw_sha256
        for row in canonical_events
        if row.raw_sha256 is not None and str(row.raw_sha256).strip()
    }
    providers = sorted({row.provider for row in source_coverage if row.provider})
    request_refs = []
    for row in source_coverage:
        watermark = dict(row.observed_watermark or {})
        request_ref = watermark.get("request_ref")
        if request_ref:
            request_refs.append(str(request_ref))
    latest_retrieval = max(
        (int(row.retrieval_ts_ms) for row in source_coverage if row.retrieval_ts_ms is not None),
        default=None,
    )
    return {
        "canonical_event_count": len(canonical_events),
        "coverage_count": len(source_coverage),
        "raw_hash_count": len(raw_hashes),
        "providers": providers,
        "latest_retrieval_ts_ms": latest_retrieval,
        "request_refs": list(dict.fromkeys(request_refs))[:6],
        "gap_count": sum(len(row.gaps or []) for row in source_coverage),
        "conflict_count": sum(len(row.conflicts or []) for row in source_coverage),
    }


def _behavioral_projection(
    snapshot: TraceSnapshot,
    canonical_events: list[CanonicalTraceEvent],
) -> dict[str, Any]:
    address = str(
        (snapshot.result_json.get("case") or {}).get("reported_address")
        or snapshot.result_json.get("seed", {}).get("address")
        or ""
    )
    transfers = [
        {
            "source": row.source_address,
            "destination": row.destination_address,
            "amount_base": row.amount_base,
            "ts_ms": row.observed_ts_ms,
            "evidence_ref": row.evidence_ref,
        }
        for row in canonical_events
        if row.source_address and row.destination_address and row.observed_ts_ms is not None
    ]
    if not address:
        return _empty_projection("No reported address was recorded for behavioural projection.")
    try:
        features = extract_behavioral_features(
            address,
            transfers,
            reported_addresses={address},
            registry_addresses=set(),
        )
        band = assign_evidence_band(features)
    except ValueError as exc:
        return _empty_projection(str(exc))
    feature_rows = []
    for item in features["items"]:
        values = dict(item.get("values") or {})
        feature_rows.append(
            {
                "key": item.get("key"),
                "label": item.get("label"),
                "summary": _feature_summary(str(item.get("key") or ""), values),
                "evidence_count": len(item.get("evidence_refs") or []),
            }
        )
    classes = [
        _labelize(str(item))
        for item in band.get("observed_classes", [])
    ]
    if not classes and band.get("matched_rules"):
        classes = [_labelize(str(item)) for item in band.get("matched_rules", [])]
    if not classes:
        classes = [_labelize(str(band.get("band") or "unknown"))]
    return {
        "enabled": True,
        "address": address,
        "label": _projection_label(str(band.get("band") or "unknown")),
        "disclaimer": HEURISTIC_DISCLAIMER,
        "band": band.get("band"),
        "reason": band.get("reason"),
        "observed_classes": classes,
        "insufficiency_reasons": band.get("insufficiency_reasons", []),
        "event_count": features.get("observed_event_count", 0),
        "inbound_event_count": features.get("inbound_event_count", 0),
        "outbound_event_count": features.get("outbound_event_count", 0),
        "features": feature_rows,
        "burner_status": band_methodology()["burner_classification"],
    }


def _empty_projection(reason: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "address": "",
        "label": "Behavioural projection unavailable",
        "disclaimer": HEURISTIC_DISCLAIMER,
        "band": "unknown",
        "reason": reason,
        "observed_classes": [],
        "insufficiency_reasons": [reason],
        "event_count": 0,
        "inbound_event_count": 0,
        "outbound_event_count": 0,
        "features": [],
        "burner_status": band_methodology()["burner_classification"],
    }


def _capability_snapshot() -> list[dict[str, str]]:
    wanted = {
        "live_tron_seed_verification",
        "live_tron_usdt",
        "supervised_frontier_worker",
        "wallet_watch",
        "custody_provider_import",
        "bridge_defi_decoders",
        "evm_token_trace",
        "bitcoin_outpoint_trace",
        "action_workflow",
    }
    return [item for item in capability_matrix() if item["key"] in wanted]


def _projection_label(band: str) -> str:
    labels = {
        "alert": "Local record match observed",
        "medium": "Behavioural pattern observed",
        "low": "No documented pattern rule matched",
        "unknown": "Insufficient confirmed history",
    }
    return labels.get(band, "Behavioural projection unavailable")


def _feature_summary(key: str, values: dict[str, Any]) -> str:
    if key == "pass_through_pattern":
        return (
            f"in {values.get('inbound_total_base', 0)} / out "
            f"{values.get('outbound_total_base', 0)} base units"
        )
    if key == "peel_signature":
        return (
            f"largest outflow {values.get('largest_outgoing_share_bp', 0)} bp; "
            f"{values.get('smaller_outgoing_count', 0)} smaller branches"
        )
    if key == "consolidation":
        return (
            f"{values.get('distinct_inbound_sources', 0)} sources to "
            f"{values.get('distinct_outgoing_destinations', 0)} destinations"
        )
    if key == "sweep_timing":
        return f"median hold {values.get('median_hold_ms')} ms"
    if key == "destination_consistency":
        return f"top destination share {values.get('top_destination_share_bp', 0)} bp"
    if key == "resting_balance":
        return f"retained share {values.get('retained_share_bp', 0)} bp"
    if key == "forward_ratio":
        return f"outbound/inbound {values.get('outbound_to_inbound_bp')} bp"
    if key == "address_lifetime":
        return f"{values.get('observed_event_count', 0)} events over {values.get('lifetime_ms', 0)} ms"
    if key == "counterparty_exposure":
        return (
            f"{values.get('reported_address_event_count', 0)} local-case events; "
            f"{values.get('registry_address_event_count', 0)} registry events"
        )
    return "Recorded from confirmed transfer history"


def _labelize(value: str) -> str:
    return value.replace("_", " ").strip().title()
