from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfReader
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.integrations.complaints.fixture import FixtureComplaintSource
from app.models import (
    Case,
    CaseAssignment,
    Finding,
    Notice,
    NoticeAttachment,
    NoticeVersion,
    TraceSnapshot,
)
from app.services.hash import sha256_json
from app.services.notice_workflow import (
    NoticeWorkflowError,
    add_attachment,
    attest_version,
    ensure_notice_draft,
    generate_version,
    record_verification,
    render_version_pdf,
    update_notice_parameters,
)


def _workflow_rows(session: Session) -> tuple[Notice, dict[str, str]]:
    timestamp = 1_789_337_235_000
    case = Case(
        ack_no="NCRP/2026/MH/V3-WORKFLOW",
        category="investment_fraud",
        jurisdiction="MH",
        filed_ts_ms=timestamp,
        amount_reported_base=3_000_000,
        asset_symbol="USDT",
        asset_decimals=6,
        chain_family="TRON",
        chain_network="mainnet",
        reported_address="TV3WorkflowSeed111111111111111111111",
        payment_txid="b" * 64,
        payment_ts_ms=0,
        created_ts_ms=timestamp,
        updated_ts_ms=timestamp,
    )
    session.add(case)
    session.flush()
    session.add(
        CaseAssignment(
            case_id=int(case.id or 0),
            assigned_io_pis="48421",
            supervising_acp_pis="0912",
            created_ts_ms=timestamp,
            updated_ts_ms=timestamp,
        )
    )
    snapshot_result = {"engine": {"mode": "fixture"}, "hops": [], "terminal": {"kind": "evidence_boundary"}}
    snapshot = TraceSnapshot(
        case_id=int(case.id or 0),
        result_json=snapshot_result,
        sha256=sha256_json(snapshot_result),
        chain_family="TRON",
        chain_network="mainnet",
        asset_symbol="USDT",
        asset_decimals=6,
        started_ts_ms=timestamp,
        closed_ts_ms=timestamp,
    )
    session.add(snapshot)
    session.flush()
    finding = Finding(
        case_id=int(case.id or 0),
        snapshot_id=int(snapshot.id or 0),
        terminal_kind="vasp_deposit",
        custodian_key="coinsphere",
        deposit_address="TV3Deposit1111111111111111111111111",
        amount_credited_base=2_400_000,
        created_ts_ms=timestamp,
    )
    session.add(finding)
    session.flush()
    notice = Notice(
        case_id=int(case.id or 0),
        finding_id=int(finding.id or 0),
        notice_no="MH-CYBER/V3/1",
        created_by_pis="48421",
        created_ts_ms=timestamp,
    )
    session.add(notice)
    session.commit()
    session.refresh(notice)
    return notice, {
        "pis": "48421",
        "name": "A. Kulkarni",
        "rank": "Inspector",
        "role": "io",
    }


def test_notice_versions_are_immutable_and_edits_clear_attestation(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        "app.services.notice_workflow.settings",
        SimpleNamespace(var_dir=tmp_path / "var", legal_copy_approved=False),
    )
    with Session(engine) as session:
        notice, author = _workflow_rows(session)
        draft = ensure_notice_draft(session, notice, author=author)
        document = FixtureComplaintSource().fetch_document(
            "NCRP/2026/MH/0084213", "complaint"
        )
        assert document is not None
        add_attachment(
            session,
            draft,
            slot="complaint",
            source="fixture_complaint_source",
            original_name=document["name"],
            mime_type=document["mime_type"],
            data=document["data"],
            provenance=document["provenance"],
            simulated=True,
            author_pis=author["pis"],
        )
        first = generate_version(session, draft, author_pis=author["pis"])
        first_hash = first.content_sha256
        attest_version(session, draft, officer_pis=author["pis"])
        assert draft.attested_version_no == 1

        changed = dict(draft.parameters)
        changed["duration_hours"] = 48
        update_notice_parameters(session, draft, changed)
        assert draft.dirty is True
        assert draft.attested_version_no is None
        second = generate_version(session, draft, author_pis=author["pis"])
        session.commit()

        assert first.content_sha256 == first_hash
        assert second.version_no == 2
        assert second.parent_version_id == first.id
        assert second.content["parameters"]["duration_hours"] == 48
        assert first.content["parameters"]["duration_hours"] == 24
        assert session.exec(select(NoticeVersion)).all() == [first, second]


def test_verification_lock_survives_reload() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        notice, author = _workflow_rows(session)
        draft = ensure_notice_draft(session, notice, author=author)
        for _ in range(3):
            state = record_verification(
                session,
                draft,
                officer_session_id=91,
                method="cctns",
                success=False,
                fixture_mode=True,
            )
        session.commit()
        assert state.failure_count == 3
        assert state.locked_until_ts_ms is not None

    with Session(engine) as reloaded:
        draft = reloaded.exec(select(type(draft))).first()
        assert draft is not None
        try:
            record_verification(
                reloaded,
                draft,
                officer_session_id=91,
                method="cctns",
                success=False,
                fixture_mode=True,
            )
        except NoticeWorkflowError as exc:
            assert "locked" in str(exc).lower()
        else:
            raise AssertionError("Persisted verification lock was not enforced")


def test_playwright_pdf_is_immutable_and_extractable(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.services.notice_workflow.ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        "app.services.notice_workflow.settings",
        SimpleNamespace(var_dir=tmp_path / "var", legal_copy_approved=False),
    )
    with Session(engine) as session:
        notice, author = _workflow_rows(session)
        draft = ensure_notice_draft(session, notice, author=author)
        document = FixtureComplaintSource().fetch_document(
            "NCRP/2026/MH/0084213", "complaint"
        )
        assert document is not None
        add_attachment(
            session,
            draft,
            slot="complaint",
            source="fixture_complaint_source",
            original_name=document["name"],
            mime_type=document["mime_type"],
            data=document["data"],
            provenance=document["provenance"],
            simulated=True,
            author_pis=author["pis"],
        )
        version = generate_version(session, draft, author_pis=author["pis"])
        artifact = render_version_pdf(
            session,
            notice=notice,
            draft=draft,
            version=version,
            author_pis=author["pis"],
        )
        session.commit()
        path = tmp_path / artifact.storage_ref
        assert path.exists()
        assert artifact.original_name.endswith(f"-{artifact.sha256[:12]}.pdf")
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert len(reader.pages) >= 1
        assert "SPECIMEN" in text
        assert "version 1" in text
        assert session.exec(
            select(NoticeAttachment).where(NoticeAttachment.slot == "generated_notice_pdf")
        ).one().sha256 == artifact.sha256
