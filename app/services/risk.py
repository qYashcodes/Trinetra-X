from __future__ import annotations

from copy import deepcopy

from app.engine_bridge import ChainRef, resolve_vasp
from app.services.demo import demo_case


_DEMO = demo_case()
ALERT_ADDRESS = _DEMO["terminal"]["deposit_address"]
MEDIUM_ADDRESS = _DEMO["dominant_path"][2]["address"]
RECENT_PATH_ADDRESS = _DEMO["dominant_path"][3]["address"]
LOW_ADDRESS = "TZr8kM3vXp6nQe1WdB9cLf4YtJ7sHu2Ga15"


def _compact_address(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


_RESULTS = {
    ALERT_ADDRESS: {
        "score": 91,
        "band": "alert",
        "band_label": "High risk",
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
            "Classifier band is high.",
        ],
        "linked": [
            {"reference": "NCRP/2026/MH/0084213", "amount": "21,940.00 USDT", "stage": "Finding review", "tone": "medium", "href": "/notices"},
            {"reference": "NCRP/2026/MH/0081902", "amount": "112,400.00 USDT", "stage": "Linked docket", "tone": "success", "href": "/findings/1"},
            {"reference": "NCRP/2026/MH/0079411", "amount": "15,600.00 USDT", "stage": "Closed, no custody", "tone": "muted", "href": "/docket"},
            {"reference": "NCRP/2026/KA/0079922", "amount": "46,000.00 USDT", "stage": "Review pending", "tone": "medium", "href": "/docket"},
        ],
    },
    MEDIUM_ADDRESS: {
        "score": 58,
        "band": "medium",
        "band_label": "Medium risk",
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
            {"label": "No advisory listing", "detail": "The address does not appear on any sanctions or advisory list held by this desk.", "meta": "checked 09:41 IST", "tone": "success"},
        ],
        "signals": ["Classifier band is medium for an intermediate hop.", "Address is on a traced path in this docket."],
        "linked": [
            {"reference": "NCRP/2026/MH/0084213", "amount": "21,940.00 USDT", "stage": "Linked docket", "tone": "medium", "href": "/notices"}
        ],
    },
    LOW_ADDRESS: {
        "score": 12,
        "band": "low",
        "band_label": "Low risk",
        "verdict_badge": "Nothing on record",
        "verdict_line": "No adverse signal against this address",
        "verdict_note": (
            "The address appears in no docket at this desk, carries no advisory listing, and its "
            "transfer pattern is unremarkable. Absence of a record is not clearance; it means only "
            "that nothing is held."
        ),
        "facts": [
            {"label": "First seen", "value": "2025-02-04", "tone": "default"},
            {"label": "Inbound total", "value": "9,140 USDT", "tone": "default"},
            {"label": "Attribution", "value": "Retail wallet", "tone": "default"},
            {"label": "Dockets", "value": "0", "tone": "success"},
        ],
        "signal_details": [
            {"label": "No docket match", "detail": "Checked against 37 open and closed dockets held by this desk.", "meta": "0 hits", "tone": "success"},
            {"label": "No advisory listing", "detail": "Not present on sanctions or advisory lists as at the current revision.", "meta": "rev. 2026-08-24", "tone": "success"},
            {"label": "Steady low-value pattern", "detail": "Small regular transfers to two long-lived counterparties over nineteen months.", "meta": "19 months", "tone": "success"},
        ],
        "signals": ["Absence of a record is not clearance."],
        "linked": [],
    },
}


def risk_page_data() -> dict:
    return {
        "risk_samples": [
            {"address": ALERT_ADDRESS, "label": _compact_address(ALERT_ADDRESS), "tone": "alert"},
            {"address": MEDIUM_ADDRESS, "label": _compact_address(MEDIUM_ADDRESS), "tone": "medium"},
            {"address": LOW_ADDRESS, "label": "TZr8kM…Ga15", "tone": "success"},
        ],
        "risk_recent": [
            {"address": ALERT_ADDRESS, "label": _compact_address(ALERT_ADDRESS), "time": "09:38", "tone": "alert"},
            {"address": MEDIUM_ADDRESS, "label": _compact_address(MEDIUM_ADDRESS), "time": "09:12", "tone": "medium"},
            {"address": LOW_ADDRESS, "label": "TZr8kM…Ga15", "time": "08:55", "tone": "success"},
            {"address": RECENT_PATH_ADDRESS, "label": _compact_address(RECENT_PATH_ADDRESS), "time": "08:31", "tone": "medium"},
        ],
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
            "score": 34,
            "band": "unknown",
            "band_label": "Inconclusive",
            "verdict_badge": "Not held at this desk",
            "verdict_line": "The address is unknown to the records checked",
            "verdict_note": (
                "No docket, attribution entry or advisory listing matches this address. A score in "
                "this range reflects the absence of information, not a finding of safety."
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
                else "No local record matched this address."
            ),
            "dockets": [item["reference"] for item in result["linked"]],
            "attribution": (
                {"name": "Coinsphere Global Pte Ltd"}
                if result_key == ALERT_ADDRESS
                else resolve_vasp(normalized, ChainRef("TRON", "mainnet"))
            ),
            "record_based": True,
            "live_enrichment": "disabled in fixture mode",
        }
    )
    return result
