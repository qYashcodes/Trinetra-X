from __future__ import annotations

from sqlmodel import Session, select

from app.engine_bridge import TraceParams, TraceSeed, run_trace
from app.models import Case, CaseStage, Finding, Notice, TraceEvent, TraceSnapshot
from app.services.demo import demo_case
from app.services.time import now_ms


def finding_review_specs() -> list[dict]:
    """Return the ordered, explicit review contract used by storage and the UI."""
    return [dict(spec) for spec in demo_case()["finding_review_checks"]]


def initial_finding_review_checks() -> dict[str, bool]:
    return {
        str(spec["key"]): bool(spec.get("default_checked", False))
        for spec in finding_review_specs()
    }


def ensure_finding_review_checks(session: Session, finding: Finding) -> Finding:
    """Migrate legacy positional checks without carrying unrelated assertions forward."""
    expected = initial_finding_review_checks()
    current = dict(finding.review_checks or {})
    if set(current) == set(expected):
        return finding
    finding.review_checks = expected
    session.add(finding)
    session.commit()
    session.refresh(finding)
    return finding


def seed_demo(session: Session) -> Case:
    data = demo_case()
    return case_from_complaint(session, data["case"])


def case_from_complaint(session: Session, record: dict) -> Case:
    existing = session.exec(select(Case).where(Case.ack_no == record["ack_no"])).first()
    if existing:
        return existing
    ts = now_ms()
    asset = record["asset"]
    chain = record["chain"]
    case = Case(
        ack_no=record["ack_no"],
        category=record["category"],
        jurisdiction=record["jurisdiction"],
        filed_ts_ms=record["filed_ts_ms"],
        amount_reported_base=record["amount_reported_base"],
        asset_symbol=asset["symbol"],
        asset_decimals=asset["decimals"],
        chain_family=chain["family"],
        chain_network=chain["network"],
        reported_address=record["reported_address"],
        payment_txid=record.get("payment_txid"),
        payment_ts_ms=record.get("victim_payment_ts_ms"),
        complainant_contact_redacted=record.get("complainant_contact_redacted"),
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def get_or_create_trace(session: Session, case: Case) -> tuple[TraceSnapshot, Finding]:
    existing = session.exec(
        select(TraceSnapshot).where(TraceSnapshot.case_id == case.id).order_by(TraceSnapshot.id.desc())
    ).first()
    if existing:
        finding = session.exec(select(Finding).where(Finding.snapshot_id == existing.id)).first()
        if finding:
            return existing, ensure_finding_review_checks(session, finding)

    result = run_trace(
        TraceSeed(
            address=case.reported_address,
            payment_ts_ms=case.payment_ts_ms,
            amount_base=case.amount_reported_base,
            payment_txid=case.payment_txid,
        ),
        TraceParams(),
    )
    snapshot = TraceSnapshot(
        case_id=case.id,
        version=1,
        result_json=result,
        sha256=result["sha256"],
        chain_family=result["chain"]["family"],
        chain_network=result["chain"]["network"],
        asset_symbol=result["asset"]["symbol"],
        asset_decimals=result["asset"]["decimals"],
        bridge_candidate=(result.get("bridge_candidates") or [None])[0],
        case_link_evidence=result["case_link_evidence"],
        started_ts_ms=result["started_ts"],
        closed_ts_ms=result["closed_ts"],
    )
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)

    for seq, event in enumerate(_events_from_result(result), start=1):
        session.add(
            TraceEvent(
                snapshot_id=snapshot.id,
                seq=seq,
                event_type=event["type"],
                data=event["data"],
                created_ts_ms=now_ms(),
            )
        )
    terminal = result["terminal"]
    review_checks = initial_finding_review_checks()
    finding = Finding(
        case_id=case.id,
        snapshot_id=snapshot.id,
        terminal_kind=terminal["kind"],
        custodian_key=terminal.get("custodian_key"),
        deposit_address=terminal.get("deposit_address"),
        amount_credited_base=terminal.get("amount_credited_base"),
        review_checks=review_checks,
        created_ts_ms=now_ms(),
    )
    case.stage = CaseStage.custody_found
    case.updated_ts_ms = now_ms()
    session.add(finding)
    session.add(case)
    session.commit()
    session.refresh(finding)
    return snapshot, finding


def prepare_notice(session: Session, finding: Finding, created_by_pis: str) -> Notice:
    existing = session.exec(select(Notice).where(Notice.finding_id == finding.id)).first()
    if existing:
        return existing
    data = demo_case()
    notice = Notice(
        case_id=finding.case_id,
        finding_id=finding.id,
        notice_no=data["notice"]["notice_no"],
        deadline_hours=data["notice"]["deadline_hours"],
        created_by_pis=created_by_pis,
        created_ts_ms=now_ms(),
    )
    case = session.get(Case, finding.case_id)
    if case:
        case.stage = CaseStage.notice_draft
        case.updated_ts_ms = now_ms()
        session.add(case)
    session.add(notice)
    session.commit()
    session.refresh(notice)
    return notice


def _events_from_result(result: dict) -> list[dict]:
    events = []
    for hop in result["hops"]:
        events.append({"type": "hop", "data": hop})
    for parked in result["parked"]:
        events.append({"type": "parked", "data": parked})
    for dust in result["dust"]:
        events.append({"type": "dust", "data": dust})
    events.append({"type": "candidate", "data": {"name": "Coinsphere Global Pte Ltd", "band": "high"}})
    events.append({"type": "terminal", "data": result["terminal"]})
    events.append({"type": "done", "data": {"sha256": result["sha256"], "closed_ts": result["closed_ts"]}})
    return events
