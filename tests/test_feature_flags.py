from __future__ import annotations

import os
from pathlib import Path

from app.env import load_project_env
from app.services.capabilities import capability_matrix
from app.services.feature_flags import feature_flags


def test_project_env_loader_reads_dotenv_without_overriding_shell() -> None:
    names = (
        "TRINETRA_ENABLE_LIVE_TRON",
        "TRONGRID_API_KEY",
        "TRONSCAN_API_KEY",
        "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED",
        "TRINETRA_LIVE_TRON_SMOKE_VERIFIED",
        "TRINETRA_LIVE_TRON_TRACE_VERIFIED",
    )
    original = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        os.environ["TRONGRID_API_KEY"] = "shell-key"
        env_dir = Path("var")
        env_dir.mkdir(exist_ok=True)
        env_file = env_dir / "test-env-loader.env"
        env_file.write_text(
            "\n".join(
                [
                    "TRINETRA_ENABLE_LIVE_TRON=true",
                    "TRONGRID_API_KEY=file-key",
                    'TRONSCAN_API_KEY="scan-key"',
                    "export TRINETRA_LIVE_TRON_SCHEMA_VERIFIED=true",
                    "TRINETRA_LIVE_TRON_SMOKE_VERIFIED=true # verified locally",
                    "TRINETRA_LIVE_TRON_TRACE_VERIFIED=true",
                ]
            ),
            encoding="utf-8",
        )

        loaded = load_project_env(env_file)

        assert os.environ["TRONGRID_API_KEY"] == "shell-key"
        assert "TRONGRID_API_KEY" not in loaded
        assert os.environ["TRONSCAN_API_KEY"] == "scan-key"
        assert feature_flags()["live_tron_provider"]["enabled"] is True
        assert feature_flags()["live_tron_trace"]["enabled"] is True
    finally:
        if "env_file" in locals():
            env_file.unlink(missing_ok=True)
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_live_tron_requires_flag_and_key(monkeypatch) -> None:
    for name in (
        "TRINETRA_ENABLE_LIVE_TRON",
        "TRONGRID_API_KEY",
        "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED",
        "TRINETRA_LIVE_TRON_SMOKE_VERIFIED",
        "TRINETRA_LIVE_TRON_TRACE_VERIFIED",
    ):
        monkeypatch.delenv(name, raising=False)
    assert feature_flags()["live_tron_provider"]["enabled"] is False
    assert feature_flags()["live_tron_trace"]["enabled"] is False

    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    assert feature_flags()["live_tron_provider"]["enabled"] is False
    assert "TRONGRID_API_KEY" in feature_flags()["live_tron_provider"]["blocked_reason"]

    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    assert feature_flags()["live_tron_provider"]["enabled"] is False
    blocked_reason = str(feature_flags()["live_tron_provider"]["blocked_reason"])
    assert "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED" in blocked_reason

    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    assert feature_flags()["live_tron_provider"]["enabled"] is True
    assert feature_flags()["live_tron_trace"]["enabled"] is False

    monkeypatch.setenv("TRINETRA_LIVE_TRON_TRACE_VERIFIED", "true")
    assert feature_flags()["live_tron_trace"]["enabled"] is True


def test_risky_capabilities_default_disabled(monkeypatch) -> None:
    for name in (
        "TRINETRA_ENABLE_PROVIDER_IMPORTS",
        "TRINETRA_ENABLE_PROTOCOL_DECODERS",
        "TRINETRA_ENABLE_PRIVACY_REVIEW",
    ):
        monkeypatch.delenv(name, raising=False)

    flags = feature_flags()

    assert flags["provider_custody_import"]["enabled"] is False
    assert flags["protocol_decoders"]["enabled"] is False
    assert flags["privacy_review"]["enabled"] is False
    assert flags["truthful_trace_result"]["enabled"] is True


def test_risky_capabilities_require_approval_gates(monkeypatch) -> None:
    monkeypatch.setenv("TRINETRA_ENABLE_PROVIDER_IMPORTS", "true")
    monkeypatch.setenv("TRINETRA_ENABLE_PROTOCOL_DECODERS", "true")
    monkeypatch.setenv("TRINETRA_ENABLE_PRIVACY_REVIEW", "true")

    flags = feature_flags()

    assert flags["provider_custody_import"]["enabled"] is False
    assert flags["provider_custody_import"]["requested"] is True
    assert flags["protocol_decoders"]["enabled"] is False
    assert flags["protocol_decoders"]["requested"] is True
    assert flags["privacy_review"]["enabled"] is False
    assert flags["privacy_review"]["requested"] is True

    monkeypatch.setenv("TRINETRA_PROVIDER_SCHEMA_APPROVED", "true")
    monkeypatch.setenv("TRINETRA_PROVIDER_AUTHORITY_CONFIGURED", "true")
    monkeypatch.setenv("TRINETRA_PROTOCOL_DECODERS_LIVE_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_PRIVACY_REVIEW_APPROVED", "true")

    flags = feature_flags()

    assert flags["provider_custody_import"]["enabled"] is True
    assert flags["protocol_decoders"]["enabled"] is True
    assert flags["privacy_review"]["enabled"] is True


def test_capability_matrix_separates_provider_contract_from_live_enablement() -> None:
    allowed = {
        "unavailable",
        "fixture-tested",
        "integration-tested",
        "live-verified",
        "enabled",
    }
    capabilities = capability_matrix()
    by_key = {item["key"]: item for item in capabilities}

    assert {item["state"] for item in capabilities} <= allowed
    assert by_key["tron_provider_contract"]["state"] == "integration-tested"
    assert by_key["live_tron_seed_verification"]["state"] == "integration-tested"
    assert by_key["live_tron_usdt"]["state"] == "unavailable"


def test_capability_matrix_reflects_enabled_gates_without_overstating_live_trace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TRINETRA_ENABLE_LIVE_TRON", "true")
    monkeypatch.setenv("TRONGRID_API_KEY", "test-key")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SCHEMA_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_SMOKE_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_LIVE_TRON_TRACE_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_ENABLE_PROVIDER_IMPORTS", "true")
    monkeypatch.setenv("TRINETRA_PROVIDER_SCHEMA_APPROVED", "true")
    monkeypatch.setenv("TRINETRA_PROVIDER_AUTHORITY_CONFIGURED", "true")
    monkeypatch.setenv("TRINETRA_ENABLE_PROTOCOL_DECODERS", "true")
    monkeypatch.setenv("TRINETRA_PROTOCOL_DECODERS_LIVE_VERIFIED", "true")
    monkeypatch.setenv("TRINETRA_ENABLE_PRIVACY_REVIEW", "true")
    monkeypatch.setenv("TRINETRA_PRIVACY_REVIEW_APPROVED", "true")

    by_key = {item["key"]: item for item in capability_matrix()}

    assert by_key["live_tron_seed_verification"]["state"] == "enabled"
    assert "custody findings remain disabled" in by_key["live_tron_seed_verification"]["detail"]
    assert by_key["live_tron_usdt"]["state"] == "enabled"
    assert by_key["custody_provider_import"]["state"] == "enabled"
    assert by_key["bridge_defi_decoders"]["state"] == "enabled"
    assert by_key["privacy_boundaries"]["state"] == "enabled"
