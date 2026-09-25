from __future__ import annotations

import os
from typing import Any

from app.services.capabilities import capability_matrix
from app.services.feature_flags import feature_flags
from app.services.worker import frontier_worker_status
from app.services.provider_budget import provider_budget_status
from app.settings import ROOT_DIR, settings


SECRET_ENV_NAMES = {
    "SESSION_SECRET",
    "TRONGRID_API_KEY",
    "TRONSCAN_API_KEY",
    "ETHERSCAN_API_KEY",
    "OIDC_CCTNS_CLIENT_SECRET",
    "OIDC_PARICHAY_CLIENT_SECRET",
    "SMTP_URL",
}


def _configured(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def _value(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _status(configured: bool, *, enabled: bool = True, requires_approval: bool = False) -> str:
    if not enabled:
        return "disabled"
    if requires_approval:
        return "approval_required"
    return "configured" if configured else "missing"


def integration_status() -> dict[str, Any]:
    flags = feature_flags()
    worker = frontier_worker_status()
    provider_budget = provider_budget_status()
    cctns_oidc_ready = all(
        _configured(name)
        for name in ("OIDC_CCTNS_ISSUER", "OIDC_CCTNS_CLIENT_ID", "OIDC_CCTNS_CLIENT_SECRET")
    )
    parichay_oidc_ready = all(
        _configured(name)
        for name in (
            "OIDC_PARICHAY_ISSUER",
            "OIDC_PARICHAY_CLIENT_ID",
            "OIDC_PARICHAY_CLIENT_SECRET",
        )
    )
    dispatch_ready = settings.legal_copy_approved and _configured("SMTP_URL")
    session_secret_ready = _configured("SESSION_SECRET")
    chain_live_ready = settings.mode == "live" and bool(
        flags["live_tron_provider"]["enabled"]
    )
    presence_asset_root = ROOT_DIR / "app" / "static" / "vendor" / "mediapipe"
    presence_assets_ready = all(
        (presence_asset_root / relative).is_file()
        for relative in (
            "vision_bundle.mjs",
            "blaze_face_short_range.tflite",
            "wasm/vision_wasm_internal.js",
            "wasm/vision_wasm_internal.wasm",
            "wasm/vision_wasm_nosimd_internal.js",
            "wasm/vision_wasm_nosimd_internal.wasm",
        )
    )

    groups = [
        {
            "key": "mode",
            "name": "Runtime mode",
            "summary": "Fixture mode keeps the demo offline and deterministic.",
            "status": "configured"
            if settings.mode == "fixture"
            else "configured"
            if chain_live_ready
            else "missing",
            "items": [
                {"label": "TRINETRA_MODE", "status": "configured", "detail": settings.mode},
                {"label": "Fixture data", "status": "configured", "detail": "docs/demo_case.json"},
                {"label": "Runtime state", "status": "configured", "detail": "var/"},
            ],
        },
        {
            "key": "chain",
            "name": "Chain data providers",
            "summary": (
                "Live adapters remain closed until provider keys, verification and live mode "
                "are configured."
            ),
            "status": "configured" if chain_live_ready else "missing",
            "items": [
                {
                    "label": "TRONGRID_API_KEY",
                    "status": _status(_configured("TRONGRID_API_KEY")),
                    "detail": "TRON provider key",
                },
                {
                    "label": "TRONSCAN_API_KEY",
                    "status": _status(_configured("TRONSCAN_API_KEY")),
                    "detail": "TRON fallback key",
                },
                {
                    "label": "ETHERSCAN_API_KEY",
                    "status": _status(_configured("ETHERSCAN_API_KEY")),
                    "detail": "EVM provider key",
                },
                {
                    "label": "ESPLORA_BASE_URL",
                    "status": _status(_configured("ESPLORA_BASE_URL")),
                    "detail": _value("ESPLORA_BASE_URL") or "public endpoint not set",
                },
            ],
        },
        {
            "key": "worker",
            "name": "Trace frontier worker",
            "summary": "The supervisor exposes counts and failure classes without provider keys or response content.",
            "status": "configured" if worker["state"] == "running" else "disabled",
            "items": [
                {
                    "label": "Supervisor state",
                    "status": "configured" if worker["state"] == "running" else "disabled",
                    "detail": str(worker["state"]),
                },
                {
                    "label": "Queued frontier",
                    "status": "configured",
                    "detail": str(worker["queued"]),
                },
                {
                    "label": "Deferred frontier",
                    "status": "configured",
                    "detail": str(worker["deferred"]),
                },
                {
                    "label": "Worker failures",
                    "status": "configured" if worker["failures"] == 0 else "missing",
                    "detail": str(worker["failures"]),
                },
            ],
        },
        {
            "key": "provider_budget",
            "name": "Shared provider request budget",
            "summary": "Interactive trace requests retain reserved capacity ahead of background watch polling.",
            "status": "configured",
            "items": [
                {
                    "label": "Window consumption",
                    "status": "configured",
                    "detail": f"{provider_budget['used']} of {provider_budget['limit']} requests",
                },
                {
                    "label": "Interactive reserve",
                    "status": "configured",
                    "detail": str(provider_budget["interactive_reserve"]),
                },
                {
                    "label": "Watch capacity remaining",
                    "status": "configured"
                    if provider_budget["watch_capacity_remaining"] > 0
                    else "disabled",
                    "detail": str(provider_budget["watch_capacity_remaining"]),
                },
                {
                    "label": "Watch poll gate",
                    "status": "configured" if flags["wallet_watch"]["enabled"] else "disabled",
                    "detail": "in-app only; no email or messaging channel",
                },
            ],
        },
        {
            "key": "identity",
            "name": "Government identity",
            "summary": "OIDC and WebAuthn are unavailable shells; demonstration login is not strong authentication.",
            "status": "disabled",
            "items": [
                {
                    "label": "SSO_CALLBACK_ENABLED",
                    "status": "configured" if settings.sso_callback_enabled else "disabled",
                    "detail": str(settings.sso_callback_enabled).lower(),
                },
                {
                    "label": "CCTNS OIDC",
                    "status": _status(cctns_oidc_ready, enabled=settings.sso_callback_enabled),
                    "detail": "issuer, client id, client secret",
                },
                {
                    "label": "Parichay OIDC",
                    "status": _status(parichay_oidc_ready, enabled=settings.sso_callback_enabled),
                    "detail": "issuer, client id, client secret",
                },
                {
                    "label": "WebAuthn",
                    "status": "disabled",
                    "detail": "integration shell; no approved relying-party configuration",
                },
            ],
        },
        {
            "key": "session_security",
            "name": "Session security",
            "summary": "Local session controls are tested; production identity and infrastructure controls remain unavailable.",
            "status": "configured" if session_secret_ready else "missing",
            "items": [
                {
                    "label": "SESSION_SECRET",
                    "status": _status(session_secret_ready),
                    "detail": "required outside fixture mode; value never rendered",
                },
                {
                    "label": "Server session registry",
                    "status": "configured",
                    "detail": "hashed tokens, idle expiry and revocation",
                },
                {
                    "label": "Transport security",
                    "status": "disabled",
                    "detail": "local HTTP only; TLS termination not configured",
                },
                {
                    "label": "Secret vault",
                    "status": "disabled",
                    "detail": "environment-file configuration only",
                },
                {
                    "label": "Tenant authorisation boundary",
                    "status": "disabled",
                    "detail": "no production multi-tenant policy",
                },
                {
                    "label": "Monitoring and backup",
                    "status": "disabled",
                    "detail": "production operations integration not configured",
                },
            ],
        },
        {
            "key": "workstation_presence",
            "name": "Workstation presence",
            "summary": (
                "Optional on-device face presence can obscure an unattended workspace; "
                "it does not identify or authenticate the officer."
            ),
            "status": "configured"
            if flags["workstation_presence"]["enabled"]
            else "disabled",
            "items": [
                {
                    "label": "TRINETRA_ENABLE_WORKSTATION_PRESENCE",
                    "status": "configured"
                    if flags["workstation_presence"]["requested"]
                    else "disabled",
                    "detail": str(flags["workstation_presence"]["requested"]).lower(),
                },
                {
                    "label": "TRINETRA_WORKSTATION_PRESENCE_REVIEWED",
                    "status": "configured"
                    if flags["workstation_presence"]["reviewed"]
                    else "approval_required",
                    "detail": str(flags["workstation_presence"]["reviewed"]).lower(),
                },
                {
                    "label": "Pinned local runtime",
                    "status": "configured" if presence_assets_ready else "missing",
                    "detail": "MediaPipe Tasks Vision 1.0.1 and BlazeFace short-range",
                },
                {
                    "label": "Browser security context",
                    "status": "configured"
                    if flags["workstation_presence"]["enabled"]
                    else "disabled",
                    "detail": "requires HTTPS or localhost plus officer camera permission",
                },
            ],
        },
        {
            "key": "narrative_triage",
            "name": "Complaint narrative triage",
            "summary": (
                "Deterministic English typology indicators remain advisory and contain no "
                "probability or custody claim."
            ),
            "status": "configured" if flags["narrative_triage"]["enabled"] else "approval_required",
            "items": [
                {
                    "label": "TRINETRA_ENABLE_NARRATIVE_TRIAGE",
                    "status": "configured" if flags["narrative_triage"]["requested"] else "disabled",
                    "detail": str(flags["narrative_triage"]["requested"]).lower(),
                },
                {
                    "label": "TRINETRA_NARRATIVE_TAXONOMY_REVIEWED",
                    "status": "configured" if flags["narrative_triage"]["taxonomy_reviewed"] else "approval_required",
                    "detail": str(flags["narrative_triage"]["taxonomy_reviewed"]).lower(),
                },
                {
                    "label": "TRINETRA_NARRATIVE_PRIVACY_REVIEWED",
                    "status": "configured" if flags["narrative_triage"]["privacy_reviewed"] else "approval_required",
                    "detail": str(flags["narrative_triage"]["privacy_reviewed"]).lower(),
                },
            ],
        },
        {
            "key": "dispatch",
            "name": "Notice dispatch",
            "summary": (
                "SAHYOG/export and email dispatch stay specimen-only until legal copy and "
                "channels are approved."
            ),
            "status": "configured" if dispatch_ready else "approval_required",
            "items": [
                {
                    "label": "LEGAL_COPY_APPROVED",
                    "status": "configured"
                    if settings.legal_copy_approved
                    else "approval_required",
                    "detail": str(settings.legal_copy_approved).lower(),
                },
                {
                    "label": "NOTICE_SPECIMEN_WATERMARK",
                    "status": "configured" if settings.specimen_watermark else "disabled",
                    "detail": str(settings.specimen_watermark).lower(),
                },
                {
                    "label": "SMTP_URL",
                    "status": _status(_configured("SMTP_URL")),
                    "detail": "dispatch channel secret",
                },
            ],
        },
    ]
    return {
        "schema": "trinetra.integration_status/1",
        "mode": settings.mode,
        "environment": settings.env,
        "secrets_rendered": False,
        "secret_env_names": sorted(SECRET_ENV_NAMES),
        "capabilities": capability_matrix(),
        "feature_flags": flags,
        "worker": worker,
        "provider_budget": provider_budget,
        "groups": groups,
    }
