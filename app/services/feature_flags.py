from __future__ import annotations

import os


def flag_enabled(name: str) -> bool:
    return (os.getenv(name) or "false").strip().lower() in {"1", "true", "yes", "on"}


def feature_flags() -> dict[str, dict[str, object]]:
    live_tron_requested = flag_enabled("TRINETRA_ENABLE_LIVE_TRON")
    live_tron_key = bool((os.getenv("TRONGRID_API_KEY") or "").strip())
    live_tron_schema = flag_enabled("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED")
    live_tron_smoke = flag_enabled("TRINETRA_LIVE_TRON_SMOKE_VERIFIED")
    live_tron_ready = live_tron_requested and live_tron_key and live_tron_schema and live_tron_smoke
    live_tron_trace_verified = flag_enabled("TRINETRA_LIVE_TRON_TRACE_VERIFIED")
    live_tron_trace_ready = live_tron_ready and live_tron_trace_verified
    live_tron_missing = [
        name
        for name, ready in (
            ("TRINETRA_ENABLE_LIVE_TRON", live_tron_requested),
            ("TRONGRID_API_KEY", live_tron_key),
            ("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", live_tron_schema),
            ("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", live_tron_smoke),
            ("TRINETRA_LIVE_TRON_TRACE_VERIFIED", live_tron_trace_verified),
        )
        if not ready
    ]
    worker_requested = flag_enabled("TRINETRA_ENABLE_FRONTIER_WORKER")
    worker_ready = worker_requested and live_tron_trace_ready
    watch_requested = flag_enabled("TRINETRA_ENABLE_WALLET_WATCH")
    watch_ready = watch_requested and worker_ready
    provider_import_requested = flag_enabled("TRINETRA_ENABLE_PROVIDER_IMPORTS")
    provider_schema = flag_enabled("TRINETRA_PROVIDER_SCHEMA_APPROVED")
    provider_authority = flag_enabled("TRINETRA_PROVIDER_AUTHORITY_CONFIGURED")
    provider_import_ready = provider_import_requested and provider_schema and provider_authority
    protocol_requested = flag_enabled("TRINETRA_ENABLE_PROTOCOL_DECODERS")
    protocol_live_verified = flag_enabled("TRINETRA_PROTOCOL_DECODERS_LIVE_VERIFIED")
    privacy_requested = flag_enabled("TRINETRA_ENABLE_PRIVACY_REVIEW")
    privacy_approved = flag_enabled("TRINETRA_PRIVACY_REVIEW_APPROVED")
    return {
        "truthful_trace_result": {
            "enabled": True,
            "detail": "Typed trace results and non-custody terminal states are always enabled.",
        },
        "live_tron_provider": {
            "enabled": live_tron_ready,
            "requested": live_tron_requested,
            "missing_gates": live_tron_missing[:-1]
            if not live_tron_trace_verified
            else live_tron_missing,
            "blocked_reason": None
            if live_tron_ready
            else (
                "TRINETRA_ENABLE_LIVE_TRON, TRONGRID_API_KEY, "
                "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED and "
                "TRINETRA_LIVE_TRON_SMOKE_VERIFIED are required."
            ),
        },
        "live_tron_trace": {
            "enabled": live_tron_trace_ready,
            "requested": live_tron_requested,
            "missing_gates": live_tron_missing,
            "blocked_reason": None
            if live_tron_trace_ready
            else (
                "Live TRON USDT tracing requires the live TRON provider gate plus "
                "TRINETRA_LIVE_TRON_TRACE_VERIFIED."
            ),
        },
        "supervised_frontier_worker": {
            "enabled": worker_ready,
            "requested": worker_requested,
            "blocked_reason": None
            if worker_ready
            else (
                "The supervised worker requires TRINETRA_ENABLE_FRONTIER_WORKER and all "
                "live TRON trace gates."
            ),
        },
        "wallet_watch": {
            "enabled": watch_ready,
            "requested": watch_requested,
            "blocked_reason": None
            if watch_ready
            else (
                "Wallet watch requires TRINETRA_ENABLE_WALLET_WATCH, the supervised worker "
                "and all live TRON trace gates."
            ),
        },
        "provider_custody_import": {
            "enabled": provider_import_ready,
            "requested": provider_import_requested,
            "blocked_reason": None
            if provider_import_ready
            else (
                "Provider import remains local-only until schemas and authority are configured."
            ),
        },
        "protocol_decoders": {
            "enabled": protocol_requested and protocol_live_verified,
            "requested": protocol_requested,
            "blocked_reason": None
            if protocol_requested and protocol_live_verified
            else "Protocol evidence contracts exist, but live decoders are disabled.",
        },
        "privacy_review": {
            "enabled": privacy_requested and privacy_approved,
            "requested": privacy_requested,
            "blocked_reason": None
            if privacy_requested and privacy_approved
            else "Privacy-boundary helpers are local-only until review workflow is approved.",
        },
    }
