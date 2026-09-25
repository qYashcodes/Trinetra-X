from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import app.repository as repository_module
from app.models import Case, Finding, NarrativeAssessment, NarrativeAssessmentReview
from app.services.narrative_showcase import seed_narrative_showcase_cases
import app.services.narrative_triage as narrative_triage_module


EXPECTED_TYPOLOGIES = {
    "investment_scam",
    "task_based_fraud",
    "sextortion",
    "ransomware",
    "phishing",
    "darknet_transaction",
    "organized_cyber_enabled_financial_crime",
}


def test_narrative_showcase_seed_is_idempotent_and_explains_each_category(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(narrative_triage_module, "settings", SimpleNamespace(var_dir=tmp_path / "var"))
    monkeypatch.setattr(
        repository_module,
        "settings",
        SimpleNamespace(var_dir=tmp_path / "var", data_stale_after_seconds=900),
    )

    with Session(engine) as session:
        assert seed_narrative_showcase_cases(session, timestamp_ms=2_000_000_000_000) == 7
        assert seed_narrative_showcase_cases(session, timestamp_ms=2_000_000_000_000) == 0

        cases = session.exec(
            select(Case).where(Case.ack_no.startswith("NCRP/2026/SHOW/"))
        ).all()
        assert len(cases) == 7
        assert {case.category for case in cases} == {
            "complaint supplied category - investment",
            "complaint supplied category - online work",
            "complaint supplied category - online blackmail",
            "complaint supplied category - system incident",
            "complaint supplied category - account access",
            "complaint supplied category - online marketplace",
            "complaint supplied category - coordinated fraud",
        }

        assessments = session.exec(select(NarrativeAssessment)).all()
        assert len(assessments) == 7
        seen_typologies: set[str] = set()
        for assessment in assessments:
            assert assessment.status == "candidate"
            candidates = assessment.result_json["candidates"]
            assert len(candidates) == 1
            candidate = candidates[0]
            seen_typologies.add(candidate["key"])
            assert assessment.result_json["score"] is None
            assert assessment.result_json["posterior"] is None
            assert assessment.result_json["probability_enabled"] is False
            assert "Suggested because" in candidate["reasoning"]
            assert candidate["indicators"]
        assert seen_typologies == EXPECTED_TYPOLOGIES

        reviews = session.exec(select(NarrativeAssessmentReview)).all()
        assert len(reviews) == 7
        for review in reviews:
            assert review.primary_typology in EXPECTED_TYPOLOGIES
            assert review.accepted_typologies == [review.primary_typology]
            assert review.note and "Showcase review:" in review.note

        assert session.exec(select(Finding)).all() == []

    engine.dispose()
