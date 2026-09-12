from __future__ import annotations

from typing import Any


def docket_fixture() -> dict[str, Any]:
    """Deterministic docket presentation data for the offline prototype."""

    return {
        "kpis": [
            {"label": "Open dockets", "value": 37, "unit": "cases", "detail": "6 filed this week", "tone": "default"},
            {"label": "Value under trace", "amount_base": 2_410_000_000_000, "unit": "M USDT", "detail": "across 12 active traces", "tone": "default"},
            {"label": "Restrained to date", "amount_base": 1_060_000_000_000, "unit": "M USDT", "detail": "19 notices honoured", "tone": "success"},
            {"label": "Awaiting custodian", "value": 5, "unit": "notices", "detail": "2 past the notice period", "tone": "warning"},
            {"label": "No custody found", "value": 9, "unit": "cases", "detail": "bridged or self-custody", "tone": "muted"},
        ],
        "filters": [
            {"label": "All cases", "count": 8, "tone": "muted", "active": True},
            {"label": "Trace running", "count": 1, "tone": "muted"},
            {"label": "Notice out", "count": 2, "tone": "warning"},
            {"label": "Restraint confirmed", "count": 2, "tone": "success"},
            {"label": "Needs attention", "count": 2, "tone": "danger"},
            {"label": "Closed / no custody", "count": 2, "tone": "muted"},
            {"label": "Linked cases", "count": 4, "tone": "warning"},
        ],
        "rows": [
            {"ack_no": "NCRP/2026/MH/0084213", "amount_base": 21_940_000_000, "traced_to": "Coinsphere Global (VASP)", "traced_tone": "strong", "recovered_base": 17_880_000_000, "recovered_label": "held", "progress": 81, "progress_tone": "warning", "stage": "Freeze notice drafted", "linked": 3, "status_tone": "warning", "age": "4 h"},
            {"ack_no": "NCRP/2026/MH/0084108", "amount_base": 8_150_000_000, "traced_to": "Tracing, hop 3 of 5", "traced_tone": "muted", "recovered_label": "—", "stage": "Trace running", "status_tone": "muted", "age": "9 h"},
            {"ack_no": "NCRP/2026/KA/0079922", "amount_base": 46_000_000_000, "traced_to": "Northbridge Exchange", "traced_tone": "strong", "recovered_base": 39_200_000_000, "recovered_label": "held", "progress": 85, "progress_tone": "warning", "stage": "Notice overdue, awaiting custodian", "linked": 1, "status_tone": "warning", "age": "1 d"},
            {"ack_no": "NCRP/2026/DL/0079481", "amount_base": 3_275_000_000, "traced_to": "Unattributed cluster K-88", "traced_tone": "muted", "recovered_base": 3_275_000_000, "recovered_label": "unheld", "progress": 0, "progress_tone": "muted", "stage": "Attribution pending — Trace stalled", "status_tone": "danger", "age": "1 d", "age_tone": "danger"},
            {"ack_no": "NCRP/2026/MH/0081902", "amount_base": 112_400_000_000, "traced_to": "Coinsphere Global (VASP)", "traced_tone": "strong", "recovered_base": 104_900_000_000, "recovered_label": "frozen", "progress": 93, "progress_tone": "success", "stage": "Restraint confirmed", "linked": 3, "status_tone": "success", "age": "3 d"},
            {"ack_no": "NCRP/2026/MH/0079411", "amount_base": 15_600_000_000, "traced_to": "Bridged to Ethereum", "traced_tone": "muted", "recovered_base": 0, "recovered_label": "recovered", "progress": 0, "progress_tone": "muted", "stage": "Closed, no custody", "linked": 3, "status_tone": "muted", "age": "6 d"},
            {"ack_no": "NCRP/2026/GJ/0078220", "amount_base": 27_050_000_000, "traced_to": "Vaultpay (VASP)", "traced_tone": "strong", "recovered_base": 22_700_000_000, "recovered_label": "frozen", "progress": 84, "progress_tone": "success", "stage": "Restraint confirmed", "status_tone": "success", "age": "8 d"},
            {"ack_no": "NCRP/2026/MH/0077104", "amount_base": 5_480_000_000, "traced_to": "Self-custody remainder", "traced_tone": "muted", "recovered_base": 0, "recovered_label": "recovered", "progress": 0, "progress_tone": "muted", "stage": "Closed, no custody", "status_tone": "muted", "age": "11 d"},
        ],
    }
