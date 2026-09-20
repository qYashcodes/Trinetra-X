from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import Session, select

from app.models import (
    Case,
    CaseAssignment,
    CaseEscalationAssignment,
    CaseStage,
    CaseWatcher,
    Dispatch,
    DispatchRecord,
    Finding,
    Notice,
    OfficerProfile,
    OfficerRole,
    TraceSnapshot,
    VaspResponse,
)
from app.services.hash import sha256_json
from app.services.time import now_ms
from app.settings import ROOT_DIR


SEED_PATH = ROOT_DIR / "fixtures" / "workflow_states_v1.json"


def seed_workflow_states(session: Session, *, timestamp_ms: int | None = None) -> int:
    """Idempotently add the versioned workflow demonstration dataset.

    The canonical tracing fixture is neither read nor modified here. Existing
    rows, evidence, artifacts, and audit events are never deleted or reset.
    """
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    now = now_ms() if timestamp_ms is None else timestamp_ms
    for officer in payload["officers"]:
        row = session.exec(select(OfficerProfile).where(OfficerProfile.pis == officer["pis"])).first()
        if row is None:
            session.add(
                OfficerProfile(
                    pis=officer["pis"],
                    name=officer["name"],
                    rank=officer["rank"],
                    unit=officer["unit"],
                    role=OfficerRole(officer["role"]),
                    created_ts_ms=now,
                    updated_ts_ms=now,
                )
            )
    session.flush()
    created = 0
    for index, state in enumerate(payload["states"]):
        existing = session.exec(select(Case).where(Case.ack_no == state["ack_no"])).first()
        if existing is not None:
            continue
        created += 1
        ts = now - index * 3_600_000
        case = Case(
            ack_no=state["ack_no"],
            category="investment_fraud",
            jurisdiction=state["ack_no"].split("/")[2],
            filed_ts_ms=ts - 3_600_000,
            amount_reported_base=int(state["amount_base"]),
            asset_symbol="USDT",
            asset_decimals=6,
            chain_family="TRON",
            chain_network="mainnet",
            reported_address=f"TV3Seed{index:02d}11111111111111111111111111",
            payment_txid=(f"{index + 1:x}" * 64)[:64],
            payment_ts_ms=ts - 3_000_000,
            complainant_contact_redacted="+91-98XXXXXX00",
            stage=CaseStage(state["case_stage"]),
            created_ts_ms=ts,
            updated_ts_ms=ts,
        )
        session.add(case)
        session.flush()
        session.add(
            CaseAssignment(
                case_id=int(case.id or 0),
                assigned_io_pis=state["io"],
                supervising_acp_pis=state["acp"],
                created_ts_ms=ts,
                updated_ts_ms=ts,
            )
        )
        snapshot = None
        if state.get("trace_status"):
            terminal = {
                "kind": state.get("terminal_kind"),
                "note": state.get("terminal_reason") or "Workflow seed evidence boundary.",
                "deposit_address": f"TV3Deposit{index:02d}111111111111111111111111" if state.get("credited_base") is not None else None,
                "amount_credited_base": state.get("credited_base"),
                "custodian_key": state.get("vasp"),
            }
            result = {
                "engine": {"mode": "fixture", "fixture": "workflow_states_v1"},
                "asset": {"symbol": "USDT", "decimals": 6},
                "hops": [],
                "parked": [],
                "terminal": terminal,
                "stats": {"addresses_visited": 0, "transfers_read": 0, "branches_parked": 0},
            }
            result["sha256"] = sha256_json(result)
            snapshot = TraceSnapshot(
                case_id=int(case.id or 0),
                status=state["trace_status"],
                result_json=result,
                sha256=result["sha256"],
                chain_family="TRON",
                chain_network="mainnet",
                asset_symbol="USDT",
                asset_decimals=6,
                started_ts_ms=ts,
                closed_ts_ms=ts + 60_000,
            )
            session.add(snapshot)
            session.flush()
        finding = None
        if snapshot is not None and state.get("credited_base") is not None:
            finding = Finding(
                case_id=int(case.id or 0),
                snapshot_id=int(snapshot.id or 0),
                terminal_kind="vasp_deposit",
                custodian_key=state.get("vasp"),
                deposit_address=snapshot.result_json["terminal"]["deposit_address"],
                amount_credited_base=int(state["credited_base"]),
                created_ts_ms=ts + 60_000,
            )
            session.add(finding)
            session.flush()
        notice = None
        if finding is not None and state.get("notice_status"):
            dispatch_age = state.get("dispatch_age_minutes")
            dispatched = now - int(dispatch_age) * 60_000 if dispatch_age is not None else None
            notice = Notice(
                case_id=int(case.id or 0),
                finding_id=int(finding.id or 0),
                notice_no=f"V3-SEED/{index + 1:02d}",
                status=state["notice_status"],
                tracker_status="dispatched" if dispatched else state.get("tracker_status", "drafted"),
                tracker_last_note=state.get("note"),
                dispatched_ts_ms=dispatched,
                created_by_pis=state["io"],
                created_ts_ms=ts + 120_000,
            )
            session.add(notice)
            session.flush()
            if dispatched is not None:
                session.add(
                    Dispatch(
                        notice_id=int(notice.id or 0),
                        channel="sahyog-simulated",
                        target="SAHYOG specimen route — no external delivery",
                        status="simulated",
                        attempts=1,
                        created_ts_ms=dispatched,
                        updated_ts_ms=dispatched,
                    )
                )
                record = DispatchRecord(
                    case_id=int(case.id or 0),
                    notice_id=int(notice.id or 0),
                    notice_type="freeze",
                    vasp_label=str(state["vasp"]).title(),
                    stage="dispatched",
                    acknowledgement_state="awaiting",
                    dispatched_ts_ms=dispatched,
                    sla_window_minutes=1_440,
                    sla_due_ts_ms=dispatched + 86_400_000,
                    sla_override_source="default_24h",
                    assigned_io_pis=state["io"],
                    supervising_acp_pis=state["acp"],
                    current_owner_pis=state["io"],
                    created_ts_ms=dispatched,
                    updated_ts_ms=dispatched,
                )
                session.add(record)
                session.flush()
                previous_owner = state["io"]
                for level, owner in enumerate(state.get("escalation_chain", []), start=1):
                    session.add(
                        CaseEscalationAssignment(
                            case_id=int(case.id or 0),
                            dispatch_record_id=int(record.id or 0),
                            level=level,
                            previous_owner_pis=previous_owner,
                            new_owner_pis=owner,
                            reason=f"Versioned workflow seed escalation level {level}",
                            assigned_by_pis=state["acp"],
                            created_ts_ms=dispatched + level * 60_000,
                        )
                    )
                    previous_owner = owner
                    record.current_owner_pis = owner
                    record.escalation_level = level
                    record.stage = "escalated"
                if state.get("escalation_chain"):
                    session.add(
                        CaseWatcher(
                            case_id=int(case.id or 0),
                            officer_pis=state["acp"],
                            reason="Original supervising ACP retained after seeded escalation",
                            created_ts_ms=dispatched,
                        )
                    )
                    session.add(record)
            if state.get("response_restrained_base") is not None:
                session.add(
                    VaspResponse(
                        notice_id=int(notice.id or 0),
                        received_ts_ms=now - 60_000,
                        reference=f"SIMULATED-VASP-{index + 1}",
                        kyc_disclosed=False,
                        amount_restrained_base=int(state["response_restrained_base"]),
                        account_reference_hint="simulated fixture response",
                        created_ts_ms=now - 60_000,
                    )
                )
    session.commit()
    return created
