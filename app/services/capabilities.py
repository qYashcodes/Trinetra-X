from __future__ import annotations

from typing import Literal

from app.services.feature_flags import feature_flags

CapabilityState = Literal[
    "unavailable",
    "fixture-tested",
    "integration-tested",
    "live-verified",
    "enabled",
]


def capability_matrix(flags: dict[str, dict[str, object]] | None = None) -> list[dict[str, str]]:
    active_flags = flags or feature_flags()
    live_seed_enabled = bool(active_flags["live_tron_provider"]["enabled"])
    provider_import_enabled = bool(active_flags["provider_custody_import"]["enabled"])
    protocol_decoders_enabled = bool(active_flags["protocol_decoders"]["enabled"])
    privacy_review_enabled = bool(active_flags["privacy_review"]["enabled"])
    live_trace_enabled = bool(active_flags["live_tron_trace"]["enabled"])
    worker_enabled = bool(active_flags["supervised_frontier_worker"]["enabled"])
    watch_enabled = bool(active_flags.get("wallet_watch", {}).get("enabled"))
    return [
        {
            "key": "session_lifecycle",
            "name": "Officer session lifecycle",
            "state": "integration-tested",
            "detail": "Hashed server sessions, idle expiry, CSRF sign-out, revocation and export attribution are tested locally.",
        },
        {
            "key": "government_identity",
            "name": "Government identity",
            "state": "unavailable",
            "detail": "OIDC and WebAuthn remain shells pending approved metadata, credentials and authority configuration.",
        },
        {
            "key": "fixture_tron_usdt",
            "name": "Fixture TRON USDT trace",
            "state": "fixture-tested",
            "detail": "Canonical offline demo path with projected events, lots, coverage and findings.",
        },
        {
            "key": "frontier_resume",
            "name": "Frontier resume state",
            "state": "fixture-tested",
            "detail": "Deferred frontier rows, resume cursors and local lease completion are tested.",
        },
        {
            "key": "live_tron_seed_verification",
            "name": "Live TRON seed verification",
            "state": "enabled" if live_seed_enabled else "integration-tested",
            "detail": (
                "Credentialed seed-event lookup and one-hop frontier projection are enabled; "
                "custody findings remain disabled."
            )
            if live_seed_enabled
            else (
                "TronGrid seed-event lookup and one-hop frontier projection are tested offline "
                "behind credentials, schema and smoke-test gates."
            ),
        },
        {
            "key": "live_tron_usdt",
            "name": "Live TRON USDT trace",
            "state": "enabled" if live_trace_enabled else "unavailable",
            "detail": (
                "Bounded multi-hop TronGrid USDT traversal is enabled; custody findings remain disabled until provider attribution gates pass."
            )
            if live_trace_enabled
            else (
                "Bounded multi-hop traversal exists, but production use requires provider credentials, schema/smoke verification and TRINETRA_LIVE_TRON_TRACE_VERIFIED."
            ),
        },
        {
            "key": "tron_provider_contract",
            "name": "TRON provider contract",
            "state": "integration-tested",
            "detail": "TronGrid request/pagination, seed-event verification and gated no-custody live-slice persistence are tested offline.",
        },
        {
            "key": "supervised_frontier_worker",
            "name": "Supervised frontier worker",
            "state": "enabled" if worker_enabled else "integration-tested",
            "detail": (
                "The bounded worker is enabled to recover leases and drain gated live TRON frontier work."
                if worker_enabled
                else "Lease recovery, queue draining, retry deferral and key-free telemetry are tested locally behind live gates."
            ),
        },
        {
            "key": "wallet_watch",
            "name": "Wallet watch and in-app alerts",
            "state": "enabled" if watch_enabled else "integration-tested",
            "detail": (
                "Bounded TRON watch polling and in-app observation alerts are enabled."
                if watch_enabled
                else (
                    "Watch population, shared provider budgeting, immutable graph extensions "
                    "and audited in-app alert lifecycle are tested locally behind live gates."
                )
            ),
        },
        {
            "key": "evm_token_trace",
            "name": "EVM token trace",
            "state": "unavailable",
            "detail": "Detected with typed unavailable adapter status until evidence gates pass.",
        },
        {
            "key": "bitcoin_outpoint_trace",
            "name": "Bitcoin outpoint trace",
            "state": "unavailable",
            "detail": "Detected with typed unavailable adapter status until UTXO gates pass.",
        },
        {
            "key": "custody_provider_import",
            "name": "Custody provider import",
            "state": "enabled" if provider_import_enabled else "fixture-tested",
            "detail": (
                "Certified response imports are enabled for configured, authorised provider schemas."
            )
            if provider_import_enabled
            else (
                "Local certified response, ledger, KYC, trade, withdrawal and session contracts are tested."
            ),
        },
        {
            "key": "action_workflow",
            "name": "Authorised action workflow",
            "state": "fixture-tested",
            "detail": "Draft, countersignature and specimen dispatch are local-only.",
        },
        {
            "key": "v3_working_context",
            "name": "Scoped working context",
            "state": "integration-tested",
            "detail": "Server-authoritative case, mode and record context is locally tested with assignment-based access checks.",
        },
        {
            "key": "v3_strategy_projection",
            "name": "Trace strategy projection",
            "state": "integration-tested",
            "detail": "Engine-enumerated strategy views recompute presentation without changing sealed trace evidence.",
        },
        {
            "key": "v3_notice_workflow",
            "name": "Versioned notice workflow and PDF",
            "state": "integration-tested",
            "detail": "Immutable notice versions, attachments, attestation, review loops and validated A4 artifacts are tested locally; statutory labels remain specimens pending legal review.",
        },
        {
            "key": "v3_dispatch_oversight",
            "name": "Dispatch oversight and escalation",
            "state": "integration-tested",
            "detail": "Shared dispatch records, SLA accounting, case-specific owner chains and audit-linked oversight are tested locally; external delivery remains simulated or unavailable.",
        },
        {
            "key": "v3_audit_outbox",
            "name": "Canonical workflow audit outbox",
            "state": "integration-tested",
            "detail": "Stable workflow event IDs are durably queued and idempotently appended to the legacy-compatible hash chain.",
        },
        {
            "key": "bridge_defi_decoders",
            "name": "Bridge and DeFi decoders",
            "state": "enabled" if protocol_decoders_enabled else "fixture-tested",
            "detail": (
                "Protocol decoders are enabled only after live verification gates pass."
            )
            if protocol_decoders_enabled
            else (
                "CCTP, correlation, unknown-boundary and swap evidence contracts are tested; no live decoder is enabled."
            ),
        },
        {
            "key": "privacy_boundaries",
            "name": "Privacy boundary review",
            "state": "enabled" if privacy_review_enabled else "fixture-tested",
            "detail": (
                "Review indicators and claim checks preserve exposure, service role, "
                "participation and privacy-boundary distinctions."
            ),
        },
    ]
