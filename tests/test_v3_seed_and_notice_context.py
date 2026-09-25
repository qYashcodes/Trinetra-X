from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Case, CaseAssignment, Notice, OfficerProfile, TraceSnapshot
from app.services.docket import docket_state
from app.services.notices import notice_view_model
from app.services.workflow_seed import seed_workflow_states


def test_workflow_seed_is_idempotent_and_uses_demo_login_identities() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        assert seed_workflow_states(session, timestamp_ms=2_000_000_000_000) == 9
        assert seed_workflow_states(session, timestamp_ms=2_000_000_000_000) == 0

        assert len(session.exec(select(Case)).all()) == 9
        profiles = {row.pis: row for row in session.exec(select(OfficerProfile)).all()}
        assert profiles["74821"].name == "R. Kulkarni"
        assert profiles["61207"].name == "S. Deshmukh"

        visible_seed_assignment = session.exec(
            select(CaseAssignment).where(CaseAssignment.assigned_io_pis == "74821")
        ).first()
        assert visible_seed_assignment is not None

        snapshots = session.exec(select(TraceSnapshot)).all()
        assert snapshots
        assert all(row.result_json["sha256"] == row.sha256 for row in snapshots)


def test_seeded_notice_view_uses_its_own_case_and_snapshot_facts() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_workflow_states(session, timestamp_ms=2_000_000_000_000)
        notice = session.exec(select(Notice).order_by(Notice.id)).first()
        assert notice is not None
        case = session.get(Case, notice.case_id)
        snapshot = session.exec(
            select(TraceSnapshot).where(TraceSnapshot.case_id == notice.case_id)
        ).first()
        assert case is not None and snapshot is not None

        model = notice_view_model(case.model_dump(), snapshot.result_json, notice.model_dump())
        assert model["case"]["ack_no"] == case.ack_no
        assert model["particulars"][0]["value"] == case.reported_address
        assert model["particulars"][1]["value"] == case.payment_txid
        assert model["particulars"][4]["value"] == snapshot.sha256
        assert model["addressee"] == str(snapshot.result_json["terminal"]["custodian_key"]).title()


def test_acp_docket_aggregates_cases_across_seeded_officers() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_workflow_states(session, timestamp_ms=2_000_000_000_000)

        io_docket = docket_state(
            session,
            officer_pis="74821",
            role="io",
            active_case_id=None,
            timestamp_ms=2_000_000_000_000,
        )
        acp_docket = docket_state(
            session,
            officer_pis="61207",
            role="supervisor",
            active_case_id=None,
            timestamp_ms=2_000_000_000_000,
        )

        io_refs = {row["ack_no"] for row in io_docket["rows"]}
        acp_refs = {row["ack_no"] for row in acp_docket["rows"]}
        acp_io_names = {item["io_name"] for item in acp_docket["supervisor_cases"]}

        assert "NCRP/2026/KA/V3-1003" not in io_refs
        assert "NCRP/2026/KA/V3-1003" in acp_refs
        assert "NCRP/2026/DL/V3-1005" in acp_refs
        assert {"Inspector R. Kulkarni", "PSI A. Patil", "SI M. Shaikh"}.issubset(
            acp_io_names
        )
        assert len(acp_refs) > len(io_refs)
        assert acp_docket["kpis"][0]["detail"] == "supervisory queue"
