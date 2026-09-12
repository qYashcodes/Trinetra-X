from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("TRINETRA_ENV", "development")
    mode: str = os.getenv("TRINETRA_MODE", "fixture")
    db_url: str = os.getenv("TRINETRA_DB_URL", "sqlite:///var/trinetra.db")
    demo_clock: str = os.getenv("DEMO_CLOCK", "2026-08-30T04:11:00Z")
    session_secret: str = os.getenv("SESSION_SECRET", "dev-only-change-me")
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
