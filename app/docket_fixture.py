from __future__ import annotations

from typing import Any


def docket_fixture(active_case: Any | None = None) -> dict[str, Any]:
    """Deterministic docket presentation data for the offline prototype."""
    active_ack = getattr(active_case, "ack_no", None)

    rows = [
        {"ack_no": "NCRP/2026/MH/0084213", "amount_base": 21_940_000_000, "traced_to": "Coinsphere Global (VASP)", "traced_tone": "strong", "recovered_base": 17_880_000_000, "recovered_label": "held", "progress": 81, "progress_tone": "warning", "stage": "Freeze notice drafted", "linked": 3, "status_tone": "warning", "age": "4 h"},
        {"ack_no": "NCRP/2026/MH/0084108", "amount_base": 8_150_000_000, "traced_to": "Trace running", "traced_tone": "muted", "recovered_label": "—", "stage": "Trace running", "status_tone": "muted", "age": "9 h"},
        {"ack_no": "NCRP/2026/KA/0079922", "amount_base": 46_000_000_000, "traced_to": "Northbridge Exchange", "traced_tone": "strong", "recovered_base": 39_200_000_000, "recovered_label": "held", "progress": 85, "progress_tone": "warning", "stage": "Notice overdue, awaiting custodian", "linked": 1, "status_tone": "warning", "age": "1 d"},
        {"ack_no": "NCRP/2026/DL/0079481", "amount_base": 3_275_000_000, "traced_to": "Unattributed cluster K-88", "traced_tone": "muted", "recovered_base": 3_275_000_000, "recovered_label": "unheld", "progress": 0, "progress_tone": "muted", "stage": "Attribution pending - Trace stalled", "status_tone": "danger", "age": "1 d", "age_tone": "danger"},
        {"ack_no": "NCRP/2026/MH/0081902", "amount_base": 112_400_000_000, "traced_to": "Coinsphere Global (VASP)", "traced_tone": "strong", "recovered_base": 104_900_000_000, "recovered_label": "frozen", "progress": 93, "progress_tone": "success", "stage": "Restraint confirmed", "linked": 3, "status_tone": "success", "age": "3 d"},
        {"ack_no": "NCRP/2026/MH/0079411", "amount_base": 15_600_000_000, "traced_to": "Bridged to Ethereum", "traced_tone": "muted", "recovered_base": 0, "recovered_label": "recovered", "progress": 0, "progress_tone": "muted", "stage": "Closed, no custody", "linked": 3, "status_tone": "muted", "age": "6 d"},
        {"ack_no": "NCRP/2026/GJ/0078220", "amount_base": 27_050_000_000, "traced_to": "Vaultpay (VASP)", "traced_tone": "strong", "recovered_base": 22_700_000_000, "recovered_label": "frozen", "progress": 84, "progress_tone": "success", "stage": "Restraint confirmed", "status_tone": "success", "age": "8 d"},
        {"ack_no": "NCRP/2026/MH/0077104", "amount_base": 5_480_000_000, "traced_to": "Self-custody remainder", "traced_tone": "muted", "recovered_base": 0, "recovered_label": "recovered", "progress": 0, "progress_tone": "muted", "stage": "Closed, no custody", "status_tone": "muted", "age": "11 d"},
    ]
    if active_case and active_ack and active_ack not in {row["ack_no"] for row in rows}:
        rows.insert(
            0,
            {
                "ack_no": active_case.ack_no,
                "amount_base": active_case.amount_reported_base,
                "traced_to": "Coinsphere Global (VASP)",
                "traced_tone": "strong",
                "recovered_base": 17_880_000_000,
                "recovered_label": "held",
                "progress": 81,
                "progress_tone": "warning",
                "stage": "Active working case",
                "linked": 3,
                "status_tone": "warning",
                "age": "now",
            },
        )
    io_roster = [
        "Insp. R. Kulkarni",
        "PSI A. Patil",
        "SI M. Shaikh",
        "PSI N. More",
    ]
    supervisor_cases = []
    for index, row in enumerate(rows):
        row["active"] = row["ack_no"] == active_ack
        row["filter_groups"] = _filter_groups(row)
        row["search_text"] = " ".join(
            str(row.get(key) or "")
            for key in ("ack_no", "traced_to", "stage", "recovered_label", "age")
        ).lower()
        row["age_rank"] = _age_rank(str(row.get("age") or ""))
        row["status_label"] = _status_label(str(row.get("status_tone") or "muted"))
        escalated = "needs_attention" in row["filter_groups"]
        supervisor_cases.append(
            {
                "ack_no": row["ack_no"],
                "io_name": io_roster[index % len(io_roster)],
                "stage": row["stage"],
                "age": row["age"],
                "tone": "danger" if escalated else row["status_tone"],
                "status": "Escalated" if escalated else row["status_label"],
                "escalated": escalated,
            }
        )

    return {
        "kpis": [
            {"label": "Open dockets", "value": 37, "unit": "cases", "detail": "6 filed this week", "tone": "default"},
            {"label": "Value under trace", "amount_base": 2_410_000_000_000, "unit": "M USDT", "detail": "across 12 active traces", "tone": "default"},
            {"label": "Restrained to date", "amount_base": 1_060_000_000_000, "unit": "M USDT", "detail": "19 notices honoured", "tone": "success"},
            {"label": "Awaiting custodian", "value": 5, "unit": "notices", "detail": "2 past the notice period", "tone": "warning"},
            {"label": "No custody found", "value": 9, "unit": "cases", "detail": "bridged or self-custody", "tone": "muted"},
        ],
        "filters": [
            {"key": "all", "label": "All cases", "count": 8, "tone": "muted", "active": True},
            {"key": "trace_running", "label": "Trace running", "count": 1, "tone": "muted"},
            {"key": "notice_out", "label": "Notice out", "count": 2, "tone": "warning"},
            {"key": "restraint_confirmed", "label": "Restraint confirmed", "count": 2, "tone": "success"},
            {"key": "needs_attention", "label": "Needs attention", "count": 2, "tone": "danger"},
            {"key": "closed_no_custody", "label": "Closed / no custody", "count": 2, "tone": "muted"},
            {"key": "linked_cases", "label": "Linked cases", "count": 4, "tone": "warning"},
        ],
        "rows": rows,
        "supervisor_cases": supervisor_cases,
        "supervisor_escalated_count": sum(1 for row in supervisor_cases if row["escalated"]),
        "total_count": 37,
        "attention_count": 2,
    }


def _filter_groups(row: dict[str, Any]) -> list[str]:
    stage = str(row.get("stage") or "").lower()
    groups = ["all"]
    if "trace running" in stage:
        groups.append("trace_running")
    if "notice" in stage or "freeze notice" in stage:
        groups.append("notice_out")
    if "restraint confirmed" in stage:
        groups.append("restraint_confirmed")
    if row.get("status_tone") == "danger" or "overdue" in stage or "stalled" in stage:
        groups.append("needs_attention")
    if "closed" in stage or "no custody" in stage:
        groups.append("closed_no_custody")
    if row.get("linked"):
        groups.append("linked_cases")
    return groups


def _age_rank(age: str) -> int:
    parts = age.strip().lower().split()
    if not parts or parts[0] == "now":
        return 0
    try:
        amount = int(parts[0])
    except ValueError:
        return 0
    unit = parts[1] if len(parts) > 1 else "h"
    return amount * (24 if unit.startswith("d") else 1)


def _status_label(tone: str) -> str:
    return {
        "danger": "Attention",
        "warning": "Pending",
        "success": "Confirmed",
        "muted": "Watch",
    }.get(tone, "Open")
