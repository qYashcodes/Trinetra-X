from __future__ import annotations

import json

from app.services.demo import demo_case
from app.services.hash import sha256_bytes


def notice_view_model(case: dict, snapshot: dict, notice: dict | None = None) -> dict:
    data = demo_case()
    terminal = snapshot["terminal"]
    return {
        "letterhead": {
            "govt_line": "GOVERNMENT OF MAHARASHTRA - POLICE DEPARTMENT",
            "office": data["office"]["name"],
            "address": data["office"]["address"],
            "email": data["office"]["email"],
        },
        "notice_no": (notice or {}).get("notice_no", data["notice"]["notice_no"]),
        "date_ist": "2026-08-30 09:41:00 IST",
        "addressee": data["entity"]["name"],
        "case": {"ack_no": data["case"]["ack_no"]},
        "legal_basis": data["notice"]["legal_basis"],
        "particulars": [
            {"label": "Reported address", "value": data["case"]["reported_address"]},
            {"label": "Payment transaction hash", "value": data["case"]["payment_txid"]},
            {"label": "Deposit address", "value": terminal.get("deposit_address")},
            {"label": "Amount credited", "value": "17,880.00 USDT"},
            {"label": "Snapshot SHA-256", "value": snapshot["sha256"]},
        ],
        "path_summary": [
            {
                "hop": hop["hop"],
                "address": hop["address"],
                "amount": hop["value_base"],
                "txids": hop["txids"],
            }
            for hop in snapshot["hops"]
        ],
        "deadline_hours": data["notice"]["deadline_hours"],
        "requests": [
            "Restrain the credited crypto-assets or equivalent balance pending lawful process.",
            "Disclose KYC and account particulars linked to the deposit address.",
            "Preserve login, device, IP, and withdrawal logs relevant to the credited account.",
        ],
        "signature": data["officers"]["io"],
        "countersignature": None,
        "enclosures": ["Trace report", "Graph exhibit", "Complaint extract", "Hash manifest"],
        "copy_to": ["Nodal officer", "Case file"],
    }


def sahyog_manifest(snapshot: dict, notice_no: str) -> dict:
    manifest = {
        "schema": "trinetra.sahyog_export/1",
        "notice_no": notice_no,
        "snapshot_sha256": snapshot["sha256"],
        "attachments": [
            {"name": "notice.pdf", "sha256": "integration_pending"},
            {"name": "trace-report.pdf", "sha256": "integration_pending"},
            {"name": "graph-exhibit.svg", "sha256": sha256_bytes(json.dumps(snapshot).encode("utf-8"))},
        ],
        "submission": "integration_pending",
    }
    return manifest
