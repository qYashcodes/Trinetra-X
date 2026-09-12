from __future__ import annotations

import os
from typing import Any

from app.settings import settings


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
    cctns_oidc_ready = all(
        _configured(name)
        for name in ("OIDC_CCTNS_ISSUER", "OIDC_CCTNS_CLIENT_ID", "OIDC_CCTNS_CLIENT_SECRET")
    )
    parichay_oidc_ready = all(
        _configured(name)
        for name in ("OIDC_PARICHAY_ISSUER", "OIDC_PARICHAY_CLIENT_ID", "OIDC_PARICHAY_CLIENT_SECRET")
    )
    dispatch_ready = settings.legal_copy_approved and _configured("SMTP_URL")
    chain_live_ready = settings.mode == "live" and (
        _configured("TRONGRID_API_KEY")
        or _configured("TRONSCAN_API_KEY")
        or _configured("ETHERSCAN_API_KEY")
        or _configured("ESPLORA_BASE_URL")
    )

    groups = [
        {
            "key": "mode",
            "name": "Runtime mode",
            "summary": "Fixture mode keeps the demo offline and deterministic.",
            "status": "configured" if settings.mode == "fixture" else "configured" if chain_live_ready else "missing",
            "items": [
                {"label": "TRINETRA_MODE", "status": "configured", "detail": settings.mode},
                {"label": "Fixture data", "status": "configured", "detail": "docs/demo_case.json"},
                {"label": "Runtime state", "status": "configured", "detail": "var/"},
            ],
        },
        {
            "key": "chain",
            "name": "Chain data providers",
            "summary": "Live adapters remain closed until provider keys and live mode are configured.",
            "status": "configured" if chain_live_ready else "missing",
            "items": [
                {"label": "TRONGRID_API_KEY", "status": _status(_configured("TRONGRID_API_KEY")), "detail": "TRON provider key"},
                {"label": "TRONSCAN_API_KEY", "status": _status(_configured("TRONSCAN_API_KEY")), "detail": "TRON fallback key"},
                {"label": "ETHERSCAN_API_KEY", "status": _status(_configured("ETHERSCAN_API_KEY")), "detail": "EVM provider key"},
                {"label": "ESPLORA_BASE_URL", "status": _status(_configured("ESPLORA_BASE_URL")), "detail": _value("ESPLORA_BASE_URL") or "public endpoint not set"},
            ],
        },
        {
            "key": "identity",
            "name": "Government identity",
            "summary": "Login buttons link out; callback login requires real metadata and credentials.",
            "status": "configured" if settings.sso_callback_enabled and (cctns_oidc_ready or parichay_oidc_ready) else "disabled",
            "items": [
                {"label": "SSO_CALLBACK_ENABLED", "status": "configured" if settings.sso_callback_enabled else "disabled", "detail": str(settings.sso_callback_enabled).lower()},
                {"label": "CCTNS OIDC", "status": _status(cctns_oidc_ready, enabled=settings.sso_callback_enabled), "detail": "issuer, client id, client secret"},
                {"label": "Parichay OIDC", "status": _status(parichay_oidc_ready, enabled=settings.sso_callback_enabled), "detail": "issuer, client id, client secret"},
            ],
        },
        {
            "key": "dispatch",
            "name": "Notice dispatch",
            "summary": "SAHYOG/export and email dispatch stay specimen-only until legal copy and channels are approved.",
            "status": "configured" if dispatch_ready else "approval_required",
            "items": [
                {"label": "LEGAL_COPY_APPROVED", "status": "configured" if settings.legal_copy_approved else "approval_required", "detail": str(settings.legal_copy_approved).lower()},
                {"label": "NOTICE_SPECIMEN_WATERMARK", "status": "configured" if settings.specimen_watermark else "disabled", "detail": str(settings.specimen_watermark).lower()},
                {"label": "SMTP_URL", "status": _status(_configured("SMTP_URL")), "detail": "dispatch channel secret"},
            ],
        },
    ]
    return {
        "schema": "trinetra.integration_status/1",
        "mode": settings.mode,
        "environment": settings.env,
        "secrets_rendered": False,
        "secret_env_names": sorted(SECRET_ENV_NAMES),
        "groups": groups,
    }
