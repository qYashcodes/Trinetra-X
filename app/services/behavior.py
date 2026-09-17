from __future__ import annotations

from typing import Any


FEATURE_REVISION = "trinetra.behavioural_features/1"
BAND_RULE_REVISION = "trinetra.evidence_band_rules/1"
MINIMUM_ACTIVITY_EVENTS = 5


def extract_behavioral_features(
    address: str,
    transfers: list[dict[str, Any]],
    *,
    reported_addresses: set[str] | None = None,
    registry_addresses: set[str] | None = None,
) -> dict[str, Any]:
    normalized_address = address.strip().lower()
    reported = {item.lower() for item in reported_addresses or set()}
    registry = {item.lower() for item in registry_addresses or set()}
    events = _address_events(normalized_address, transfers)
    inbound = [event for event in events if event["direction"] == "inbound"]
    outbound = [event for event in events if event["direction"] == "outbound"]
    inbound_total = sum(event["amount_base"] for event in inbound)
    outbound_total = sum(event["amount_base"] for event in outbound)
    retained_base = max(0, inbound_total - outbound_total)
    pairings = _fifo_pairings(events)
    latencies = sorted(pair["hold_ms"] for pair in pairings)
    principal_pair = sorted(
        pairings,
        key=lambda pair: (-pair["matched_base"], pair["hold_ms"], pair["outbound_ref"]),
    )[0] if pairings else None
    destination_totals = _counterparty_totals(outbound)
    top_destination, top_destination_base = _largest_counterparty(destination_totals)
    largest_outgoing = max(outbound, key=lambda event: event["amount_base"], default=None)
    all_refs = [event["evidence_ref"] for event in events]
    inbound_refs = [event["evidence_ref"] for event in inbound]
    outbound_refs = [event["evidence_ref"] for event in outbound]
    exposure_events = [
        event
        for event in events
        if event["counterparty"].lower() in reported
        or event["counterparty"].lower() in registry
    ]
    reported_exposure = [
        event for event in exposure_events if event["counterparty"].lower() in reported
    ]
    registry_exposure = [
        event for event in exposure_events if event["counterparty"].lower() in registry
    ]

    features = [
        _feature(
            "pass_through_pattern",
            "Pass-through pattern",
            {
                "inbound_total_base": inbound_total,
                "outbound_total_base": outbound_total,
                "matched_base": sum(pair["matched_base"] for pair in pairings),
                "principal_inbound_ref": principal_pair["inbound_ref"] if principal_pair else None,
                "principal_outbound_ref": principal_pair["outbound_ref"] if principal_pair else None,
                "principal_hold_ms": principal_pair["hold_ms"] if principal_pair else None,
                "retained_base": retained_base,
            },
            sorted({ref for pair in pairings for ref in (pair["inbound_ref"], pair["outbound_ref"])}),
            "FIFO matches observed inbound lots to later outbound transfers.",
        ),
        _feature(
            "peel_signature",
            "Peel signature",
            {
                "largest_outgoing_base": largest_outgoing["amount_base"] if largest_outgoing else 0,
                "largest_outgoing_share_bp": _basis_points(
                    largest_outgoing["amount_base"] if largest_outgoing else 0,
                    outbound_total,
                ),
                "smaller_outgoing_count": max(0, len(outbound) - (1 if largest_outgoing else 0)),
                "retained_base": retained_base,
            },
            outbound_refs,
            "Compares the largest observed onward transfer with all other onward transfers.",
        ),
        _feature(
            "consolidation",
            "Consolidation",
            {
                "distinct_inbound_sources": len({event["counterparty"] for event in inbound}),
                "distinct_outgoing_destinations": len(destination_totals),
                "top_destination": top_destination,
                "top_destination_outflow_base": top_destination_base,
            },
            all_refs,
            "Counts observed fan-in sources and onward destinations in the selected window.",
        ),
        _feature(
            "fan_in_out_ratio",
            "Fan-in / fan-out ratio",
            {
                "fan_in_count": len({event["counterparty"] for event in inbound}),
                "fan_out_count": len(destination_totals),
                "ratio_milli": _ratio_milli(
                    len({event["counterparty"] for event in inbound}),
                    len(destination_totals),
                ),
            },
            all_refs,
            "Reports distinct counterparties; ratio_milli is fan-in multiplied by 1000 over fan-out.",
        ),
        _feature(
            "sweep_timing",
            "Sweep timing",
            {
                "matched_pair_count": len(pairings),
                "minimum_hold_ms": latencies[0] if latencies else None,
                "median_hold_ms": _percentile(latencies, 50),
                "p90_hold_ms": _percentile(latencies, 90),
                "maximum_hold_ms": latencies[-1] if latencies else None,
            },
            sorted({ref for pair in pairings for ref in (pair["inbound_ref"], pair["outbound_ref"])}),
            "Uses non-negative hold intervals from deterministic FIFO lot matching.",
        ),
        _feature(
            "destination_consistency",
            "Destination consistency",
            {
                "top_destination": top_destination,
                "top_destination_outflow_base": top_destination_base,
                "outbound_total_base": outbound_total,
                "top_destination_share_bp": _basis_points(top_destination_base, outbound_total),
            },
            outbound_refs,
            "Measures the share of observed outflow sent to the most-used destination.",
        ),
        _feature(
            "resting_balance",
            "Resting balance",
            {
                "inbound_total_base": inbound_total,
                "forwarded_from_observed_inflow_base": min(inbound_total, outbound_total),
                "retained_base": retained_base,
                "retained_share_bp": _basis_points(retained_base, inbound_total),
            },
            all_refs,
            "Observed-window remainder only; it is not a provider account-balance query.",
        ),
        _feature(
            "forward_ratio",
            "Forward ratio",
            {
                "inbound_total_base": inbound_total,
                "outbound_total_base": outbound_total,
                "outbound_to_inbound_bp": _basis_points_unbounded(outbound_total, inbound_total),
            },
            all_refs,
            "Divides observed outflow by observed inflow; values may exceed 10000 basis points when opening balance is outside the window.",
        ),
        _feature(
            "address_lifetime",
            "Address lifetime",
            {
                "first_seen_ts_ms": events[0]["ts_ms"] if events else None,
                "last_seen_ts_ms": events[-1]["ts_ms"] if events else None,
                "lifetime_ms": (
                    events[-1]["ts_ms"] - events[0]["ts_ms"] if len(events) >= 2 else 0
                ),
                "observed_event_count": len(events),
            },
            all_refs,
            "Measures first-to-last activity only inside the fetched history window.",
        ),
        _feature(
            "counterparty_exposure",
            "Counterparty exposure",
            {
                "reported_address_event_count": len(reported_exposure),
                "registry_address_event_count": len(registry_exposure),
                "distinct_exposed_counterparties": len(
                    {event["counterparty"] for event in exposure_events}
                ),
            },
            [event["evidence_ref"] for event in exposure_events],
            "Direct transfers only; an exposure does not establish identity, intent or participation.",
        ),
    ]

    insufficiency = []
    if len(events) < MINIMUM_ACTIVITY_EVENTS:
        insufficiency.append(
            f"At least {MINIMUM_ACTIVITY_EVENTS} observed transfers are required; {len(events)} were available."
        )
    if not inbound:
        insufficiency.append("No inbound transfer was available in the selected history window.")
    if not outbound:
        insufficiency.append("No outbound transfer was available in the selected history window.")
    return {
        "schema": FEATURE_REVISION,
        "address": address,
        "observed_event_count": len(events),
        "inbound_event_count": len(inbound),
        "outbound_event_count": len(outbound),
        "sufficient_for_band": not insufficiency,
        "insufficiency_reasons": insufficiency,
        "items": features,
    }


def assign_evidence_band(
    feature_set: dict[str, Any],
    *,
    local_case_match_count: int = 0,
) -> dict[str, Any]:
    values = {item["key"]: item["values"] for item in feature_set["items"]}
    matched_rules: list[str] = []
    observed_classes: list[str] = []

    if local_case_match_count > 0:
        matched_rules.append("direct_local_case_record")
        return _band_result("alert", matched_rules, observed_classes)
    if not feature_set["sufficient_for_band"]:
        return {
            **_band_result("unknown", [], []),
            "reason": "Observed history is insufficient for behavioural banding.",
            "insufficiency_reasons": list(feature_set["insufficiency_reasons"]),
        }

    forward = values["forward_ratio"]["outbound_to_inbound_bp"]
    resting = values["resting_balance"]["retained_share_bp"]
    median_hold = values["sweep_timing"]["median_hold_ms"]
    if (
        forward is not None
        and 8000 <= forward <= 12000
        and resting is not None
        and resting <= 2000
        and median_hold is not None
        and median_hold <= 60 * 60 * 1000
    ):
        matched_rules.append("rapid_pass_through")
        observed_classes.append("pass_through_pattern_observed")

    peel = values["peel_signature"]
    if peel["largest_outgoing_share_bp"] >= 7000 and peel["smaller_outgoing_count"] >= 2:
        matched_rules.append("dominant_outflow_with_smaller_branches")
        observed_classes.append("peel_pattern_observed")

    consolidation = values["consolidation"]
    consistency = values["destination_consistency"]
    if (
        consolidation["distinct_inbound_sources"] >= 5
        and consistency["top_destination_share_bp"] >= 7000
    ):
        matched_rules.append("multi_source_consolidation")
        observed_classes.append("consolidation_pattern_observed")

    if matched_rules:
        return _band_result("medium", matched_rules, observed_classes)
    return {
        **_band_result("low", ["no_documented_pattern_rule_matched"], []),
        "reason": "No adverse findings in available sources and no documented behavioural rule matched.",
    }


def band_methodology() -> dict[str, Any]:
    return {
        "feature_revision": FEATURE_REVISION,
        "rule_revision": BAND_RULE_REVISION,
        "minimum_activity_events": MINIMUM_ACTIVITY_EVENTS,
        "rules": [
            {
                "key": "direct_local_case_record",
                "band": "alert",
                "definition": "The address is named by an available local case or canonical trace record.",
            },
            {
                "key": "rapid_pass_through",
                "band": "medium",
                "definition": "Forward ratio 8000-12000 bp, retained share at most 2000 bp, and median FIFO hold at most one hour.",
            },
            {
                "key": "dominant_outflow_with_smaller_branches",
                "band": "medium",
                "definition": "Largest outflow is at least 7000 bp of outflow and at least two smaller outward transfers exist.",
            },
            {
                "key": "multi_source_consolidation",
                "band": "medium",
                "definition": "At least five inbound sources and at least 7000 bp of outflow reaches one destination.",
            },
        ],
        "burner_classification": {
            "status": "disabled",
            "reason": (
                "No independently reviewed threshold and false-positive benchmark for legitimate "
                "short-lived addresses is available."
            ),
        },
        "caveat": (
            "Behavioural similarity is not custody attribution, account identity, intent or participation."
        ),
    }


def _address_events(address: str, transfers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, row in enumerate(transfers):
        source = str(row.get("source") if row.get("source") is not None else "")
        destination = str(
            row.get("destination") if row.get("destination") is not None else ""
        )
        source_match = source.lower() == address
        destination_match = destination.lower() == address
        if source_match == destination_match:
            continue
        amount_value = row.get("amount_base")
        ts_value = row.get("ts_ms")
        if amount_value is None or ts_value is None:
            continue
        amount_base = int(amount_value)
        ts_ms = int(ts_value)
        if amount_base < 0 or ts_ms < 0:
            raise ValueError("Transfer amounts and timestamps must be non-negative integers.")
        direction = "outbound" if source_match else "inbound"
        counterparty = destination if source_match else source
        events.append(
            {
                "direction": direction,
                "counterparty": counterparty,
                "amount_base": amount_base,
                "ts_ms": ts_ms,
                "evidence_ref": _event_ref(row, index),
            }
        )
    return sorted(
        events,
        key=lambda event: (event["ts_ms"], event["evidence_ref"], event["direction"]),
    )


def _event_ref(row: dict[str, Any], index: int) -> str:
    txid_value = row.get("txid")
    txid = str(txid_value) if txid_value is not None else f"unknown-{index}"
    event_index = row.get("event_index")
    suffix = index if event_index is None else int(event_index)
    return f"tron:{txid}:{suffix}"


def _fifo_pairings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lots: list[dict[str, Any]] = []
    pairings: list[dict[str, Any]] = []
    for event in events:
        if event["direction"] == "inbound":
            lots.append({**event, "remaining_base": event["amount_base"]})
            continue
        remaining = event["amount_base"]
        for lot in lots:
            if remaining <= 0:
                break
            if lot["remaining_base"] <= 0 or lot["ts_ms"] > event["ts_ms"]:
                continue
            matched = min(remaining, lot["remaining_base"])
            pairings.append(
                {
                    "inbound_ref": lot["evidence_ref"],
                    "outbound_ref": event["evidence_ref"],
                    "matched_base": matched,
                    "hold_ms": event["ts_ms"] - lot["ts_ms"],
                }
            )
            lot["remaining_base"] -= matched
            remaining -= matched
    return pairings


def _counterparty_totals(events: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for event in events:
        counterparty = event["counterparty"]
        totals[counterparty] = totals.get(counterparty, 0) + event["amount_base"]
    return totals


def _largest_counterparty(totals: dict[str, int]) -> tuple[str | None, int]:
    if not totals:
        return None, 0
    address, amount = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[0]
    return address, amount


def _feature(
    key: str,
    label: str,
    values: dict[str, Any],
    evidence_refs: list[str],
    method: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "values": values,
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
        "method": method,
    }


def _basis_points(part: int, whole: int) -> int | None:
    if whole <= 0:
        return None
    return max(0, min(10000, (part * 10000) // whole))


def _basis_points_unbounded(part: int, whole: int) -> int | None:
    if whole <= 0:
        return None
    return max(0, (part * 10000) // whole)


def _ratio_milli(numerator: int, denominator: int) -> int | None:
    if denominator <= 0:
        return None
    return (numerator * 1000) // denominator


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    index = ((len(values) - 1) * percentile) // 100
    return values[index]


def _band_result(
    band: str,
    matched_rules: list[str],
    observed_classes: list[str],
) -> dict[str, Any]:
    return {
        "schema": BAND_RULE_REVISION,
        "band": band,
        "matched_rules": matched_rules,
        "observed_classes": observed_classes,
        "insufficiency_reasons": [],
    }
