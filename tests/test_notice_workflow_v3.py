from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.integrations.complaints.fixture import FixtureComplaintSource
from app.main import save_notice_workflow_parameters, upload_notice_workflow_attachment
from app.models import (
    AuditOutbox,
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
    MAX_ATTACHMENT_BYTES,
    NoticeWorkflowError,
    active_attachments,
    add_attachment,
    attest_version,
    ensure_notice_draft,
    generate_version,
    import_report_attachments,
    record_verification,
    render_version_pdf,
    update_notice_parameters,
    validate_attachment,
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
        changed["service_channels"] = ["portal", "sahyog"]
        update_notice_parameters(session, draft, changed)
        assert draft.dirty is True
        assert draft.attested_version_no is None
        second = generate_version(session, draft, author_pis=author["pis"])
        session.commit()

        assert first.content_sha256 == first_hash
        assert second.version_no == 2
        assert second.parent_version_id == first.id
        assert second.content["parameters"]["duration_hours"] == 48
        assert second.content["parameters"]["service_channels"] == ["portal", "sahyog"]
        assert 'class="notice-paper"' in second.rendered_html
        assert "OFFICE OF THE CYBER CRIME CELL" in second.rendered_html
        assert "Trace methodology annex" in second.rendered_html
        assert "SAHYOG specimen route" in second.rendered_html
        assert first.content["parameters"]["duration_hours"] == 24
        assert session.exec(select(NoticeVersion)).all() == [first, second]


def test_report_number_import_attaches_portal_bundle_once(tmp_path: Path, monkeypatch) -> None:
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
        first = import_report_attachments(
            session,
            draft,
            ack_no="NCRP/2026/MH/0084213",
            complaint_source=FixtureComplaintSource(),
            author_pis=author["pis"],
        )
        second = import_report_attachments(
            session,
            draft,
            ack_no="NCRP/2026/MH/0084213",
            complaint_source=FixtureComplaintSource(),
            author_pis=author["pis"],
        )
        session.commit()

        assert [item.slot for item in first] == ["complaint", "fir", "portal_uploads"]
        assert second == []
        assert [item.slot for item in active_attachments(session, int(draft.id or 0))] == [
            "complaint",
            "fir",
            "portal_uploads",
        ]
        assert all((tmp_path / "var" / item.storage_ref).exists() for item in first)


def test_parameter_save_only_accepts_allowed_editable_fields(tmp_path: Path, monkeypatch) -> None:
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

    class MultiForm(dict):
        def getlist(self, key: str) -> list[str]:
            value = self.get(key, [])
            return value if isinstance(value, list) else [str(value)]

    class ParameterRequest:
        session = {"csrf_token": "parameter-token"}

        async def form(self) -> MultiForm:
            return MultiForm(
                {
                    "csrf_token": "parameter-token",
                    "transaction_hashes": "tampered-tx",
                    "amount_base": "999999999",
                    "duration_hours": "36",
                    "vasp_name": "Tampered Custodian",
                    "vasp_contact": "freeze-requests@example.test",
                    "jurisdiction": "Tampered Jurisdiction",
                    "officer_pis": "00000",
                    "officer_name": "Tampered Officer",
                    "officer_rank": "Tampered Rank",
                    "supervisor_pis": "",
                    "supervisor_name": "Tampered Supervisor",
                    "statutory_key": "generic_restraint",
                    "service_channels": ["portal", "sahyog"],
                    "annex_enabled": "on",
                }
            )

    try:
        with Session(engine) as session:
            notice, author = _workflow_rows(session)
            draft = ensure_notice_draft(session, notice, author=author)
            blanked = dict(draft.parameters)
            blanked["supervisor_pis"] = ""
            blanked["supervisor_name"] = ""
            draft.parameters = blanked
            session.add(draft)
            session.commit()
            original = dict(draft.parameters)

            response = asyncio.run(
                save_notice_workflow_parameters(
                    notice_id=int(notice.id or 0),
                    request=ParameterRequest(),
                    session=session,
                    user=author,
                )
            )

            assert response.status_code == 303
            assert response.headers["location"] == f"/notices/{notice.id}/workflow#stage-attachments"
            session.refresh(draft)
            assert draft.stage == "attachments"
            assert draft.annex_enabled is True
            assert draft.parameters["duration_hours"] == 36
            assert draft.parameters["vasp_contact"] == "freeze-requests@example.test"
            assert draft.parameters["service_channels"] == ["portal", "sahyog"]
            assert draft.parameters["transaction_hashes"] == original["transaction_hashes"]
            assert draft.parameters["amount_base"] == original["amount_base"]
            assert draft.parameters["vasp_name"] == original["vasp_name"]
            assert draft.parameters["jurisdiction"] == original["jurisdiction"]
            assert draft.parameters["officer_pis"] == original["officer_pis"]
            assert draft.parameters["supervisor_pis"] == "0912"
            assert draft.parameters["supervisor_name"] == "ACP S. Deshmukh"
            assert draft.parameters["statutory_key"] == original["statutory_key"]
    finally:
        engine.dispose()


def test_attachment_size_boundary_and_route_read_limit(tmp_path: Path, monkeypatch) -> None:
    exact_limit_pdf = b"%PDF-" + (b"x" * (MAX_ATTACHMENT_BYTES - 5))
    assert validate_attachment(
        name="boundary.pdf",
        mime_type="application/pdf",
        data=exact_limit_pdf,
        slot="complaint",
    ) == ("boundary.pdf", "application/pdf")
    with pytest.raises(NoticeWorkflowError, match="10 MB or smaller"):
        validate_attachment(
            name="oversized.pdf",
            mime_type="application/pdf",
            data=exact_limit_pdf + b"x",
            slot="complaint",
        )

    class OversizedUpload:
        filename = "oversized.pdf"
        content_type = "application/pdf"

        def __init__(self) -> None:
            self.read_sizes: list[int] = []

        async def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return b"%PDF-" + (b"x" * (size - 5))

    upload = OversizedUpload()

    class AttachmentRequest:
        session = {"csrf_token": "attachment-token"}

        async def form(self) -> dict[str, object]:
            return {
                "csrf_token": "attachment-token",
                "file": upload,
                "slot": "complaint",
            }

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
    try:
        with Session(engine) as session:
            notice, author = _workflow_rows(session)
            draft = ensure_notice_draft(session, notice, author=author)
            before = draft.model_dump()

            with pytest.raises(HTTPException) as caught:
                asyncio.run(
                    upload_notice_workflow_attachment(
                        notice_id=int(notice.id or 0),
                        request=AttachmentRequest(),
                        session=session,
                        user=author,
                    )
                )

            assert caught.value.status_code == 422
            assert caught.value.detail == "Each attachment must be 10 MB or smaller."
            assert upload.read_sizes == [MAX_ATTACHMENT_BYTES + 1]
            session.refresh(draft)
            assert draft.model_dump() == before
            assert session.exec(select(NoticeAttachment)).all() == []
            assert session.exec(select(NoticeVersion)).all() == []
            assert session.exec(select(AuditOutbox)).all() == []
    finally:
        engine.dispose()


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
        assert "OFFICE OF THE CYBER CRIME CELL" in text
        assert "Trace methodology annex" in text
        assert session.exec(
            select(NoticeAttachment).where(NoticeAttachment.slot == "generated_notice_pdf")
        ).one().sha256 == artifact.sha256
