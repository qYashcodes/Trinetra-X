from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlmodel import Session, select

from app.models import (
    Case,
    CaseAssignment,
    CaseStage,
    DispatchRecord,
    Finding,
    Notice,
    OfficerProfile,
    TraceSnapshot,
    VaspResponse,
)
from app.services.officers import visible_cases
from app.services.time import now_ms


ACTIVE_TRACE_STATUSES = {"queued", "running", "paused", "provider_backoff"}
NON_CUSTODY_TERMINALS = {
    "provider_error", "evidence_boundary", "invalid_seed", "unsupported_chain",
    "wrong_asset", "wrong_chain", "gate_disabled", "no_custody",
}


def docket_state(
    session: Session,
    *,
    officer_pis: str,
    role: str,
    active_case_id: int | None,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    now = now_ms() if timestamp_ms is None else timestamp_ms
    cases = visible_cases(session, officer_pis=officer_pis, role=role)
    snapshots = _latest_by_case(session.exec(select(TraceSnapshot).order_by(TraceSnapshot.id)).all())
    findings = _latest_by_case(session.exec(select(Finding).order_by(Finding.id)).all())
    notices = _latest_by_case(session.exec(select(Notice).order_by(Notice.id)).all())
    records = {row.case_id: row for row in session.exec(select(DispatchRecord).order_by(DispatchRecord.id)).all()}
    responses_by_notice: dict[int, list[VaspResponse]] = defaultdict(list)
    for response in session.exec(select(VaspResponse).order_by(VaspResponse.id)).all():
        responses_by_notice[response.notice_id].append(response)
    assignments = {row.case_id: row for row in session.exec(select(CaseAssignment)).all()}
    profiles = {row.pis: row for row in session.exec(select(OfficerProfile)).all()}

    rows: list[dict[str, Any]] = []
    for case in cases:
        case_id = int(case.id or 0)
        snapshot = snapshots.get(case_id)
        finding = findings.get(case_id)
        notice = notices.get(case_id)
        record = records.get(case_id)
        responses = responses_by_notice.get(int(notice.id or 0), []) if notice else []
        restrained = sum(int(item.amount_restrained_base) for item in responses)
        terminal_kind = str(snapshot.result_json.get("terminal", {}).get("kind") or "") if snapshot else ""
        traced_to = _traced_label(snapshot, finding)
        stage = _stage_label(case, snapshot, notice, record, now)
        status_tone = _tone(case, snapshot, notice, record, now)
        linked = 0
        row = {
            "case_id": case_id,
            "snapshot_id": snapshot.id if snapshot else None,
            "ack_no": case.ack_no,
            "amount_base": case.amount_reported_base,
            "traced_to": traced_to,
            "traced_tone": "strong" if finding else "muted",
            "recovered_base": restrained,
            "recovered_label": "recorded restrained" if responses else "—",
            "progress": min(100, restrained * 100 // case.amount_reported_base) if case.amount_reported_base > 0 else 0,
            "progress_tone": "success" if restrained > 0 else "muted",
            "stage": stage,
            "linked": linked,
            "status_tone": status_tone,
            "status_label": _status_label(status_tone),
            "age": _age_label(now - case.updated_ts_ms),
            "age_rank": max(0, (now - case.updated_ts_ms) // 3_600_000),
            "active": case_id == active_case_id,
            "terminal_kind": terminal_kind,
        }
        row["filter_groups"] = _filter_groups(row, case, snapshot, notice, record, now)
        row["search_text"] = " ".join(
            str(value or "") for value in (case.ack_no, traced_to, stage, record.current_owner_pis if record else "")
        ).lower()
        rows.append(row)

    filter_specs = [
        ("all", "All cases", "muted"),
        ("trace_running", "Trace running", "muted"),
        ("notice_out", "Notice out", "warning"),
        ("restraint_confirmed", "Restraint confirmed", "success"),
        ("needs_attention", "Needs attention", "danger"),
        ("closed_no_custody", "Closed / no custody", "muted"),
        ("linked_cases", "Linked cases", "warning"),
    ]
    counts = Counter(group for row in rows for group in row["filter_groups"])
    open_cases = sum(case.stage != CaseStage.closed_no_custody for case in cases)
    active_trace_case_ids = {
        case_id for case_id, snapshot in snapshots.items() if snapshot.status in ACTIVE_TRACE_STATUSES
    }
    value_under_trace = sum(
        case.amount_reported_base for case in cases if int(case.id or 0) in active_trace_case_ids
    )
    restrained_total = sum(
        int(item.amount_restrained_base)
        for items in responses_by_notice.values()
        for item in items
        if item.amount_restrained_base is not None
    )
    awaiting_records = [
        item
        for item in records.values()
        if item.dispatched_ts_ms is not None
        and item.acknowledgement_state == "awaiting"
        and item.closed_ts_ms is None
    ]
    overdue = sum(item.sla_due_ts_ms is not None and now > item.sla_due_ts_ms for item in awaiting_records)
    non_custody = sum(
        case.stage == CaseStage.closed_no_custody
        or (snapshots.get(int(case.id or 0)) is not None and str(snapshots[int(case.id or 0)].result_json.get("terminal", {}).get("kind") or "") in NON_CUSTODY_TERMINALS)
        for case in cases
    )
    supervisor_cases = []
    for row in rows:
        assignment = assignments.get(row["case_id"])
        io = profiles.get(assignment.assigned_io_pis) if assignment else None
        escalated = "needs_attention" in row["filter_groups"]
        supervisor_cases.append(
            {
                "ack_no": row["ack_no"],
                "io_name": f"{io.rank} {io.name}" if io else "Unassigned IO",
                "stage": row["stage"],
                "age": row["age"],
                "tone": "danger" if escalated else row["status_tone"],
                "status": "Escalated" if escalated else row["status_label"],
                "escalated": escalated,
            }
        )
    return {
        "kpis": [
            {"label": "Open dockets", "value": open_cases, "unit": "cases", "detail": "visible assigned cases", "tone": "default"},
            {"label": "Value under trace", "amount_base": value_under_trace, "unit": "M USDT", "detail": f"across {len(active_trace_case_ids)} active traces", "tone": "default"},
            {"label": "Restrained to date", "amount_base": restrained_total, "unit": "M USDT", "detail": "explicit recorded VASP responses only", "tone": "success"},
            {"label": "Awaiting custodian", "value": len(awaiting_records), "unit": "notices", "detail": f"{overdue} past the acknowledgement SLA", "tone": "warning"},
            {"label": "No custody found", "value": non_custody, "unit": "cases", "detail": "explicit non-custody terminals", "tone": "muted"},
        ],
        "filters": [
            {"key": key, "label": label, "count": counts.get(key, 0), "tone": tone, "active": key == "all"}
            for key, label, tone in filter_specs
        ],
        "rows": rows,
        "supervisor_cases": supervisor_cases,
        "supervisor_escalated_count": sum(item["escalated"] for item in supervisor_cases),
        "total_count": len(rows),
        "attention_count": counts.get("needs_attention", 0),
    }


def _latest_by_case(rows: list[Any]) -> dict[int, Any]:
    result: dict[int, Any] = {}
    for row in rows:
        result[row.case_id] = row
    return result


def _traced_label(snapshot: TraceSnapshot | None, finding: Finding | None) -> str:
    if finding is not None and finding.custodian_key:
        return str(finding.custodian_key).replace("_", " ").title() + " (VASP label)"
    if snapshot is None:
        return "Trace not started"
    if snapshot.status in ACTIVE_TRACE_STATUSES:
        return "Trace running"
    terminal = snapshot.result_json.get("terminal", {})
    return str(terminal.get("note") or terminal.get("kind") or "Evidence boundary")


def _stage_label(case: Case, snapshot: TraceSnapshot | None, notice: Notice | None, record: DispatchRecord | None, now: int) -> str:
    if record and record.sla_due_ts_ms is not None and now > record.sla_due_ts_ms and record.acknowledgement_state == "awaiting":
        return "Notice overdue, awaiting custodian"
    if record and record.escalation_level:
        return f"Escalated · level {record.escalation_level}"
    if notice is not None:
        return notice.status.replace("_", " ").title()
    if snapshot is not None and snapshot.status == "failed":
        return "Trace failed — explicit non-custody terminal"
    return case.stage.value.replace("_", " ").title()


def _tone(case: Case, snapshot: TraceSnapshot | None, notice: Notice | None, record: DispatchRecord | None, now: int) -> str:
    if snapshot is not None and snapshot.status == "failed":
        return "danger"
    if record and ((record.sla_due_ts_ms is not None and now > record.sla_due_ts_ms) or record.escalation_level):
        return "danger"
    if case.stage == CaseStage.restraint_confirmed:
        return "success"
    if notice is not None:
        return "warning"
    return "muted"


def _filter_groups(row: dict[str, Any], case: Case, snapshot: TraceSnapshot | None, notice: Notice | None, record: DispatchRecord | None, now: int) -> list[str]:
    groups = ["all"]
    if snapshot is not None and snapshot.status in ACTIVE_TRACE_STATUSES:
        groups.append("trace_running")
    if notice is not None and notice.status in {"dispatched", "countersigned", "awaiting_countersignature"}:
        groups.append("notice_out")
    if case.stage == CaseStage.restraint_confirmed:
        groups.append("restraint_confirmed")
    if row["status_tone"] == "danger":
        groups.append("needs_attention")
    if case.stage == CaseStage.closed_no_custody or row["terminal_kind"] in NON_CUSTODY_TERMINALS:
        groups.append("closed_no_custody")
    if row["linked"]:
        groups.append("linked_cases")
    return groups


def _age_label(delta_ms: int) -> str:
    hours = max(0, delta_ms // 3_600_000)
    if hours < 1:
        return "now"
    if hours < 24:
        return f"{hours} h"
    return f"{hours // 24} d"


def _status_label(tone: str) -> str:
    return {"danger": "Attention", "warning": "Pending", "success": "Recorded", "muted": "Watch"}.get(tone, "Open")
