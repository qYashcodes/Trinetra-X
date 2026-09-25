from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.hash import sha256_bytes
from app.settings import ROOT_DIR


ML_STATUS_SCHEMA = "trinetra.wallet_model_status/1"
ML_FEATURE_PREVIEW_SCHEMA = "trinetra.ml_feature_preview/1"
ML_LAB_REPLAY_SCHEMA = "trinetra.ml_lab_replay/1"
DEFAULT_MODEL_ARTIFACT = ROOT_DIR / "app" / "ml" / "models" / "wallet_risk_v1.joblib"


def disabled_wallet_model_status(
    *,
    model_path: Path | None = None,
    feature_revision: str | None = None,
) -> dict[str, Any]:
    """Report ML readiness without loading untrusted model artifacts."""
    artifact = _artifact_status(model_path or DEFAULT_MODEL_ARTIFACT)
    return {
        "schema": ML_STATUS_SCHEMA,
        "status": "disabled",
        "model_version": "wallet-risk-v1",
        "model_type": "random_forest",
        "artifact": artifact,
        "feature_revision": feature_revision,
        "probability_enabled": False,
        "calibration_status": "disabled_pending_independent_labelled_data",
        "reason": (
            "Model scoring is not enabled until independent labelled data, leakage controls, "
            "calibration and false-positive review are completed."
        ),
    }


def graph_model_metadata(model_status: dict[str, Any] | None) -> dict[str, Any]:
    status = dict(model_status or disabled_wallet_model_status())
    return {
        "model_status": str(status.get("status") or "disabled"),
        "model_version": status.get("model_version"),
        "model_type": status.get("model_type"),
        "probability_enabled": False,
        "calibration_status": (
            status.get("calibration_status")
            or "disabled_pending_independent_labelled_data"
        ),
    }


def prototype_model_status(
    *,
    model_path: Path | None = None,
    feature_revision: str | None = None,
) -> dict[str, Any]:
    """Return user-facing ML integration status without enabling scoring."""
    status = disabled_wallet_model_status(
        model_path=model_path,
        feature_revision=feature_revision,
    )
    return {
        **status,
        "display_name": "Wallet behavioural ML prototype",
        "integration_mode": "embedded_metadata_and_offline_lab",
        "output_status": "disabled_for_live_risk_scoring",
        "feature_schema": feature_revision or "trinetra.behavioural_features/1",
        "checklist": [
            {"label": "Model artifact fingerprinted", "status": "complete" if status["artifact"]["present"] else "pending"},
            {"label": "Feature extractor connected", "status": "complete"},
            {"label": "Risk page metadata connected", "status": "complete"},
            {"label": "Offline lab replay available", "status": "complete"},
            {"label": "Independent calibration", "status": "pending"},
            {"label": "Public scoring gate", "status": "disabled"},
        ],
    }


def ml_feature_vector_preview(result: dict[str, Any] | None) -> dict[str, Any]:
    """Project current behavioural features into the ML-facing prototype vector."""
    feature_set = dict((result or {}).get("feature_set") or {})
    items = {
        str(item.get("key")): dict(item.get("values") or {})
        for item in feature_set.get("items") or []
        if isinstance(item, dict)
    }
    rows = [
        _preview_row("observed_event_count", feature_set.get("observed_event_count"), "Observed transfers in the lookup window."),
        _preview_row("inbound_event_count", feature_set.get("inbound_event_count"), "Incoming transfer events for this address."),
        _preview_row("outbound_event_count", feature_set.get("outbound_event_count"), "Outgoing transfer events for this address."),
        _preview_row("forward_ratio_bp", _item_value(items, "forward_ratio", "outbound_to_inbound_bp"), "Observed outflow divided by observed inflow, in basis points."),
        _preview_row("retained_share_bp", _item_value(items, "resting_balance", "retained_share_bp"), "Observed retained value as a share of inflow."),
        _preview_row("median_hold_ms", _item_value(items, "sweep_timing", "median_hold_ms"), "Median deterministic FIFO hold time."),
        _preview_row("fan_in_count", _item_value(items, "fan_in_out_ratio", "fan_in_count"), "Distinct observed inbound sources."),
        _preview_row("fan_out_count", _item_value(items, "fan_in_out_ratio", "fan_out_count"), "Distinct observed outbound destinations."),
        _preview_row("top_destination_share_bp", _item_value(items, "destination_consistency", "top_destination_share_bp"), "Share of outflow sent to the most-used destination."),
        _preview_row("exposed_counterparty_count", _item_value(items, "counterparty_exposure", "distinct_exposed_counterparties"), "Direct counterparties matching reported or reviewed lists."),
    ]
    has_values = any(row["value"] is not None for row in rows)
    return {
        "schema": ML_FEATURE_PREVIEW_SCHEMA,
        "status": "ready" if has_values else "awaiting_live_or_featured_history",
        "feature_revision": feature_set.get("schema") or "trinetra.behavioural_features/1",
        "rows": rows,
        "note": (
            "These are ML-ready feature inputs only. They do not enable a live model score."
            if has_values
            else "Run a live lookup with confirmed transfer history to populate this vector."
        ),
    }


def offline_lab_replay() -> dict[str, Any]:
    """Static ML demonstration samples for prototype walkthroughs."""
    return {
        "schema": ML_LAB_REPLAY_SCHEMA,
        "mode": "offline_demo_replay",
        "model_version": "wallet-risk-v1",
        "model_type": "random_forest",
        "probability_enabled": False,
        "calibration_status": "disabled_pending_independent_labelled_data",
        "disclaimer": (
            "Replay values are fixed prototype outputs for demonstrating the embedded ML workflow; "
            "they are not used by live risk checks or case decisions."
        ),
        "samples": [
            {
                "label": "Rapid pass-through sample",
                "address_label": "TRON sample A",
                "lab_score_bp": 8200,
                "pattern_label": "pattern match candidate",
                "top_features": [
                    {"name": "forward_ratio_bp", "value": 9000},
                    {"name": "median_hold_ms", "value": 1000},
                    {"name": "retained_share_bp", "value": 1000},
                ],
            },
            {
                "label": "Sparse-history sample",
                "address_label": "TRON sample B",
                "lab_score_bp": 2400,
                "pattern_label": "insufficient pattern evidence",
                "top_features": [
                    {"name": "observed_event_count", "value": 3},
                    {"name": "fan_in_count", "value": 1},
                    {"name": "fan_out_count", "value": 1},
                ],
            },
            {
                "label": "Consolidation sample",
                "address_label": "TRON sample C",
                "lab_score_bp": 6700,
                "pattern_label": "review-priority candidate",
                "top_features": [
                    {"name": "fan_in_count", "value": 7},
                    {"name": "top_destination_share_bp", "value": 7600},
                    {"name": "outbound_event_count", "value": 5},
                ],
            },
        ],
    }


def _artifact_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False, "path": str(path)}
    data = path.read_bytes()
    return {
        "present": True,
        "path": str(path),
        "sha256": sha256_bytes(data),
        "load_status": "not_loaded_pickle_artifact",
    }


def _item_value(items: dict[str, dict[str, Any]], item_key: str, value_key: str) -> Any:
    return items.get(item_key, {}).get(value_key)


def _preview_row(key: str, value: Any, meaning: str) -> dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "display": "not available" if value is None else value,
        "meaning": meaning,
    }
