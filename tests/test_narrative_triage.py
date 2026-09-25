from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import json
import re
from types import SimpleNamespace
from collections.abc import Iterator
import zipfile

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.main as main_module
from app.main import app
from app.models import (
    Case,
    ComplaintNarrative,
    NarrativeAssessment,
    NarrativeAssessmentReview,
    TraceSnapshot,
)
from app.repository import get_or_create_trace, seed_demo
from app.services.demo import demo_case
from app.services.narrative_triage import (
    NarrativeTriageError,
    analyse_narrative,
    extract_searchable_pdf,
    ingest_narrative,
    narrative_export_rows,
    record_narrative_review,
)
from app.services.time import now_ms


def _pdf_bytes(*pages: str) -> bytes:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    for page_text in pages:
        canvas.drawString(48, 780, page_text)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _case(session: Session) -> Case:
    timestamp = now_ms()
    row = Case(
        ack_no="NCRP/2026/TEST/NARRATIVE",
        category="source supplied category",
        jurisdiction="Test Cyber Cell",
        filed_ts_ms=timestamp,
        amount_reported_base=1_000_000,
        asset_symbol="USDT",
        asset_decimals=6,
        chain_family="TRON",
        chain_network="mainnet",
        reported_address="TNarrativeCase11111111111111111111111",
        payment_txid="a" * 64,
        payment_ts_ms=timestamp,
        created_ts_ms=timestamp,
        updated_ts_ms=timestamp,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_analyzer_fails_closed_for_empty_generic_non_english_and_negated_text() -> None:
    empty = analyse_narrative("", language="en")
    generic = analyse_narrative("I paid money to a wallet.", language="en")
    negated = analyse_narrative(
        "This was not ransomware; no files were encrypted.",
        language="en",
    )
    hindi = analyse_narrative("मुझे निवेश के नाम पर पैसे भेजने को कहा गया।", language="hi")

    assert empty["status"] == "unknown"
    assert generic["status"] == "unknown"
    assert negated["status"] == "unknown"
    assert {item["term"] for item in negated["negated_indicators"]} >= {
        "ransomware",
        "files were encrypted",
    }
    assert hindi["status"] == "unsupported_language"
    for result in (empty, generic, negated, hindi):
        assert result["score"] is None
        assert result["posterior"] is None
        assert result["probability_enabled"] is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A fake trading platform promised a guaranteed return.", "investment_scam"),
        ("The task scam required a recharge balance.", "task_based_fraud"),
        ("The victim reported sextortion and a threat to share an intimate photo.", "sextortion"),
        ("A ransom note said the encrypted files needed a decryption key.", "ransomware"),
        ("A phishing link led to a fake login page.", "phishing"),
        ("A darknet market used an onion service for prohibited goods.", "darknet_transaction"),
        (
            "An organized cybercrime network used mule accounts and layering transactions.",
            "organized_cyber_enabled_financial_crime",
        ),
    ],
)
def test_all_seven_typologies_have_deterministic_positive_examples(text: str, expected: str) -> None:
    result = analyse_narrative(text, language="en")
    assert result["status"] == "candidate"
    assert expected in [item["key"] for item in result["candidates"]]


def test_multi_label_and_longest_phrase_matching_are_preserved() -> None:
    result = analyse_narrative(
        "A phishing email led to a fake trading platform promising a guaranteed return.",
        language="en",
    )
    assert [item["key"] for item in result["candidates"]] == [
        "investment_scam",
        "phishing",
    ]
    investment = result["candidates"][0]
    terms = [item["term"] for item in investment["indicators"]]
    assert "fake trading platform" in terms
    assert "trading platform" not in terms


def test_searchable_pdf_extracts_pages_and_rejects_unavailable_text() -> None:
    extracted = extract_searchable_pdf(
        _pdf_bytes("A phishing link was sent.", "The victim entered login details.")
    )
    assert extracted["status"] == "ready"
    assert [page["page"] for page in extracted["pages"]] == [1, 2]
    assert "phishing link" in extracted["text"]
    assert extract_searchable_pdf(b"")["status"] == "text_unavailable"
    assert extract_searchable_pdf(b"not-a-pdf")["status"] == "invalid_input"
    assert extract_searchable_pdf(_pdf_bytes(""))["status"] == "text_unavailable"


def test_persistence_is_idempotent_superseding_and_review_is_append_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        "app.services.narrative_triage.settings",
        SimpleNamespace(var_dir=tmp_path / "var"),
    )
    with Session(engine) as session:
        case = _case(session)
        first, assessment, created = ingest_narrative(
            session,
            case_id=int(case.id or 0),
            source_kind="officer_text",
            source_ref="manual-source",
            language="en",
            original_name="report.txt",
            mime_type="text/plain",
            data=b"A phishing link led to a fake login page.",
            provenance="unit test",
            created_by_pis="48421",
        )
        session.commit()
        repeated, repeated_assessment, repeated_created = ingest_narrative(
            session,
            case_id=int(case.id or 0),
            source_kind="officer_text",
            source_ref="manual-source",
            language="en",
            original_name="report.txt",
            mime_type="text/plain",
            data=b"A phishing link led to a fake login page.",
            provenance="unit test",
            created_by_pis="48421",
        )
        assert repeated.id == first.id
        assert repeated_assessment.id == assessment.id
        assert repeated_created is False

        second, _second_assessment, second_created = ingest_narrative(
            session,
            case_id=int(case.id or 0),
            source_kind="officer_text",
            source_ref="manual-source",
            language="en",
            original_name="report.txt",
            mime_type="text/plain",
            data=b"A ransomware attack left files encrypted and demanded a ransom payment.",
            provenance="unit test revision",
            created_by_pis="48421",
        )
        session.commit()
        session.refresh(first)
        assert second_created is True
        assert first.superseded_by_id == second.id
        assert len(session.exec(select(ComplaintNarrative)).all()) == 2
        assert len(session.exec(select(NarrativeAssessment)).all()) == 2

        with pytest.raises(NarrativeTriageError):
            record_narrative_review(
                session,
                assessment=assessment,
                accepted_typologies=[],
                primary_typology="phishing",
                reviewer_pis="48421",
                note=None,
            )
        record_narrative_review(
            session,
            assessment=assessment,
            accepted_typologies=["phishing"],
            primary_typology="phishing",
            reviewer_pis="48421",
            note="Reviewed against the complaint source.",
        )
        record_narrative_review(
            session,
            assessment=assessment,
            accepted_typologies=[],
            primary_typology="none",
            reviewer_pis="0912",
            note="Later review retained as a separate event.",
        )
        session.commit()
        assert len(session.exec(select(NarrativeAssessmentReview)).all()) == 2
        assert session.get(Case, case.id).category == "source supplied category"
        exported = narrative_export_rows(session, int(case.id or 0))
        serialized = json.dumps(exported)
        assert "A phishing link" not in serialized
        assert "protected_source_route" in serialized
    engine.dispose()


def test_connector_text_and_pdf_are_preserved_as_separate_sources(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Connector:
        def fetch_narrative(self, ack_no: str) -> dict:
            return {
                "text": "A fake trading platform promised a guaranteed return.",
                "language": "en",
                "name": "connector-narrative.txt",
                "source_ref": f"text:{ack_no}",
                "provenance": "connector text field",
            }

        def fetch_document(self, ack_no: str, document_type: str) -> dict:
            return {
                "name": "complaint.pdf",
                "mime_type": "application/pdf",
                "data": _pdf_bytes("A phishing link led to a fake login page."),
                "provenance": "connector complaint PDF",
            }

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setenv("TRINETRA_ENABLE_NARRATIVE_TRIAGE", "true")
    monkeypatch.setenv("TRINETRA_NARRATIVE_TAXONOMY_REVIEWED", "true")
    monkeypatch.setenv("TRINETRA_NARRATIVE_PRIVACY_REVIEWED", "true")
    monkeypatch.setattr(main_module, "complaints", Connector())
    monkeypatch.setattr(
        "app.services.narrative_triage.settings",
        SimpleNamespace(var_dir=tmp_path / "var"),
    )
    monkeypatch.setattr(
        "app.services.audit.settings",
        SimpleNamespace(var_dir=tmp_path / "var"),
    )
    with Session(engine) as session:
        case = _case(session)
        main_module._ingest_connector_narratives(
            session,
            case,
            {"pis": "48421", "name": "Officer", "rank": "Inspector", "role": "io"},
        )
        rows = session.exec(select(ComplaintNarrative).order_by(ComplaintNarrative.id)).all()
        assessments = session.exec(select(NarrativeAssessment).order_by(NarrativeAssessment.id)).all()
        assert [row.source_kind for row in rows] == [
            "complaint_connector_text",
            "complaint_connector_pdf",
        ]
        assert [row.status for row in assessments] == ["candidate", "candidate"]
        assert session.get(Case, case.id).category == "source supplied category"
    engine.dispose()


def test_case_ingest_page_shows_narrative_triage_before_trace_starts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ack_no = "NCRP/2026/MH/1234567"

    class Connector:
        def health(self) -> dict:
            return {"name": "test connector", "status": "ok"}

        def referrals_today(self) -> list[dict]:
            return []

        def sample_references(self) -> list[dict]:
            return []

        def fetch(self, requested_ack: str) -> dict | None:
            if requested_ack != ack_no:
                return None
            record = dict(demo_case()["case"])
            record["ack_no"] = ack_no
            return record

        def fetch_narrative(self, requested_ack: str) -> dict:
            return {
                "text": "A fake trading platform promised a guaranteed return.",
                "language": "en",
                "name": "connector-narrative.txt",
                "source_ref": f"text:{requested_ack}",
                "provenance": "connector text field",
            }

        def fetch_document(self, _requested_ack: str, _document_type: str) -> None:
            return None

    with _isolated_app(tmp_path, monkeypatch) as engine:
        monkeypatch.setattr(main_module, "complaints", Connector())
        with TestClient(app) as client:
            assert client.post("/auth/prototype", data={"role": "io"}).status_code == 200
            intake = client.get("/cases/new")
            token = _csrf(intake.text)
            rendered = client.post(
                "/cases/ingest",
                data={"ack_no": ack_no, "csrf_token": token},
            )
            assert rendered.status_code == 200
            assert "8 of 8 particulars" in rendered.text
            assert "Reported narrative triage" in rendered.text
            assert "Investment scam indicators" in rendered.text
            assert "Start trace" in rendered.text

            with Session(engine) as session:
                case = session.exec(select(Case).where(Case.ack_no == ack_no)).one()
                assert (
                    session.exec(
                        select(TraceSnapshot).where(TraceSnapshot.case_id == case.id)
                    ).all()
                    == []
                )
                assessment = session.exec(select(NarrativeAssessment)).first()
                assert assessment is not None
                assert assessment.status == "candidate"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


@contextmanager
def _isolated_app(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
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
    monkeypatch.setenv("TRINETRA_ENABLE_NARRATIVE_TRIAGE", "true")
    monkeypatch.setenv("TRINETRA_NARRATIVE_TAXONOMY_REVIEWED", "true")
    monkeypatch.setenv("TRINETRA_NARRATIVE_PRIVACY_REVIEWED", "true")
    monkeypatch.setattr(
        "app.services.narrative_triage.settings",
        SimpleNamespace(var_dir=tmp_path / "var"),
    )
    monkeypatch.setattr(
        "app.services.audit.settings",
        SimpleNamespace(var_dir=tmp_path / "var"),
    )
    try:
        yield engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_authenticated_case_routes_review_and_bundle_exclude_raw_text(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _isolated_app(tmp_path, monkeypatch) as engine:
        with TestClient(app) as client:
            assert client.post("/auth/prototype", data={"role": "io"}).status_code == 200
            with Session(engine) as session:
                case = seed_demo(session)
                snapshot, _finding = get_or_create_trace(session, case, trace_mode="fixture")
                case_id = int(case.id or 0)
                snapshot_id = int(snapshot.id or 0)

            intake = client.get(f"/cases/new?case_id={case_id}")
            token = _csrf(intake.text)
            secret_text = "<script>alert('private')</script> A phishing link led to a fake login page."
            uploaded = client.post(
                f"/cases/{case_id}/narratives",
                data={"csrf_token": token, "language": "en", "narrative_text": secret_text},
                follow_redirects=False,
            )
            assert uploaded.status_code == 303
            assert uploaded.headers["location"] == f"/cases/new?case_id={case_id}#narrative-triage"
            rendered = client.get(f"/cases/new?case_id={case_id}")
            assert "Reported narrative triage" in rendered.text
            assert "Phishing indicators" in rendered.text
            assert secret_text not in rendered.text
            assert "&lt;script&gt;" not in rendered.text
            trace = client.get(f"/traces/{snapshot_id}")
            assert "Reported narrative triage" not in trace.text
            assert "Phishing indicators" not in trace.text

            with Session(engine) as session:
                assessment = session.exec(select(NarrativeAssessment)).first()
                assert assessment is not None
                assessment_id = int(assessment.id or 0)
            review = client.post(
                f"/cases/{case_id}/narrative-assessments/{assessment_id}/review",
                data={
                    "csrf_token": token,
                    "accepted_typologies": "phishing",
                    "primary_typology": "phishing",
                    "review_note": "Officer reviewed the indicator references.",
                },
                follow_redirects=False,
            )
            assert review.status_code == 303
            assert review.headers["location"] == f"/cases/new?case_id={case_id}#narrative-triage"

            bundle = client.get(f"/api/cases/{case_id}/evidence-bundle.zip")
            assert bundle.status_code == 200
            with zipfile.ZipFile(BytesIO(bundle.content)) as archive:
                assert "narrative_assessments.json" in archive.namelist()
                payload = archive.read("narrative_assessments.json").decode("utf-8")
                assert secret_text not in payload
                assert "phishing" in payload
                assert not any(name.startswith("complaint_narratives/") for name in archive.namelist())

            audit_text = (tmp_path / "var" / "audit.jsonl").read_text(encoding="utf-8")
            assert secret_text not in audit_text
            assert "narrative.ingested" in audit_text
            assert "narrative.reviewed" in audit_text
