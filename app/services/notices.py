from __future__ import annotations

import json

from app.services.demo import demo_case
from app.services.explainability import methodology_annex
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
        "methodology_annex": methodology_annex(snapshot),
        "copy_to": ["Nodal officer", "Case file"],
    }


def sahyog_manifest(snapshot: dict, notice_no: str) -> dict:
    annex = methodology_annex(snapshot)
    annex_sha256 = sha256_bytes(
        json.dumps(annex, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    manifest = {
        "schema": "trinetra.sahyog_export/1",
        "notice_no": notice_no,
        "snapshot_sha256": snapshot["sha256"],
        "trace_mode": snapshot.get("engine", {}).get("mode"),
        "trace_params": snapshot.get("params"),
        "data_freshness": snapshot.get("data_freshness"),
        "methodology_annex": annex,
        "specimen_only": True,
        "submission_status": "integration_pending",
        "external_submission_performed": False,
        "integration_boundary": {
            "provider": "SAHYOG",
            "approved_schema_configured": False,
            "credentials_configured": False,
            "live_dispatch_enabled": False,
            "reason": "Government portal submission requires approved schemas, provider metadata and credentials.",
        },
        "attachments": [
            {"name": "notice.pdf", "sha256": "integration_pending"},
            {"name": "trace-report.pdf", "sha256": "integration_pending"},
            {"name": "graph-exhibit.svg", "sha256": sha256_bytes(json.dumps(snapshot).encode("utf-8"))},
            {"name": "methodology-annex.json", "sha256": annex_sha256},
        ],
        "submission": "integration_pending",
    }
    return manifest
