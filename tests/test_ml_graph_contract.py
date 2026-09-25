from __future__ import annotations

from app.services.graph_view import omega_graph_payload
from app.services.ml_model import disabled_wallet_model_status
from app.services.risk import risk_check


def test_disabled_wallet_model_status_fingerprints_without_loading(tmp_path) -> None:
    model_path = tmp_path / "wallet_risk_v1.joblib"
    model_path.write_bytes(b"pickle-like model bytes are not deserialized")

    status = disabled_wallet_model_status(model_path=model_path)

    assert status["status"] == "disabled"
    assert status["probability_enabled"] is False
    assert status["calibration_status"] == "disabled_pending_independent_labelled_data"
    assert status["artifact"]["present"] is True
    assert status["artifact"]["load_status"] == "not_loaded_pickle_artifact"
    assert "sha256" in status["artifact"]


def test_risk_result_reports_ml_disabled_without_model_output() -> None:
    result = risk_check("RC_clean")

    assert result["score"] is None
    assert result["posterior"] is None
    assert result["probability_enabled"] is False
    assert result["ml_model"]["status"] == "disabled"
    assert result["ml_model"]["probability_enabled"] is False
    assert result["ml_model"]["calibration_status"] == (
        "disabled_pending_independent_labelled_data"
    )


def test_omega_graph_sanitizes_ml_metadata_and_omits_model_scores() -> None:
    payload = omega_graph_payload(
        {
            "case": {
                "reported_address": "TSeed",
                "payment_txid": "",
                "payment_ts_ms": 0,
                "amount_reported_base": 100,
            },
            "asset": {"symbol": "USDT", "decimals": 0},
            "engine": {"mode": "live"},
            "ml_model": {
                "status": "enabled",
                "model_version": "wallet-risk-v1",
                "model_type": "random_forest",
                "probability_enabled": True,
                "calibration_status": "claimed_calibrated",
                "ml_risk_score": 0.91,
            },
            "hops": [
                {
                    "hop": 1,
                    "source_address": "TSeed",
                    "address": "THop",
                    "value_base": 80,
                    "observed_amount_base": 100,
                    "txids": ["1" * 64],
                    "event_index": 0,
                }
            ],
            "terminal": {"kind": "depth_exhausted"},
        }
    )

    serialized = str(payload)
    assert payload["ml_model"]["status"] == "disabled"
    assert payload["ml_model"]["probability_enabled"] is False
    assert "ml_risk_score" not in serialized
    assert "'score'" not in serialized
    assert payload["tree"]["metadata"]["ml_model"]["model_status"] == "disabled"
    assert payload["tree"]["children"][0]["metadata"]["ml_model"][
        "probability_enabled"
    ] is False
