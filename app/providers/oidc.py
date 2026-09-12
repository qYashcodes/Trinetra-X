from __future__ import annotations

import secrets


def build_authorization_request(provider: str, redirect_uri: str) -> dict:
    return {
        "provider": provider,
        "status": "integration_pending",
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(24),
        "pkce_required": True,
        "redirect_uri": redirect_uri,
    }


def verify_callback(provider: str, params: dict) -> dict:
    return {
        "provider": provider,
        "status": "integration_pending",
        "reason": "OIDC metadata and client credentials are not configured.",
    }
