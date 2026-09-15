from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.main as main_module
from app.main import app
from app.models import Finding, Notice, TraceSnapshot
from app.services.explainability import (
    ALLOCATION_APPROXIMATION,
    methodology_annex,
    trace_explainability,
)
from app.services.notices import sahyog_manifest


QUESTIONS = {
    "Why was this path followed?",
    "Where did this attributed amount come from?",
    "What is this address, and what is it not?",
    "Why did the trace end here?",
    "What is this based on?",
}


@pytest.fixture
def isolated_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[main_module.get_session] = session_override
    monkeypatch.setattr(main_module, "append_audit_event", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main_module, "read_audit_events", lambda **_kwargs: [])
    try:
        yield engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _live_snapshot() -> dict:
    return {
        "schema": "trinetra.snapshot/2",
        "case": {
            "reported_address": "TSeed1111111111111111111111111111111",
            "payment_txid": "0" * 64,
            "payment_ts": 1_789_337_235_000,
            "amount_reported_base": 700,
        },
        "chain": {"family": "TRON", "network": "mainnet"},
        "asset": {"symbol": "USDT", "decimals": 6},
        "engine": {"mode": "live", "version": "live-tron-v1"},
        "params": {
            "strategy": "value_weighted",
            "max_depth": 3,
            "time_window_hours": 8,
            "value_floor_share": "0.02",
            "breadth_cap": 2,
            "address_budget": 10,
        },
        "seed_match": {
            "matched": True,
            "txid": "0" * 64,
            "amount_base": 700,
            "retrieval_ts_ms": 1_789_337_236_000,
            "provider": "trongrid",
            "raw_sha256": "a" * 64,
        },
        "hops": [
            {
                "hop": 1,
                "address": "THop11111111111111111111111111111111",
                "source_address": "TSeed1111111111111111111111111111111",
                "value_base": 280,
                "ts_ms": 1_789_337_237_000,
                "txids": ["1" * 64],
                "event_ref": "TRON:mainnet:1:0",
                "event_index": 0,
                "block": 71_000_001,
                "retrieval_ts_ms": 1_789_337_238_000,
                "provider_request_ref": "trongrid:request-1",
                "raw_sha256": "b" * 64,
                "frontier_state": "expanded",
                "allocation": {
                    "policy": "proportional",
                    "version": "trinetra.allocation/1",
                    "incoming_attributed_base": 700,
                    "observed_outgoing_base": 400,
                    "denominator_base": 1_000,
                    "numerator_base": 280_002,
                    "outgoing_attributed_base": 280,
                    "residual_before": 2,
                    "residual_numerator": 2,
                    "initial_balance_basis": "max(incoming_attributed, observed_outgoing_total)",
                },
                "scheduling": {
                    "strategy": "value_weighted",
                    "local_candidate_rank": 1,
                    "eligible_candidate_count": 2,
                    "decision": "expanded",
                },
                "global_pending_decision": {
                    "strategy": "value_weighted",
                    "selected_rank": 1,
                    "pending_count": 2,
                    "ranking": [
                        {"event_ref": "TRON:mainnet:1:0", "priority_base": 280},
                        {"event_ref": "TRON:mainnet:2:0", "priority_base": 210},
                    ],
                },
            }
        ],
        "parked": [
            {
                "branch_id": "TRON:mainnet:2:0",
                "event_ref": "TRON:mainnet:2:0",
                "address": "TParked11111111111111111111111111111",
                "source_address": "TSeed1111111111111111111111111111111",
                "value_base": 210,
                "ts_ms": 1_789_337_237_100,
                "txids": ["2" * 64],
                "event_index": 0,
                "block": 71_000_002,
                "frontier_state": "deferred",
                "deferral_reason": "breadth_cap",
            }
        ],
        "terminal": {
            "kind": "depth_exhausted",
            "note": "Configured depth was reached with retained frontier work.",
        },
    }


def test_live_explanation_preserves_scheduler_arithmetic_and_evidence_identity() -> None:
    snapshot = _live_snapshot()
    explanation = trace_explainability(
        snapshot,
        canonical_events=[
            {
                "evidence_ref": "TRON:mainnet:1:0",
                "txid": "1" * 64,
                "destination_address": "THop11111111111111111111111111111111",
                "block_height": 71_000_001,
                "event_index": 0,
                "observed_ts_ms": 1_789_337_237_000,
                "finality": "provider_confirmed",
                "raw_sha256": "b" * 64,
            }
        ],
        frontier_items=[
            {
                "event_ref": "TRON:mainnet:1:0",
                "priority_base": 280,
                "priority_reason": "value_weighted",
                "state": "completed",
            }
        ],
    )

    assert explanation["default_register"] == "investigator"
    hop = next(item for item in explanation["items"] if item["id"] == "hop:0")
    for register in ("investigator", "technical"):
        assert {section["title"] for section in hop["registers"][register]} == QUESTIONS

    arithmetic = hop["registers"]["technical"][1]
    assert arithmetic["facts"]["formula"] == "280002 // 1000 = 280"
    assert arithmetic["facts"]["residual_numerator"] == 2
    assert ALLOCATION_APPROXIMATION in arithmetic["body"]

    scheduler = hop["registers"]["technical"][0]
    assert scheduler["facts"]["pending_work_at_step"]["selected_rank"] == 1
    assert scheduler["facts"]["sibling_edges"][1]["deferral_reason"] == "breadth_cap"
    evidence = hop["registers"]["technical"][4]["facts"]
    assert evidence["txid"] == "1" * 64
    assert evidence["block_height"] == 71_000_001
    assert evidence["event_index"] == 0
    assert evidence["provider_request_ref"] == "trongrid:request-1"
    assert evidence["raw_sha256"] == "b" * 64
    assert hop["registers"]["investigator"][2]["body"].startswith("Unscored -")


def test_non_custody_terminal_explains_the_finding_gate_and_stronger_evidence() -> None:
    explanation = trace_explainability(_live_snapshot())
    terminal = next(item for item in explanation["items"] if item["id"] == "terminal")
    end_facts = terminal["registers"]["investigator"][3]["facts"]

    assert end_facts["terminal_kind"] == "depth_exhausted"
    assert end_facts["case_stage"] == "trace_incomplete"
    assert end_facts["finding_permitted"] is False
    assert "Resume or re-run" in end_facts["stronger_outcome_requires"]
    assert terminal["amount_base"] is None


def test_methodology_annex_records_bounds_taxonomy_limits_and_no_model_output() -> None:
    annex = methodology_annex(_live_snapshot())

    assert annex["strategy"]["selected"] == "value_weighted"
    assert annex["bounds"]["max_depth"] == 3
    assert annex["allocation"]["known_approximation"] == ALLOCATION_APPROXIMATION
    assert {row["terminal_kind"] for row in annex["terminal_taxonomy"]} >= {
        "provider_error",
        "unsupported_chain",
        "verified_custody",
    }
    assert "Probabilities are disabled pending independent labelled data and calibration." in annex["limits"]
    serialized = json.dumps(annex)
    assert '"score"' not in serialized
    assert '"posterior"' not in serialized


def test_fixture_trace_and_canvas_expose_both_explanation_registers(isolated_app) -> None:
    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "io"})
        assert client.get("/docket").status_code == 200

        trace = client.get("/traces/1")
        canvas = client.get("/cases/1/canvas?snapshot=1")
        api = client.get("/api/traces/1/explanations")

        assert trace.status_code == 200
        assert canvas.status_code == 200
        assert api.status_code == 200
        for page in (trace.text, canvas.text):
            assert "Explain trace" in page
            assert 'data-explain-register="investigator"' in page
            assert 'data-explain-register="technical"' in page
            assert 'data-trace-explainability' in page
        payload = api.json()
        assert payload["schema"] == "trinetra.trace_explainability/1"
        hop = next(item for item in payload["items"] if item["id"] == "hop:0")
        evidence = hop["registers"]["technical"][4]["facts"]
        assert evidence["txid"]
        assert len(evidence["raw_sha256"]) == 64


def test_evidence_and_sahyog_exports_include_hashed_methodology_annex(isolated_app) -> None:
    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "io"})
        client.get("/docket")
        bundle = client.get("/api/cases/1/evidence-bundle.zip")

        assert bundle.status_code == 200
        with zipfile.ZipFile(BytesIO(bundle.content)) as archive:
            names = set(archive.namelist())
            annex_json = json.loads(archive.read("methodology_annex.json"))
            annex_text = archive.read("methodology_annex.txt").decode("utf-8")
            readme = archive.read("README.txt").decode("utf-8")
        assert {"methodology_annex.json", "methodology_annex.txt"} <= names
        assert annex_json["schema"] == "trinetra.methodology_annex/1"
        assert "TRINETRA methodology annex" in annex_text
        assert "methodology_annex.json" in readme

        with Session(isolated_app) as session:
            snapshot = session.get(TraceSnapshot, 1)
            finding = session.get(Finding, 1)
            assert snapshot is not None
            assert finding is not None
            notice = Notice(
                case_id=1,
                finding_id=1,
                notice_no="TEST/NOTICE/1",
                status="draft",
                deadline_hours=24,
                created_by_pis="74821",
                created_ts_ms=1_789_337_240_000,
                updated_ts_ms=1_789_337_240_000,
            )
            session.add(notice)
            session.commit()
            manifest = sahyog_manifest(snapshot.result_json, notice.notice_no)

        annex_attachment = next(
            row for row in manifest["attachments"] if row["name"] == "methodology-annex.json"
        )
        assert manifest["methodology_annex"]["schema"] == "trinetra.methodology_annex/1"
        assert len(annex_attachment["sha256"]) == 64
        assert annex_attachment["sha256"] != "integration_pending"
