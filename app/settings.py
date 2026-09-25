from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("TRINETRA_ENV", "development")
    mode: str = os.getenv("TRINETRA_MODE", "fixture")
    db_url: str = os.getenv("TRINETRA_DB_URL", "sqlite:///var/trinetra.db")
    demo_clock: str = os.getenv("DEMO_CLOCK", "2026-08-30T04:11:00Z")
    session_secret: str | None = _optional_env("SESSION_SECRET")
    session_cookie_secure: bool = (
        os.getenv("TRINETRA_SESSION_COOKIE_SECURE", "false").lower() == "true"
    )
    session_idle_obscure_seconds: int = int(
        os.getenv("TRINETRA_SESSION_IDLE_OBSCURE_SECONDS", "120")
    )
    session_idle_warning_seconds: int = int(
        os.getenv("TRINETRA_SESSION_IDLE_WARNING_SECONDS", "780")
    )
    session_idle_timeout_seconds: int = int(
        os.getenv("TRINETRA_SESSION_IDLE_TIMEOUT_SECONDS", "900")
    )
    presence_absence_seconds: int = int(
        os.getenv("TRINETRA_PRESENCE_ABSENCE_SECONDS", "15")
    )
    presence_check_interval_ms: int = int(
        os.getenv("TRINETRA_PRESENCE_CHECK_INTERVAL_MS", "400")
    )
    presence_resume_grace_seconds: int = int(
        os.getenv("TRINETRA_PRESENCE_RESUME_GRACE_SECONDS", "5")
    )
    data_stale_after_seconds: int = int(
        os.getenv("TRINETRA_DATA_STALE_AFTER_SECONDS", "900")
    )
    risk_lookback_days: int = int(os.getenv("TRINETRA_RISK_LOOKBACK_DAYS", "30"))
    provider_request_budget_per_minute: int = int(
        os.getenv("TRINETRA_PROVIDER_REQUEST_BUDGET_PER_MINUTE", "120")
    )
    provider_interactive_reserve_per_minute: int = int(
        os.getenv("TRINETRA_PROVIDER_INTERACTIVE_RESERVE_PER_MINUTE", "40")
    )
    watchlist_cap: int = int(os.getenv("TRINETRA_WATCHLIST_CAP", "100"))
    watch_auto_min_base: int = int(
        os.getenv("TRINETRA_WATCH_AUTO_MIN_BASE", "1000000000")
    )
    watch_hot_value_base: int = int(
        os.getenv("TRINETRA_WATCH_HOT_VALUE_BASE", "10000000000")
    )
    watch_material_transfer_base: int = int(
        os.getenv("TRINETRA_WATCH_MATERIAL_TRANSFER_BASE", "1000000000")
    )
    watch_hot_interval_seconds: int = int(
        os.getenv("TRINETRA_WATCH_HOT_INTERVAL_SECONDS", "60")
    )
    watch_standard_interval_seconds: int = int(
        os.getenv("TRINETRA_WATCH_STANDARD_INTERVAL_SECONDS", "300")
    )
    watch_cold_interval_seconds: int = int(
        os.getenv("TRINETRA_WATCH_COLD_INTERVAL_SECONDS", "1800")
    )
    watch_dormant_after_seconds: int = int(
        os.getenv("TRINETRA_WATCH_DORMANT_AFTER_SECONDS", "86400")
    )
    watch_poll_batch_size: int = int(
        os.getenv("TRINETRA_WATCH_POLL_BATCH_SIZE", "5")
    )
    cctns_login_url: str = os.getenv(
        "CCTNS_LOGIN_URL", "https://cctns.megpolice.gov.in/Login.aspx"
    )
    sahyog_portal_url: str = os.getenv(
        "SAHYOG_PORTAL_URL", "https://parichay.nic.in/pnv1/assets/login?sid=1234567899"
    )
    parichay_login_url: str = os.getenv("PARICHAY_LOGIN_URL", "https://parichay.nic.in/")
    sso_link_target: str = os.getenv("SSO_LINK_TARGET", "_blank")
    sso_callback_enabled: bool = os.getenv("SSO_CALLBACK_ENABLED", "false").lower() == "true"
    legal_copy_approved: bool = os.getenv("LEGAL_COPY_APPROVED", "false").lower() == "true"
    specimen_watermark: bool = os.getenv("NOTICE_SPECIMEN_WATERMARK", "true").lower() == "true"
    countersign_threshold_usdt: int = int(os.getenv("COUNTERSIGN_THRESHOLD_USDT", "10000"))
    fixture_dir: Path = ROOT_DIR / os.getenv("TRINETRA_FIXTURE_DIR", "fixtures")
    cache_path: Path = ROOT_DIR / os.getenv("TRINETRA_CACHE_PATH", ".cache/trinetra")
    demo_case_path: Path = ROOT_DIR / "docs" / "demo_case.json"
    var_dir: Path = ROOT_DIR / "var"


settings = Settings()
