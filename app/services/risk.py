from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from app.engine_bridge import ChainRef, detect_chain, resolve_vasp
from app.services.behavior import (
    assign_evidence_band,
    band_methodology,
    extract_behavioral_features,
)
from app.services.demo import demo_case
from app.services.evidence_store import provider_evidence_scope
from app.services.hash import canonical_json_bytes, sha256_bytes, sha256_json
from app.services.time import now_ms


_DEMO = demo_case()
ALERT_ADDRESS = _DEMO["terminal"]["deposit_address"]
MEDIUM_ADDRESS = _DEMO["dominant_path"][2]["address"]
RECENT_PATH_ADDRESS = _DEMO["dominant_path"][3]["address"]
LOW_ADDRESS = "TZr8kM3vXp6nQe1WdB9cLf4YtJ7sHu2Ga15"
_BAND_VALUES = {
    "alert": "High",
    "medium": "Medium",
    "low": "Low",
    "unknown": "Inconclusive",
}


def _compact_address(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


_RESULTS = {
    ALERT_ADDRESS: {
        "band": "alert",
        "band_label": "High evidence",
        "verdict_badge": "Named in an open case",
        "verdict_line": "This address appears in a complaint-linked custody finding",
        "verdict_note": (
            "The address is a Coinsphere Global deposit address that received 17,880.00 USDT "
            "along a complaint-linked trace path for NCRP/2026/MH/0084213. No notice, dispatch, "
            "acknowledgement or restraint status is asserted by this fixture result."
        ),
        "facts": [
            {"label": "First seen", "value": "2026-06-11", "tone": "default"},
            {"label": "Inbound total", "value": "1.42M USDT", "tone": "default"},
            {"label": "Attribution", "value": "Exchange deposit", "tone": "default"},
            {"label": "Dockets", "value": "4", "tone": "alert"},
        ],
        "signal_details": [
            {
                "label": "Terminal address in a complaint-linked trace path",
                "detail": "Five hops from a reported victim payment, with an unbroken direct-transfer chain.",
                "meta": "case MH/0084213",
                "tone": "alert",
            },
            {
                "label": "Cluster carries prior confirmed links",
                "detail": "214 addresses in the same cluster have earlier confirmed deposits to the same custodian.",
                "meta": "attribution rev. 08-24",
                "tone": "medium",
            },
            {
                "label": "Notice state not established",
                "detail": "No notice is recorded for this fixture result. Open the case workflow to verify any later action.",
                "meta": "workflow check required",
                "tone": "medium",
            },
        ],
        "signals": [
            "Address appears in a custody finding.",
            "Notice, dispatch and restraint status must be verified in the case workflow.",
            "Fixture evidence band is high.",
        ],
        "linked": [
            {"reference": "NCRP/2026/MH/0084213", "amount": "21,940.00 USDT", "stage": "Finding review", "tone": "medium", "href": "/notices"},
            {"reference": "NCRP/2026/MH/0081902", "amount": "112,400.00 USDT", "stage": "Linked docket", "tone": "success", "href": "/findings/1"},
            {"reference": "NCRP/2026/MH/0079411", "amount": "15,600.00 USDT", "stage": "Closed, no custody", "tone": "muted", "href": "/docket"},
            {"reference": "NCRP/2026/KA/0079922", "amount": "46,000.00 USDT", "stage": "Review pending", "tone": "medium", "href": "/docket"},
        ],
    },
    MEDIUM_ADDRESS: {
        "band": "medium",
        "band_label": "Medium evidence",
        "verdict_badge": "Behavioural match only",
        "verdict_line": "The pattern resembles a peel chain, ownership unknown",
        "verdict_note": (
            "The address splits incoming value into a large forwarded share and a small retained "
            "remainder, repeated eleven times in three days. No custodian or identity is attributed to it."
        ),
        "facts": [
            {"label": "First seen", "value": "2026-08-27", "tone": "default"},
            {"label": "Inbound total", "value": "84,300 USDT", "tone": "default"},
            {"label": "Attribution", "value": "Unattributed", "tone": "medium"},
            {"label": "Dockets", "value": "1", "tone": "default"},
        ],
        "signal_details": [
            {"label": "Peel-chain behaviour", "detail": "Eleven transfers forward 84 to 92 per cent of each deposit within minutes of receipt.", "meta": "11 events, 3 days", "tone": "medium"},
            {"label": "Short lifetime", "detail": "The address was created four days ago and has no counterparty older than that.", "meta": "age 4 d", "tone": "medium"},
            {"label": "Fixture advisory coverage", "detail": "No adverse findings in available sources at the stated fixture revision.", "meta": "checked 09:41 IST", "tone": "success"},
        ],
        "signals": ["Fixture evidence band is medium for an intermediate hop.", "Address is on a traced path in this docket."],
        "linked": [
            {"reference": "NCRP/2026/MH/0084213", "amount": "21,940.00 USDT", "stage": "Linked docket", "tone": "medium", "href": "/notices"}
        ],
    },
    LOW_ADDRESS: {
        "band": "low",
        "band_label": "Available-source result",
        "verdict_badge": "Evidence remains limited",
        "verdict_line": "No adverse findings in available sources",
        "verdict_note": (
            "No current fixture docket or fixture advisory source names this address. Absence of "
            "a record is not clearance and does not establish identity, intent or conduct."
        ),
        "facts": [
            {"label": "First seen", "value": "2025-02-04", "tone": "default"},
            {"label": "Inbound total", "value": "9,140 USDT", "tone": "default"},
            {"label": "Attribution", "value": "Unattributed", "tone": "medium"},
            {"label": "Dockets", "value": "0", "tone": "success"},
        ],
        "signal_details": [
            {"label": "No docket match", "detail": "Checked against 37 open and closed dockets held by this desk.", "meta": "0 hits", "tone": "success"},
            {"label": "No adverse findings in available sources", "detail": "No fixture advisory record matched at the stated fixture revision.", "meta": "rev. 2026-08-24", "tone": "success"},
            {"label": "Observed fixture activity", "detail": "The fixture contains small transfers to two counterparties over nineteen months.", "meta": "19 months", "tone": "default"},
        ],
        "signals": ["Absence of a record is not clearance."],
        "linked": [],
    },
}


def risk_page_data(risk_recent: list[dict] | None = None) -> dict:
    return {
        "risk_samples": [
            {"address": ALERT_ADDRESS, "label": _compact_address(ALERT_ADDRESS), "tone": "alert"},
            {"address": MEDIUM_ADDRESS, "label": _compact_address(MEDIUM_ADDRESS), "tone": "medium"},
            {"address": LOW_ADDRESS, "label": "TZr8kM…Ga15", "tone": "success"},
        ],
        "risk_recent": list(risk_recent or []),
    }


def _apply_notice_state(result: dict, notice_state: dict | None) -> None:
    """Bind legal-workflow copy to persisted state without inferring restraint or guilt."""
    status = str((notice_state or {}).get("status") or "not_recorded")
    notice_no = str((notice_state or {}).get("notice_no") or "")
    signal = result["signal_details"][2]
    linked = result["linked"][0]

    if status == "draft":
        result["verdict_line"] = "A draft notice is recorded for this address"
        result["verdict_note"] = (
            f"TRINETRA records draft notice {notice_no}. It has not been countersigned or "
            "dispatched, and remains an investigative-aid workflow record."
        )
        signal.update(
            label="Notice draft recorded",
            detail="The draft remains behind countersignature and dispatch controls.",
            meta=notice_no,
            tone="medium",
        )
        result["signals"][1] = "A draft notice exists; no dispatch or restraint is established."
        linked["stage"] = "Notice draft"
    elif status == "awaiting_countersignature":
        result["verdict_line"] = "A notice is awaiting countersignature"
        result["verdict_note"] = (
            f"TRINETRA records a countersignature request for notice {notice_no}. No dispatch, "
            "acknowledgement or restraint confirmation is recorded."
        )
        signal.update(
            label="Countersignature requested",
            detail="The supervising officer must act in their own session before dispatch can unlock.",
            meta=notice_no,
            tone="medium",
        )
        result["signals"][1] = "Countersignature is pending; no dispatch or restraint is established."
        linked["stage"] = "Awaiting countersign"
    elif status == "countersigned":
        result["verdict_line"] = "A countersigned notice is recorded for this address"
        result["verdict_note"] = (
            f"TRINETRA records notice {notice_no} as countersigned but not dispatched. "
            "Acknowledgement and restraint remain separate, unrecorded states."
        )
        signal.update(
            label="Countersignature recorded",
            detail="The drafting officer has not yet recorded a dispatch from this workflow.",
            meta=notice_no,
            tone="medium",
        )
        result["signals"][1] = "Countersignature exists; no dispatch or restraint is established."
        linked["stage"] = "Countersigned"
    elif status == "dispatched":
        dispatch_count = int((notice_state or {}).get("dispatch_count") or 0)
        failed_count = int((notice_state or {}).get("failed_count") or 0)
        if dispatch_count == 0:
            dispatch_detail = "No per-channel dispatch record was found; the audit trail requires review."
        elif failed_count:
            dispatch_detail = (
                f"{dispatch_count} channel records exist and {failed_count} recorded a delivery failure."
            )
        else:
            dispatch_detail = (
                f"{dispatch_count} channel record{' exists' if dispatch_count == 1 else 's exist'} "
                "and acknowledgement is pending."
            )
        result["verdict_line"] = "A notice dispatch is recorded for this address"
        result["verdict_note"] = (
            f"TRINETRA records notice {notice_no} as dispatched. {dispatch_detail} "
            "No restraint confirmation is inferred from dispatch alone."
        )
        signal.update(
            label="Notice dispatch recorded",
            detail=f"{dispatch_detail} Restraint must be confirmed separately.",
            meta=notice_no,
            tone="alert",
        )
        result["signals"][1] = (
            "A notice dispatch record exists; acknowledgement and restraint are separate states."
        )
        linked["stage"] = "Notice dispatched"
    else:
        result["signals"][1] = (
            "No notice is recorded; dispatch and restraint status are not established."
        )


def risk_check(address: str, *, notice_state: dict | None = None) -> dict:
    """Return deterministic, record-based risk presentation data for the local prototype."""

    normalized = address.strip()
    data = demo_case()
    aliases = {
        data["terminal"]["deposit_address"]: ALERT_ADDRESS,
        data["dominant_path"][2]["address"]: MEDIUM_ADDRESS,
        "RC_clean": LOW_ADDRESS,
    }
    result_key = aliases.get(normalized, normalized)
    result = deepcopy(_RESULTS.get(result_key))

    if result is None:
        result = {
            "band": "unknown",
            "band_label": "Inconclusive",
            "verdict_badge": "Not held at this desk",
            "verdict_line": "The address is unknown to the records checked",
            "verdict_note": (
                "No adverse findings in available sources. Available information is insufficient "
                "for a behavioural or attribution outcome, and absence is not clearance."
            ),
            "facts": [
                {"label": "First seen", "value": "unknown", "tone": "muted"},
                {"label": "Inbound total", "value": "unknown", "tone": "muted"},
                {"label": "Attribution", "value": "Unattributed", "tone": "medium"},
                {"label": "Dockets", "value": "0", "tone": "default"},
            ],
            "signal_details": [
                {"label": "No docket match", "detail": "Checked against 37 dockets held by this desk.", "meta": "0 hits", "tone": "success"},
                {"label": "No attribution entry", "detail": "The address is not in the shared attribution set at the current revision.", "meta": "rev. 2026-08-24", "tone": "medium"},
            ],
            "signals": ["Absence of a record is not clearance."],
            "linked": [],
        }

    if result_key == ALERT_ADDRESS:
        _apply_notice_state(result, notice_state)

    result.update(
        {
            "address": normalized,
            "headline": (
                "Investigative signals found for this address."
                if result["band"] in {"alert", "medium"}
                else "No adverse findings in available sources."
            ),
            "dockets": [item["reference"] for item in result["linked"]],
            "attribution": (
                {"name": "Coinsphere Global Pte Ltd"}
                if result_key == ALERT_ADDRESS
                else resolve_vasp(normalized, ChainRef("TRON", "mainnet"))
            ),
            "record_based": True,
            "live_enrichment": "disabled in fixture mode",
            "score": None,
            "posterior": None,
            "probability_enabled": False,
            "score_kind": "fixture_evidence_band",
            "calibration_status": "disabled_pending_independent_labelled_data",
            "evidence_band_value": _BAND_VALUES.get(result["band"], "Inconclusive"),
            "evidence_band_caption": "fixture evidence band",
            "mode": "fixture",
            "features": [],
            "feature_set": None,
            "methodology": band_methodology(),
            "attribution_evidence": [
                {
                    "source": "TRINETRA fixture case records",
                    "status": "matched" if result["linked"] else "no_match_in_fixture",
                    "retrieval_ts_ms": None,
                    "provenance": "docs/demo_case.json and local fixture docket records",
                },
                {
                    "source": "reviewed fixture VASP registry",
                    "status": "matched" if result_key == ALERT_ADDRESS else "no_match_in_fixture",
                    "retrieval_ts_ms": None,
                    "provenance": "Controlled fixture attribution revision 2026-08-24",
                },
                {
                    "source": "fixture sanctions sources",
                    "status": "no_adverse_findings_in_available_sources",
                    "retrieval_ts_ms": None,
                    "provenance": "Controlled fixture advisory revision 2026-08-24",
                },
                {
                    "source": "fixture reported-abuse sources",
                    "status": "no_adverse_findings_in_available_sources",
                    "retrieval_ts_ms": None,
                    "provenance": "Controlled fixture reported-abuse revision 2026-08-24",
                },
            ],
        }
    )
    return result


def live_risk_check(
    address: str,
    *,
    evidence_root: Path,
    local_records: list[dict[str, Any]] | None = None,
    reported_addresses: set[str] | None = None,
    lookback_days: int = 30,
) -> dict:
    normalized = address.strip()
    lookup_started = now_ms()
    lookup_ref = sha256_json(
        {"address": normalized, "mode": "live", "started_ts_ms": lookup_started}
    )
    records = list(local_records or [])
    if detect_chain(normalized) != "TRON":
        result = _live_result(
            normalized,
            transfers=[],
            provider_records=[],
            local_records=records,
            reported_addresses=reported_addresses or set(),
            lookup_ref=lookup_ref,
            provider_status="unsupported_chain",
            provider_error_kind="unsupported_chain",
            lookback_days=lookback_days,
        )
        return _persist_live_result(result, evidence_root)

    from engine.adapters import tron

    window_end = lookup_started
    window_start = max(0, window_end - max(1, lookback_days) * 24 * 60 * 60 * 1000)
    capture_root = evidence_root / "risk-lookups" / lookup_ref[:20]
    with provider_evidence_scope(root=capture_root, lookup_ref=lookup_ref) as capture:
        try:
            raw = tron.fetch_trc20_transfers(
                normalized,
                min_timestamp=window_start,
                max_timestamp=window_end,
                contract_address=tron.TRON_MAINNET_USDT,
            )
            transfers = [tron.normalise(row) for row in raw]
            provider_status = "confirmed_history_fetched"
            provider_error_kind = None
        except (tron.ProviderConfigurationError, tron.ProviderResponseError) as exc:
            transfers = []
            provider_status = "provider_unavailable"
            provider_error_kind = getattr(exc, "error_kind", "provider_configuration")
    result = _live_result(
        normalized,
        transfers=transfers,
        provider_records=list(capture.records),
        local_records=records,
        reported_addresses=reported_addresses or set(),
        lookup_ref=lookup_ref,
        provider_status=provider_status,
        provider_error_kind=provider_error_kind,
        lookback_days=lookback_days,
    )
    return _persist_live_result(result, evidence_root)


def _persist_live_result(result: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    result_bytes = canonical_json_bytes(result)
    result_sha256 = sha256_bytes(result_bytes)
    lookup_ref = str(result["lookup_ref"])
    directory = evidence_root / "risk-lookups" / lookup_ref[:20]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"result-{result_sha256[:16]}.json"
    if not path.exists():
        path.write_bytes(result_bytes)
    result["result_record"] = {
        "schema": "trinetra.risk_lookup_record/1",
        "sha256": result_sha256,
        "path": str(path),
    }
    return result


def _live_result(
    address: str,
    *,
    transfers: list[dict[str, Any]],
    provider_records: list[dict[str, Any]],
    local_records: list[dict[str, Any]],
    reported_addresses: set[str],
    lookup_ref: str,
    provider_status: str,
    provider_error_kind: str | None,
    lookback_days: int,
) -> dict:
    feature_set = extract_behavioral_features(
        address,
        transfers,
        reported_addresses=reported_addresses,
        registry_addresses=set(),
    )
    band_result = assign_evidence_band(
        feature_set,
        local_case_match_count=len(local_records),
    )
    if provider_status != "confirmed_history_fetched" and not local_records:
        band_result = {
            "schema": band_result["schema"],
            "band": "unknown",
            "matched_rules": [],
            "observed_classes": [],
            "reason": "Confirmed provider history was unavailable for this lookup.",
            "insufficiency_reasons": [
                "Behavioural features cannot be banded without confirmed transfer history."
            ],
        }
    band = band_result["band"]
    labels = {
        "alert": ("Case-linked evidence", "Named by available local records"),
        "medium": ("Observed pattern", "Documented behavioural rule matched"),
        "low": ("Available-source result", "Evidence remains limited"),
        "unknown": ("Unknown", "Insufficient evidence"),
    }
    verdicts = {
        "alert": "Available local case records name this address",
        "medium": "Observed activity matches a documented behavioural pattern",
        "low": "No adverse findings in available sources",
        "unknown": "Available evidence is insufficient for a behavioural outcome",
    }
    notes = {
        "alert": (
            "The address is linked to available local case evidence. That link does not establish "
            "identity, intent, participation or current custody."
        ),
        "medium": (
            "The band follows fixed, reviewable rules over confirmed transfer history. Behavioural "
            "similarity is not custody attribution, identity, intent or participation."
        ),
        "low": (
            "No adverse findings in available sources. Absence does not establish ownership, "
            "identity, intent or participation."
        ),
        "unknown": (
            "No adverse findings in available sources. The observed history is insufficient, so "
            "the system leaves the outcome unknown."
        ),
    }
    band_label, verdict_badge = labels[band]
    signal_details = _live_signal_details(band_result, feature_set)
    linked = [
        {
            "reference": record["reference"],
            "amount": record.get("amount", "Amount held in case record"),
            "stage": record.get("stage", "Local case evidence"),
            "tone": "medium",
            "href": record.get("href", "/docket"),
        }
        for record in local_records
    ]
    provider_retrievals = [
        int(record["retrieval_ts_ms"])
        for record in provider_records
        if record.get("retrieval_ts_ms") is not None
    ]
    local_case_source = {
        "source": "TRINETRA local case records",
        "status": "matched" if local_records else "no_match_in_available_records",
        "retrieval_ts_ms": max(
            [int(record["retrieval_ts_ms"]) for record in local_records if record.get("retrieval_ts_ms") is not None],
            default=None,
        ),
        "provenance": "Local case and canonical evidence index",
        "references": [record.get("reference") for record in local_records],
        "evidence_refs": [
            evidence_ref
            for record in local_records
            for evidence_ref in list(record.get("evidence_refs") or [])
        ],
    }
    attribution_evidence = [
        {
            "source": "TronGrid confirmed TRC-20 history",
            "status": provider_status,
            "retrieval_ts_ms": max(provider_retrievals) if provider_retrievals else None,
            "provenance": f"{len(provider_records)} retained provider request receipt(s)",
            "error_kind": provider_error_kind,
        },
        local_case_source,
        {
            "source": "reviewed VASP registry",
            "status": "unavailable",
            "retrieval_ts_ms": None,
            "provenance": "No reviewed live registry is configured; fixture registry data is excluded.",
        },
        {
            "source": "sanctions sources",
            "status": "unavailable",
            "retrieval_ts_ms": None,
            "provenance": "No approved live sanctions connector is configured.",
        },
        {
            "source": "reported-abuse sources",
            "status": "unavailable",
            "retrieval_ts_ms": None,
            "provenance": "No approved live reported-abuse connector is configured.",
        },
    ]
    safe_provider_records = [
        {
            "request_ref": record.get("request_ref"),
            "provider": record.get("provider"),
            "endpoint": record.get("endpoint"),
            "retrieval_ts_ms": record.get("retrieval_ts_ms"),
            "raw_sha256": record.get("raw_sha256"),
            "schema_status": record.get("schema_status"),
        }
        for record in provider_records
    ]
    return {
        "schema": "trinetra.risk_check/2",
        "lookup_ref": lookup_ref,
        "mode": "live",
        "address": address,
        "band": band,
        "band_label": band_label,
        "verdict_badge": verdict_badge,
        "verdict_line": verdicts[band],
        "verdict_note": notes[band],
        "headline": verdicts[band],
        "facts": [
            {
                "label": "Transfers observed",
                "value": str(feature_set["observed_event_count"]),
                "tone": "default",
            },
            {
                "label": "Inbound events",
                "value": str(feature_set["inbound_event_count"]),
                "tone": "default",
            },
            {
                "label": "Outbound events",
                "value": str(feature_set["outbound_event_count"]),
                "tone": "default",
            },
            {
                "label": "History window",
                "value": f"{lookback_days} days",
                "tone": "default",
            },
        ],
        "signal_details": signal_details,
        "signals": [item["detail"] for item in signal_details],
        "linked": linked,
        "dockets": [item["reference"] for item in linked],
        "attribution": None,
        "record_based": True,
        "live_enrichment": provider_status,
        "score": None,
        "posterior": None,
        "probability_enabled": False,
        "score_kind": "not_scored",
        "calibration_status": "disabled_pending_independent_labelled_data",
        "evidence_band_value": _BAND_VALUES.get(band, "Inconclusive"),
        "evidence_band_caption": "documented evidence band",
        "feature_set": feature_set,
        "features": feature_set["items"],
        "band_basis": band_result,
        "methodology": band_methodology(),
        "attribution_evidence": attribution_evidence,
        "provider_evidence": safe_provider_records,
    }


def _live_signal_details(
    band_result: dict[str, Any],
    feature_set: dict[str, Any],
) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for rule in band_result.get("matched_rules") or []:
        details.append(
            {
                "label": rule.replace("_", " ").title(),
                "detail": "A versioned evidence-band rule matched the raw features shown below.",
                "meta": band_result["schema"],
                "tone": "alert" if band_result["band"] == "alert" else "medium",
            }
        )
    for reason in band_result.get("insufficiency_reasons") or []:
        details.append(
            {
                "label": "Evidence insufficient",
                "detail": reason,
                "meta": feature_set["schema"],
                "tone": "medium",
            }
        )
    if not details:
        details.append(
            {
                "label": "No adverse findings in available sources",
                "detail": (
                    "No local case match or documented behavioural rule was found in the fetched "
                    "history. Absence is not clearance."
                ),
                "meta": band_result["schema"],
                "tone": "default",
            }
        )
    return details
