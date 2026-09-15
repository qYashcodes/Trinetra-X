from __future__ import annotations

import secrets


def registration_options(officer_pis: str, origin: str) -> dict:
    return {
        "enabled": False,
        "officer_pis": officer_pis,
        "challenge": secrets.token_urlsafe(32),
        "user_verification": "required",
        "status": "integration_pending",
        "reason": (
            "WebAuthn requires an approved relying-party identifier, secure origin and "
            "credential policy."
        ),
    }


def verify_registration(payload: dict) -> dict:
    return {
        "verified": False,
        "status": "integration_pending",
        "reason": "A real browser authenticator response is required.",
    }
