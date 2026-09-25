from __future__ import annotations

import json

from sqlmodel import Session, select

from app.models import (
    Case,
    NarrativeAssessment,
    NarrativeAssessmentReview,
    OfficerRole,
    TraceSnapshot,
)
from app.repository import get_or_create_trace
from app.services.narrative_triage import ingest_narrative, record_narrative_review
from app.services.officers import ensure_case_assignment, upsert_officer_profile
from app.services.time import now_ms
from app.settings import ROOT_DIR


SEED_PATH = ROOT_DIR / "fixtures" / "narrative_showcase_v1.json"


def seed_narrative_showcase_cases(
    session: Session,
    *,
    timestamp_ms: int | None = None,
) -> int:
    """Idempotently add seven narrative-triage showcase cases.

    This seed is intentionally separate from the protected tracing fixture. It
    creates ordinary demo cases, fail-closed trace snapshots, narrative sources,
    deterministic assessments, and one advisory review per case.
    """
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "trinetra.narrative-showcase/1":
        raise ValueError("Unsupported narrative showcase seed schema.")
    now = now_ms() if timestamp_ms is None else timestamp_ms
    officers = payload["officers"]
    io = officers[0]
    supervisor = officers[1]
    upsert_officer_profile(session, io, OfficerRole.io)
    upsert_officer_profile(session, supervisor, OfficerRole.supervisor)
    created = 0
    for index, item in enumerate(payload["cases"]):
        case = session.exec(select(Case).where(Case.ack_no == item["ack_no"])).first()
        case_created = case is None
        ts = now - index * 1_800_000
        if case is None:
            case = Case(
                ack_no=item["ack_no"],
                category=item["category"],
                jurisdiction=item["jurisdiction"],
                filed_ts_ms=ts - 3_600_000,
                amount_reported_base=int(item["amount_base"]),
                asset_symbol="USDT",
                asset_decimals=6,
                chain_family="TRON",
                chain_network="mainnet",
                reported_address=item["reported_address"],
                payment_txid=item["payment_txid"],
                payment_ts_ms=ts - 3_000_000,
                complainant_contact_redacted="+91-98XXXXXX77",
                created_ts_ms=ts,
                updated_ts_ms=ts,
            )
            session.add(case)
            session.flush()
            created += 1
        ensure_case_assignment(
            session,
            case,
            assigned_io_pis=str(io["pis"]),
            supervising_acp_pis=str(supervisor["pis"]),
        )
        narrative, assessment, _source_created = ingest_narrative(
            session,
            case_id=int(case.id or 0),
            source_kind="showcase_text",
            source_ref=f"showcase:{item['key']}",
            language=str(payload["language"]),
            original_name=f"showcase-{item['key']}.txt",
            mime_type="text/plain",
            data=str(item["narrative"]).encode("utf-8"),
            provenance=(
                "Versioned local narrative showcase seed. Dummy victim text for "
                "demonstration only."
            ),
            created_by_pis=str(io["pis"]),
            retrieved_ts_ms=ts,
        )
        _record_showcase_review(
            session,
            assessment=assessment,
            accepted_typologies=list(item["accepted_typologies"]),
            primary_typology=str(item["primary_typology"]),
            reviewer_pis=str(io["pis"]),
            note=str(item["reasoning_note"]),
        )
        latest_snapshot = session.exec(
            select(TraceSnapshot)
            .where(TraceSnapshot.case_id == case.id)
            .order_by(TraceSnapshot.id.desc())
        ).first()
        if case_created or latest_snapshot is None:
            get_or_create_trace(session, case, trace_mode="auto")
        session.add(narrative)
    session.commit()
    return created


def _record_showcase_review(
    session: Session,
    *,
    assessment: NarrativeAssessment,
    accepted_typologies: list[str],
    primary_typology: str,
    reviewer_pis: str,
    note: str,
) -> None:
    existing = session.exec(
        select(NarrativeAssessmentReview).where(
            NarrativeAssessmentReview.assessment_id == assessment.id,
            NarrativeAssessmentReview.reviewer_pis == reviewer_pis,
            NarrativeAssessmentReview.primary_typology == primary_typology,
        )
    ).first()
    if existing is not None and existing.accepted_typologies == accepted_typologies:
        return
    record_narrative_review(
        session,
        assessment=assessment,
        accepted_typologies=accepted_typologies,
        primary_typology=primary_typology,
        reviewer_pis=reviewer_pis,
        note=note,
    )
