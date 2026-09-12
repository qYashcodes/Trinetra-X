from __future__ import annotations

import secrets


def registration_options(officer_pis: str, origin: str) -> dict:
    if origin.startswith("http://") and "127.0.0.1" not in origin and "localhost" not in origin:
        return {"enabled": False, "reason": "WebAuthn requires a stable secure origin."}
    return {
        "enabled": True,
        "officer_pis": officer_pis,
        "challenge": secrets.token_urlsafe(32),
        "user_verification": "required",
    }


def verify_registration(payload: dict) -> dict:
    return {
        "verified": False,
        "status": "integration_pending",
        "reason": "A real browser authenticator response is required.",
    }
