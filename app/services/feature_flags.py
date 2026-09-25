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
    live_evm_requested = flag_enabled("TRINETRA_ENABLE_LIVE_EVM")
    live_evm_key = bool((os.getenv("ETHERSCAN_API_KEY") or "").strip())
    live_evm_schema = flag_enabled("TRINETRA_LIVE_EVM_SCHEMA_VERIFIED")
    live_evm_smoke = flag_enabled("TRINETRA_LIVE_EVM_SMOKE_VERIFIED")
    live_evm_trace_verified = flag_enabled("TRINETRA_LIVE_EVM_TRACE_VERIFIED")
    live_evm_provider_ready = live_evm_requested and live_evm_key and live_evm_schema and live_evm_smoke
    live_evm_trace_ready = live_evm_provider_ready and live_evm_trace_verified
    live_evm_missing = [
        name
        for name, ready in (
            ("TRINETRA_ENABLE_LIVE_EVM", live_evm_requested),
            ("ETHERSCAN_API_KEY", live_evm_key),
            ("TRINETRA_LIVE_EVM_SCHEMA_VERIFIED", live_evm_schema),
            ("TRINETRA_LIVE_EVM_SMOKE_VERIFIED", live_evm_smoke),
            ("TRINETRA_LIVE_EVM_TRACE_VERIFIED", live_evm_trace_verified),
        )
        if not ready
    ]
    live_btc_requested = flag_enabled("TRINETRA_ENABLE_LIVE_BTC")
    live_btc_endpoint = bool((os.getenv("ESPLORA_BASE_URL") or "").strip())
    live_btc_schema = flag_enabled("TRINETRA_LIVE_BTC_SCHEMA_VERIFIED")
    live_btc_smoke = flag_enabled("TRINETRA_LIVE_BTC_SMOKE_VERIFIED")
    live_btc_trace_verified = flag_enabled("TRINETRA_LIVE_BTC_TRACE_VERIFIED")
    live_btc_provider_ready = (
        live_btc_requested and live_btc_endpoint and live_btc_schema and live_btc_smoke
    )
    live_btc_trace_ready = live_btc_provider_ready and live_btc_trace_verified
    live_btc_missing = [
        name
        for name, ready in (
            ("TRINETRA_ENABLE_LIVE_BTC", live_btc_requested),
            ("ESPLORA_BASE_URL", live_btc_endpoint),
            ("TRINETRA_LIVE_BTC_SCHEMA_VERIFIED", live_btc_schema),
            ("TRINETRA_LIVE_BTC_SMOKE_VERIFIED", live_btc_smoke),
            ("TRINETRA_LIVE_BTC_TRACE_VERIFIED", live_btc_trace_verified),
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
    presence_requested = flag_enabled("TRINETRA_ENABLE_WORKSTATION_PRESENCE")
    presence_reviewed = flag_enabled("TRINETRA_WORKSTATION_PRESENCE_REVIEWED")
    presence_ready = presence_requested and presence_reviewed
    narrative_requested = flag_enabled("TRINETRA_ENABLE_NARRATIVE_TRIAGE")
    narrative_taxonomy_reviewed = flag_enabled("TRINETRA_NARRATIVE_TAXONOMY_REVIEWED")
    narrative_privacy_reviewed = flag_enabled("TRINETRA_NARRATIVE_PRIVACY_REVIEWED")
    narrative_ready = (
        narrative_requested and narrative_taxonomy_reviewed and narrative_privacy_reviewed
    )
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
        "live_evm_provider": {
            "enabled": live_evm_provider_ready,
            "requested": live_evm_requested,
            "missing_gates": live_evm_missing[:-1]
            if not live_evm_trace_verified
            else live_evm_missing,
            "blocked_reason": None
            if live_evm_provider_ready
            else (
                "Live EVM provider access requires TRINETRA_ENABLE_LIVE_EVM, "
                "ETHERSCAN_API_KEY, TRINETRA_LIVE_EVM_SCHEMA_VERIFIED and "
                "TRINETRA_LIVE_EVM_SMOKE_VERIFIED."
            ),
        },
        "live_evm_trace": {
            "enabled": live_evm_trace_ready,
            "requested": live_evm_requested,
            "missing_gates": live_evm_missing,
            "blocked_reason": None
            if live_evm_trace_ready
            else (
                "Live EVM tracing requires the live EVM provider gate plus "
                "TRINETRA_LIVE_EVM_TRACE_VERIFIED."
            ),
        },
        "live_btc_provider": {
            "enabled": live_btc_provider_ready,
            "requested": live_btc_requested,
            "missing_gates": live_btc_missing[:-1]
            if not live_btc_trace_verified
            else live_btc_missing,
            "blocked_reason": None
            if live_btc_provider_ready
            else (
                "Live Bitcoin provider access requires TRINETRA_ENABLE_LIVE_BTC, "
                "ESPLORA_BASE_URL, TRINETRA_LIVE_BTC_SCHEMA_VERIFIED and "
                "TRINETRA_LIVE_BTC_SMOKE_VERIFIED."
            ),
        },
        "live_btc_trace": {
            "enabled": live_btc_trace_ready,
            "requested": live_btc_requested,
            "missing_gates": live_btc_missing,
            "blocked_reason": None
            if live_btc_trace_ready
            else (
                "Live Bitcoin tracing requires the live Bitcoin provider gate plus "
                "TRINETRA_LIVE_BTC_TRACE_VERIFIED."
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
        "workstation_presence": {
            "enabled": presence_ready,
            "requested": presence_requested,
            "reviewed": presence_reviewed,
            "missing_gates": [
                name
                for name, ready in (
                    ("TRINETRA_ENABLE_WORKSTATION_PRESENCE", presence_requested),
                    ("TRINETRA_WORKSTATION_PRESENCE_REVIEWED", presence_reviewed),
                )
                if not ready
            ],
            "blocked_reason": None
            if presence_ready
            else (
                "Workstation presence requires explicit enablement and completed privacy review."
            ),
        },
        "narrative_triage": {
            "enabled": narrative_ready,
            "requested": narrative_requested,
            "taxonomy_reviewed": narrative_taxonomy_reviewed,
            "privacy_reviewed": narrative_privacy_reviewed,
            "missing_gates": [
                name
                for name, ready in (
                    ("TRINETRA_ENABLE_NARRATIVE_TRIAGE", narrative_requested),
                    ("TRINETRA_NARRATIVE_TAXONOMY_REVIEWED", narrative_taxonomy_reviewed),
                    ("TRINETRA_NARRATIVE_PRIVACY_REVIEWED", narrative_privacy_reviewed),
                )
                if not ready
            ],
            "blocked_reason": None
            if narrative_ready
            else (
                "Narrative triage requires explicit enablement plus completed taxonomy and "
                "privacy reviews."
            ),
        },
    }
