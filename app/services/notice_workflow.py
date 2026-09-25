from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlmodel import Session, select

from app.models import (
    Case,
    CaseAssignment,
    Finding,
    Notice,
    NoticeAttachment,
    NoticeDraft,
    NoticeVerificationState,
    NoticeVersion,
    OfficerProfile,
    TraceSnapshot,
)
from app.services.demo import demo_case
from app.services.hash import sha256_bytes, sha256_json
from app.services.money import format_amount
from app.services.time import format_ist, now_ms
from app.settings import ROOT_DIR, settings


ATTACHMENT_SLOTS = {
    "complaint",
    "fir",
    "portal_uploads",
    "graph",
    "custody_summary",
    "other",
    "methodology_annex",
}
ALLOWED_MIME = {"application/pdf", "image/png", "image/jpeg"}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 10
STATUTORY_OPTIONS = {
    "bnss_106": "BNSS section 106 — specimen label pending legal review",
    "generic_preservation": "Preservation request — specimen label pending legal review",
    "generic_restraint": "Restraint request — specimen label pending legal review",
}
SERVICE_CHANNEL_OPTIONS = {
    "portal": "Law-enforcement portal",
    "email": "Compliance desk email",
    "nodal-copy": "State nodal officer copy",
    "sahyog": "SAHYOG specimen route",
}
WORKFLOW_STAGES = (
    "parameters",
    "attachments",
    "review",
    "verification",
    "routing",
)


class NoticeWorkflowError(ValueError):
    pass


def _document_fact_parameters(
    *,
    case: Case,
    finding: Finding,
    snapshot: TraceSnapshot | None,
    notice: Notice,
    entity: dict[str, Any],
) -> dict[str, Any]:
    demo = demo_case()
    dominant_path = demo.get("dominant_path") or []
    terminal_hop = dominant_path[-1] if dominant_path else {}
    trace_mode = (
        snapshot.result_json.get("engine", {}).get("mode", "fixture")
        if snapshot is not None
        else "fixture"
    )
    fixture_terminal = demo.get("terminal", {})
    fixture_like = (
        trace_mode == "fixture"
        and finding.deposit_address == fixture_terminal.get("deposit_address")
    )
    return {
        "filed_ts_ms": case.filed_ts_ms,
        "amount_reported_base": case.amount_reported_base,
        "asset_symbol": case.asset_symbol,
        "asset_decimals": case.asset_decimals,
        "chain_family": case.chain_family,
        "chain_network": case.chain_network,
        "reported_address": case.reported_address,
        "payment_txid": case.payment_txid,
        "payment_ts_ms": case.payment_ts_ms,
        "trace_mode": trace_mode,
        "trace_snapshot_ref": "TS-2026-08-30-0917" if fixture_like else f"snapshot-{snapshot.id}" if snapshot else "recorded-snapshot",
        "chain_data_read": "Controlled fixture source" if trace_mode == "fixture" else "Recorded provider evidence",
        "deposit_address": finding.deposit_address,
        "amount_credited_base": finding.amount_credited_base,
        "deposit_observed_ts_ms": terminal_hop.get("ts_ms") if fixture_like else None,
        "intermediate_hop_count": max(len(dominant_path) - 2, 0) if fixture_like else None,
        "fiu_ind_reg": str(entity.get("fiu_ind_reg") or "Recorded with FIU-IND"),
        "notice_date_ts_ms": notice.created_ts_ms,
    }


def _supervisor_defaults(session: Session, case: Case | None) -> dict[str, str]:
    fixture_supervisor = demo_case()["officers"]["supervisor"]
    supervisor_pis = ""
    if case is not None and case.id is not None:
        assignment = session.exec(
            select(CaseAssignment).where(CaseAssignment.case_id == case.id)
        ).first()
        supervisor_pis = assignment.supervising_acp_pis if assignment else ""
    supervisor = (
        session.exec(select(OfficerProfile).where(OfficerProfile.pis == supervisor_pis)).first()
        if supervisor_pis
        else None
    )
    return {
        "supervisor_pis": supervisor_pis or str(fixture_supervisor["pis"]),
        "supervisor_name": supervisor.name if supervisor else str(fixture_supervisor["name"]),
    }


def _author_defaults(session: Session, pis: str) -> dict[str, str]:
    profile = session.exec(select(OfficerProfile).where(OfficerProfile.pis == pis)).first()
    if profile is not None:
        return {"pis": profile.pis, "name": profile.name, "rank": profile.rank}
    for officer in demo_case()["officers"].values():
        if str(officer.get("pis")) == pis:
            return {
                "pis": pis,
                "name": str(officer.get("name") or "Recorded officer"),
                "rank": str(officer.get("rank") or "Investigating Officer"),
            }
    return {"pis": pis, "name": "Recorded officer", "rank": "Investigating Officer"}


def _initial_notice_parameters(
    session: Session,
    notice: Notice,
    *,
    author: dict[str, str],
) -> dict[str, Any]:
    case = session.get(Case, notice.case_id)
    finding = session.get(Finding, notice.finding_id)
    if case is None or finding is None:
        raise NoticeWorkflowError("The notice is not linked to a complete case and finding.")
    supervisor_defaults = _supervisor_defaults(session, case)
    snapshot = session.get(TraceSnapshot, finding.snapshot_id)
    entity = demo_case()["entity"]
    return {
        "notice_no": notice.notice_no,
        "case_ref": case.ack_no,
        "notice_type": "freeze",
        "transaction_hashes": [case.payment_txid] if case.payment_txid is not None else [],
        "amount_base": finding.amount_credited_base,
        "duration_hours": int(notice.deadline_hours),
        "vasp_name": str(entity["name"]),
        "vasp_contact": str(entity["le_contact"]),
        "jurisdiction": case.jurisdiction,
        "officer_pis": author["pis"],
        "officer_name": author["name"],
        "officer_rank": author["rank"],
        "supervisor_pis": supervisor_defaults["supervisor_pis"],
        "supervisor_name": supervisor_defaults["supervisor_name"],
        "statutory_key": "bnss_106",
        "statutory_label": STATUTORY_OPTIONS["bnss_106"],
        "service_channels": ["portal", "email"],
        "legal_basis": "Generic legal-basis specimen wording; confirm authority before use.",
        **_document_fact_parameters(
            case=case,
            finding=finding,
            snapshot=snapshot,
            notice=notice,
            entity=entity,
        ),
        "provenance": {
            "transaction_hashes": "case.payment_txid",
            "amount_base": "finding.amount_credited_base",
            "jurisdiction": "case.jurisdiction",
            "officer": "authenticated officer profile",
            "supervisor": "case assignment",
            "vasp": "controlled fixture directory",
        },
    }


def autofill_readonly_parameter_defaults(
    session: Session,
    draft: NoticeDraft,
) -> NoticeDraft:
    parameters = dict(draft.parameters or {})
    changed = False
    for key, value in _supervisor_defaults(session, session.get(Case, draft.case_id)).items():
        if not str(parameters.get(key) or "").strip():
            parameters[key] = value
            changed = True
    if changed:
        draft.parameters = parameters
        draft.updated_ts_ms = now_ms()
        session.add(draft)
        session.flush()
    return draft


def ensure_notice_draft(
    session: Session,
    notice: Notice,
    *,
    author: dict[str, str],
) -> NoticeDraft:
    row = session.exec(select(NoticeDraft).where(NoticeDraft.finding_id == notice.finding_id)).first()
    if row is not None:
        autofill_readonly_parameter_defaults(session, row)
        case = session.get(Case, row.case_id)
        finding = session.get(Finding, row.finding_id)
        snapshot = session.get(TraceSnapshot, finding.snapshot_id) if finding else None
        if case is not None and finding is not None:
            entity = demo_case()["entity"]
            facts = _document_fact_parameters(
                case=case,
                finding=finding,
                snapshot=snapshot,
                notice=notice,
                entity=entity,
            )
            parameters = dict(row.parameters)
            changed = False
            for key, value in facts.items():
                if value is not None and parameters.get(key) in {None, ""}:
                    parameters[key] = value
                    changed = True
            if changed:
                row.parameters = parameters
                row.updated_ts_ms = now_ms()
        if row.notice_id is None:
            row.notice_id = notice.id
            row.updated_ts_ms = now_ms()
        if row.annex_enabled is False:
            row.annex_enabled = True
            if row.generated:
                row.dirty = True
                _clear_attestation(row)
            row.updated_ts_ms = now_ms()
        if row.notice_id == notice.id:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row
    timestamp = now_ms()
    row = NoticeDraft(
        case_id=int(notice.case_id),
        finding_id=int(notice.finding_id),
        notice_id=notice.id,
        parameters=_initial_notice_parameters(session, notice, author=author),
        annex_enabled=True,
        created_by_pis=author["pis"],
        created_ts_ms=timestamp,
        updated_ts_ms=timestamp,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def reset_notice_draft_for_demo_cycle(
    session: Session,
    draft: NoticeDraft,
    notice: Notice,
    *,
    reset_ts_ms: int | None = None,
) -> NoticeDraft:
    timestamp = now_ms() if reset_ts_ms is None else reset_ts_ms
    author = _author_defaults(session, draft.created_by_pis or notice.created_by_pis)
    draft.parameters = _initial_notice_parameters(session, notice, author=author)
    draft.annex_enabled = True
    draft.stage = "parameters"
    draft.generated = False
    draft.dirty = False
    draft.active_version_no = None
    _clear_attestation(draft)
    draft.updated_ts_ms = timestamp
    session.add(draft)
    session.flush()
    return draft


def validate_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    result = dict(parameters)
    hashes = result.get("transaction_hashes")
    if not isinstance(hashes, list) or not hashes or any(not str(value).strip() for value in hashes):
        raise NoticeWorkflowError("At least one transaction hash is required.")
    amount = result.get("amount_base")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise NoticeWorkflowError("The requested amount must be a positive integer in base units.")
    duration = result.get("duration_hours")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        raise NoticeWorkflowError("The response duration must be a positive whole number of hours.")
    for key in ("vasp_name", "vasp_contact", "jurisdiction", "officer_pis", "supervisor_pis"):
        if not str(result.get(key) or "").strip():
            raise NoticeWorkflowError(f"{key.replace('_', ' ').capitalize()} is required.")
    statutory_key = str(result.get("statutory_key") or "")
    if statutory_key not in STATUTORY_OPTIONS:
        raise NoticeWorkflowError("Select a supported specimen statutory label.")
    result["statutory_label"] = STATUTORY_OPTIONS[statutory_key]
    result["transaction_hashes"] = [str(value).strip() for value in hashes]
    channels = result.get("service_channels")
    if not isinstance(channels, list):
        channels = []
    clean_channels = []
    for value in channels:
        key = str(value).strip()
        if key and key not in clean_channels:
            clean_channels.append(key)
    unknown_channels = set(clean_channels).difference(SERVICE_CHANNEL_OPTIONS)
    if unknown_channels:
        raise NoticeWorkflowError(f"Unknown service channel: {sorted(unknown_channels)[0]}")
    if not clean_channels:
        raise NoticeWorkflowError("Select at least one channel for serving the notice.")
    result["service_channels"] = clean_channels
    return result


def update_notice_parameters(
    session: Session,
    draft: NoticeDraft,
    parameters: dict[str, Any],
) -> NoticeDraft:
    validated = validate_parameters(parameters)
    draft.parameters = validated
    draft.stage = "attachments"
    draft.dirty = draft.generated
    if draft.dirty:
        _clear_attestation(draft)
    draft.updated_ts_ms = now_ms()
    session.add(draft)
    session.flush()
    return draft


def validate_attachment(*, name: str, mime_type: str, data: bytes, slot: str) -> tuple[str, str]:
    if slot not in ATTACHMENT_SLOTS:
        raise NoticeWorkflowError("Unknown attachment slot.")
    if not data:
        raise NoticeWorkflowError("The attachment is empty.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise NoticeWorkflowError("Each attachment must be 10 MB or smaller.")
    clean_name = Path(name or "attachment").name
    if clean_name != name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ ()-]{0,199}", clean_name):
        raise NoticeWorkflowError("Use a safe attachment filename.")
    detected = _detect_mime(data)
    if detected not in ALLOWED_MIME or mime_type not in ALLOWED_MIME or detected != mime_type:
        raise NoticeWorkflowError("Attachment content and MIME type must be PDF, PNG, or JPG.")
    suffixes = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}
    if Path(clean_name).suffix.lower() not in ({suffixes[detected]} | ({".jpeg"} if detected == "image/jpeg" else set())):
        raise NoticeWorkflowError("Attachment extension does not match its content.")
    return clean_name, detected


def add_attachment(
    session: Session,
    draft: NoticeDraft,
    *,
    slot: str,
    source: str,
    original_name: str,
    mime_type: str,
    data: bytes,
    provenance: str | None,
    simulated: bool,
    author_pis: str,
) -> NoticeAttachment:
    active = active_attachments(session, int(draft.id or 0))
    if len(active) >= MAX_ATTACHMENTS:
        raise NoticeWorkflowError("A notice may contain no more than 10 active attachments.")
    clean_name, detected = validate_attachment(
        name=original_name,
        mime_type=mime_type,
        data=data,
        slot=slot,
    )
    digest = sha256_bytes(data)
    relative = Path("notice_attachments") / digest[:2] / f"{digest}{Path(clean_name).suffix.lower()}"
    target = settings.var_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256_bytes(target.read_bytes()) != digest:
            raise NoticeWorkflowError("Content-addressed attachment collision detected.")
    else:
        target.write_bytes(data)
    previous = next((item for item in reversed(active) if item.slot == slot), None)
    row = NoticeAttachment(
        draft_id=int(draft.id or 0),
        slot=slot,
        source=source,
        original_name=clean_name,
        mime_type=detected,
        size_bytes=len(data),
        sha256=digest,
        storage_ref=relative.as_posix(),
        provenance=provenance,
        simulated=simulated,
        created_by_pis=author_pis,
        created_ts_ms=now_ms(),
    )
    session.add(row)
    session.flush()
    if previous is not None:
        previous.superseded_by_id = row.id
        session.add(previous)
    draft.stage = "attachments"
    draft.dirty = draft.generated
    if draft.dirty:
        _clear_attestation(draft)
    draft.updated_ts_ms = now_ms()
    session.add(draft)
    session.flush()
    return row


def active_attachments(session: Session, draft_id: int) -> list[NoticeAttachment]:
    rows = session.exec(
        select(NoticeAttachment)
        .where(NoticeAttachment.draft_id == draft_id)
        .order_by(NoticeAttachment.created_ts_ms, NoticeAttachment.id)
    ).all()
    return [
        row
        for row in rows
        if row.superseded_by_id is None and row.slot in ATTACHMENT_SLOTS
    ]


def import_report_attachments(
    session: Session,
    draft: NoticeDraft,
    *,
    ack_no: str,
    complaint_source: Any,
    author_pis: str,
) -> list[NoticeAttachment]:
    """Attach report-number backed portal documents when a fixture source has them."""
    imported: list[NoticeAttachment] = []
    existing = active_attachments(session, int(draft.id or 0))
    occupied_slots = {item.slot for item in existing}
    fetch_many = getattr(complaint_source, "fetch_documents", None)
    if callable(fetch_many):
        documents = fetch_many(ack_no)
    else:
        documents = []
        for slot in ("complaint", "fir"):
            item = complaint_source.fetch_document(ack_no, slot)
            if item is not None:
                documents.append({"slot": slot, **item})
    for document in documents:
        slot = str(document.get("slot") or "")
        if slot in occupied_slots:
            continue
        row = add_attachment(
            session,
            draft,
            slot=slot,
            source=str(document.get("source") or "fixture_complaint_source"),
            original_name=str(document["name"]),
            mime_type=str(document["mime_type"]),
            data=document["data"],
            provenance=str(document.get("provenance") or ""),
            simulated=bool(document.get("simulated", True)),
            author_pis=author_pis,
        )
        imported.append(row)
        occupied_slots.add(slot)
    return imported


def generate_version(
    session: Session,
    draft: NoticeDraft,
    *,
    author_pis: str,
    tag: str | None = None,
) -> NoticeVersion:
    parameters = validate_parameters(draft.parameters)
    attachments = active_attachments(session, int(draft.id or 0))
    if not any(item.slot == "complaint" for item in attachments):
        raise NoticeWorkflowError("A complaint attachment is required before generation.")
    existing = session.exec(
        select(NoticeVersion)
        .where(NoticeVersion.draft_id == draft.id)
        .order_by(NoticeVersion.version_no.desc())
    ).first()
    version_no = (existing.version_no if existing else 0) + 1
    content = {
        "schema": "trinetra.notice/1",
        "parameters": parameters,
        "annex_enabled": bool(draft.annex_enabled),
        "attachments": [
            {
                "id": item.id,
                "slot": item.slot,
                "name": item.original_name,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "provenance": item.provenance,
                "simulated": item.simulated,
            }
            for item in attachments
        ],
    }
    rendered = render_notice_html(content, version_no=version_no)
    digest = sha256_json({"content": content, "rendered_html": rendered})
    version = NoticeVersion(
        draft_id=int(draft.id or 0),
        version_no=version_no,
        parent_version_id=existing.id if existing else None,
        tag=tag or ("post-ACP-review" if existing and existing.acp_remarks else "draft"),
        content=content,
        rendered_html=rendered,
        content_sha256=digest,
        change_summary=_version_change_summary(existing, content),
        immutable=True,
        created_by_pis=author_pis,
        created_ts_ms=now_ms(),
    )
    session.add(version)
    session.flush()
    for item in attachments:
        if item.version_id is None:
            item.version_id = version.id
            session.add(item)
    draft.parameters = parameters
    draft.generated = True
    draft.dirty = False
    draft.active_version_no = version_no
    draft.stage = "review"
    _clear_attestation(draft)
    draft.updated_ts_ms = now_ms()
    session.add(draft)
    session.flush()
    return version


def active_version(session: Session, draft: NoticeDraft) -> NoticeVersion | None:
    if draft.active_version_no is None:
        return None
    return session.exec(
        select(NoticeVersion).where(
            NoticeVersion.draft_id == draft.id,
            NoticeVersion.version_no == draft.active_version_no,
        )
    ).first()


def attest_version(session: Session, draft: NoticeDraft, *, officer_pis: str) -> NoticeDraft:
    version = active_version(session, draft)
    if version is None or draft.dirty:
        raise NoticeWorkflowError("Generate the current draft before attesting it.")
    timestamp = now_ms()
    draft.attested_by_pis = officer_pis
    draft.attested_ts_ms = timestamp
    draft.attested_version_no = version.version_no
    draft.stage = "verification"
    draft.updated_ts_ms = timestamp
    session.add(draft)
    session.flush()
    return draft


def verification_state(
    session: Session,
    draft: NoticeDraft,
    officer_session_id: int,
) -> NoticeVerificationState:
    row = session.exec(
        select(NoticeVerificationState).where(
            NoticeVerificationState.draft_id == draft.id,
            NoticeVerificationState.officer_session_id == officer_session_id,
        )
    ).first()
    if row is None:
        row = NoticeVerificationState(
            draft_id=int(draft.id or 0),
            officer_session_id=officer_session_id,
            updated_ts_ms=now_ms(),
        )
        session.add(row)
        session.flush()
    return row


def record_verification(
    session: Session,
    draft: NoticeDraft,
    *,
    officer_session_id: int,
    method: str,
    success: bool,
    fixture_mode: bool,
) -> NoticeVerificationState:
    allowed = {"cctns", "sahyog", "demo_io", "demo_acp"}
    if method not in allowed:
        raise NoticeWorkflowError("Unknown verification method.")
    if method.startswith("demo_") and not fixture_mode:
        raise NoticeWorkflowError("Demo verification shortcuts are disabled in live mode.")
    row = verification_state(session, draft, officer_session_id)
    timestamp = now_ms()
    if row.locked_until_ts_ms is not None and timestamp < row.locked_until_ts_ms:
        raise NoticeWorkflowError("Verification is temporarily locked. Try again after 60 seconds.")
    if success:
        row.failure_count = 0
        row.locked_until_ts_ms = None
        row.verified_ts_ms = timestamp
        draft.stage = "routing"
        draft.updated_ts_ms = timestamp
        session.add(draft)
    else:
        row.failure_count += 1
        row.verified_ts_ms = None
        if row.failure_count >= 3:
            row.locked_until_ts_ms = timestamp + 60_000
    row.last_method = method
    row.updated_ts_ms = timestamp
    session.add(row)
    session.flush()
    return row


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    rendered = str(value)
    return rendered if rendered else default


def _escape(value: Any, default: str = "") -> str:
    return html.escape(_safe_text(value, default))


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _notice_date(ts_ms: Any) -> str:
    value = _int_or_none(ts_ms)
    if value is None or value <= 0:
        return "30 August 2026"
    return datetime.fromtimestamp(value / 1000, UTC).strftime("%d %B %Y")


def _notice_ist(ts_ms: Any) -> str:
    value = _int_or_none(ts_ms)
    if value is None or value <= 0:
        return "Recorded time unavailable"
    return format_ist(value)


def _notice_amount(amount_base: Any, *, decimals: Any = 6, symbol: Any = "USDT") -> str:
    amount = _int_or_none(amount_base)
    decimal_places = _int_or_none(decimals) or 6
    if amount is None:
        return "Amount not recorded"
    return format_amount(amount, decimal_places, str(symbol or "USDT"))


def _slot_label(slot: Any) -> str:
    return str(slot or "Attachment").replace("_", " ").title()


def _notice_document_css() -> str:
    return """
@page { size: A4; margin: 0; }
html, body { margin: 0; min-height: 100%; }
body.notice-page { background: #e9edf2; color: #111a2b; }
.notice-page .notice-page-mat { min-width: max-content; min-height: calc(1123px + 96px); padding: 48px 24px; background: #f5f5f4; }
.notice-page .notice-paper { position: relative; width: 794px; min-height: 1123px; box-sizing: border-box; padding: 81px; background: #fff; color: #1a1f26; box-shadow: 0 1px 7px rgba(17,26,43,.12); font-family: Georgia, "Times New Roman", serif; font-size: 14.5px; line-height: 1.7; }
.notice-page .notice-specimen-label { position: absolute; top: 18px; right: 28px; padding: 5px 9px; border: 1px solid #d7a33b; border-radius: 4px; background: #fffaf0; color: #8a5a00; font-family: Arial, sans-serif; font-size: 10px; font-weight: 700; letter-spacing: .08em; line-height: 1; white-space: nowrap; }
.notice-page .notice-sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; }
.notice-page .notice-letterhead { display: flex; align-items: center; gap: 16px; padding-bottom: 12px; border-bottom: 3px double #1a1f26; }
.notice-page .notice-letterhead > div { flex: 1; display: flex; flex-direction: column; gap: 2px; text-align: center; }
.notice-page .notice-letterhead small { color: #4a5560; font-family: Arial, sans-serif; font-size: 11.5px; letter-spacing: .08em; }
.notice-page .notice-letterhead strong { font-family: Arial, sans-serif; font-size: 17px; letter-spacing: .02em; }
.notice-page .notice-letterhead span { color: #4a5560; font-family: Arial, sans-serif; font-size: 12.5px; }
.notice-page .notice-emblem { width: 43px; height: 65px; flex: none; display: flex; flex-direction: column; align-items: center; justify-content: center; color: #111; border: 1px solid transparent; }
.notice-page .notice-emblem i { font-size: 32px; line-height: 1; font-style: normal; }
.notice-page .notice-emblem b { color: #111; font-family: Arial, sans-serif; font-size: 5.5px; line-height: 1; white-space: nowrap; }
.notice-page .notice-letterhead-balance { width: 52px; flex: none; }
.notice-page .notice-number { display: flex; align-items: baseline; justify-content: space-between; gap: 20px; padding-top: 14px; font-family: Consolas, monospace; font-size: 13px; }
.notice-page .notice-addressee { margin: 18px 0 0; font-size: 14.5px; line-height: 1.6; }
.notice-page .notice-subject { margin: 16px 0 0; font-family: Arial, sans-serif; font-size: 13.5px; line-height: 1.7; }
.notice-page .notice-paper > p:not(.notice-addressee):not(.notice-subject) { margin: 16px 0 0; }
.notice-page .notice-paper > p.notice-justified { margin-top: 12px; text-align: justify; }
.notice-page .notice-facts { width: 100%; margin-top: 10px; border-collapse: collapse; background: transparent; font-family: Arial, sans-serif; }
.notice-page .notice-facts td { padding: 6px 0; border-bottom: 1px solid #d8dde3; background: transparent; vertical-align: top; }
.notice-page .notice-facts td:first-child { width: 40%; padding-right: 10px; color: #4a5560; font-size: 12.5px; }
.notice-page .notice-facts td:last-child { color: #1a1f26; font-family: Consolas, monospace; font-size: 11.5px; line-height: 1.5; word-break: break-all; }
.notice-page .notice-paper ol { display: flex; flex-direction: column; gap: 8px; margin: 10px 0 0; padding-left: 34px; }
.notice-page .notice-methodology { margin-top: 18px; padding-top: 12px; border-top: 1px solid #d8dde3; break-before: page; font-family: Arial, sans-serif; }
.notice-page .notice-methodology h2 { margin: 0 0 9px; font-size: 15px; }
.notice-page .notice-methodology dl { display: grid; grid-template-columns: 110px minmax(0,1fr); gap: 6px 12px; margin: 0; }
.notice-page .notice-methodology dt { color: #4a5560; font-size: 11.5px; }
.notice-page .notice-methodology dd { margin: 0; font-family: Consolas, monospace; font-size: 10.5px; line-height: 1.5; word-break: break-word; }
.notice-page .notice-methodology p { margin: 8px 0 0 !important; color: #4a5560; font-size: 11px; line-height: 1.55; }
.notice-page .notice-signature { display: flex; justify-content: flex-end; margin-top: 26px; }
.notice-page .notice-signature > div { display: flex; flex-direction: column; gap: 2px; text-align: center; }
.notice-page .notice-signature span { color: #4a5560; font-family: Arial, sans-serif; font-size: 13px; }
.notice-page .notice-signature i { height: 46px; }
.notice-page .notice-signature strong { font-size: 14.5px; }
.notice-page .notice-signature small { color: #4a5560; font-size: 12.5px; }
.notice-page .notice-enclosures { margin-top: 22px; padding-top: 12px; border-top: 1px solid #d8dde3; color: #4a5560; font-family: Arial, sans-serif; font-size: 12.5px; }
.notice-page .notice-enclosures div:nth-child(n+2) { margin-top: 4px; }
.mono { font-family: Consolas, monospace; }
@media print {
  body.notice-page { background: #fff; }
  .notice-page .notice-page-mat { width: auto; min-width: 0; min-height: 0; padding: 0; background: #fff; }
  .notice-page .notice-paper { width: auto; min-height: 0; margin: 0; padding: 21mm 18mm 23mm; box-shadow: none; }
}
"""


def render_notice_html(content: dict[str, Any], *, version_no: int) -> str:
    p = content["parameters"]
    decimals = p.get("asset_decimals", 6)
    symbol = p.get("asset_symbol", "USDT")
    amount_reported = _notice_amount(p.get("amount_reported_base"), decimals=decimals, symbol=symbol)
    amount_traced = _notice_amount(p.get("amount_credited_base", p.get("amount_base")), decimals=decimals, symbol=symbol)
    filed_date = _notice_date(p.get("filed_ts_ms"))
    notice_date = _notice_date(p.get("notice_date_ts_ms") or p.get("filed_ts_ms"))
    payment_time = _notice_ist(p.get("payment_ts_ms"))
    deposit_time = _notice_ist(p.get("deposit_observed_ts_ms"))
    hop_count = _int_or_none(p.get("intermediate_hop_count"))
    hop_phrase = f"{hop_count} intermediate addresses" if hop_count is not None else "recorded intermediate addresses"
    chain_asset = f"{_safe_text(p.get('chain_family'), 'Chain')}, {_safe_text(symbol, 'USDT')}-TRC20"
    tx_hash = _safe_text(p.get("payment_txid")) or (
        str((p.get("transaction_hashes") or [""])[0])
    )
    selected_channels = [
        SERVICE_CHANNEL_OPTIONS.get(str(key), str(key))
        for key in (p.get("service_channels") or [])
    ]
    service_summary = "; ".join(selected_channels) if selected_channels else "No service channel selected"
    attachments = content.get("attachments") or []
    attachment_names = ", ".join(_slot_label(item.get("slot")) for item in attachments)
    enclosure_summary = attachment_names or "Complaint extract and trace material pending attachment"
    facts = [
        ("Complaint acknowledgement", p.get("case_ref")),
        ("Trace snapshot", p.get("trace_snapshot_ref")),
        ("Trace data mode", str(p.get("trace_mode") or "fixture").upper()),
        ("Chain data read", p.get("chain_data_read")),
        ("Victim payment", payment_time),
        ("Amount reported lost", amount_reported),
        ("Chain and asset", chain_asset),
        ("Deposit address on your platform", p.get("deposit_address")),
        ("Amount traced to that address", amount_traced),
        ("Deposit observed at", deposit_time),
        ("Originating transaction hash", tx_hash),
    ]
    fact_rows = "".join(
        f"<tr><td>{_escape(label)}</td><td>{_escape(value, 'Recorded fact unavailable')}</td></tr>"
        for label, value in facts
    )
    css = _notice_document_css()
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Freeze notice version {version_no}</title><style>{css}</style></head>
<body class="notice-page">
  <div class="notice-page-mat">
    <article class="notice-paper" aria-label="Freeze notice draft version {version_no}">
      <span class="notice-sr-only">Immutable workflow version {version_no}</span>
      <div class="notice-specimen-label" role="note">SPECIMEN · NOT FOR LIVE DISPATCH</div>
      <div class="notice-letterhead">
        <span class="notice-emblem" aria-label="State emblem"><i>☸</i><b>सत्यमेव जयते</b></span>
        <div><small>GOVERNMENT OF MAHARASHTRA · POLICE DEPARTMENT</small><strong>OFFICE OF THE CYBER CRIME CELL</strong><span>Pune City Police, Maharashtra — 411001 · cybercell-pune@mahapolice.gov.in</span></div>
        <i class="notice-letterhead-balance"></i>
      </div>
      <div class="notice-number"><span>No. {_escape(p.get('notice_no'), 'Pending notice number')}</span><span>Dated: {_escape(notice_date)}</span></div>
      <p class="notice-addressee">To,<br>The Compliance Officer,<br>{_escape(p.get('vasp_name'))},<br>FIU-IND Registration {_escape(p.get('fiu_ind_reg'), 'recorded with FIU-IND')}.</p>
      <p class="notice-subject"><strong>Subject:</strong> <u>Direction to restrain and preserve virtual digital assets held at a deposit address traced from the payment identified in the filed complaint.</u></p>
      <p>Madam / Sir,</p>
      <p class="notice-justified">1.&nbsp;&nbsp;A complaint bearing acknowledgement number {_escape(p.get('case_ref'))} was filed on {_escape(filed_date)} reporting the loss of {_escape(amount_reported)} in an alleged investment fraud. An on-chain examination of the reported payment establishes that the funds were moved through {_escape(hop_phrase)} and that {_escape(amount_traced)} was deposited into an address maintained on your platform, as set out below:</p>
      <table class="notice-facts"><tbody>{fact_rows}</tbody></table>
      <p class="notice-justified">2.&nbsp;&nbsp;You are directed, in exercise of the powers conferred on the undersigned under Section 106 of the Bharatiya Nagarik Suraksha Sanhita, 2023, read with the standing instructions of the State Nodal Officer for cybercrime, to take the following steps. <span data-deadline-words>Response required: ASAP, and in any case within {_escape(p.get('duration_hours'), '24')} hours of receipt of this notice.</span></p>
      <ol type="a">
        <li>Place a hold on the balance standing at the said deposit address, and on the customer account to which it is credited, to the extent of {_escape(amount_traced)}.</li>
        <li>Preserve all account-opening records, know-your-customer documents, login and device logs, and the deposit and withdrawal history of that account for the period 1 July 2026 to date.</li>
        <li>Furnish to this office the registered name, contact particulars and verification status of the account holder.</li>
        <li>Confirm compliance in writing to the undersigned, quoting the notice number above.</li>
      </ol>
      <p class="notice-justified">3.&nbsp;&nbsp;The restraint is to be maintained until it is withdrawn in writing by this office or modified by an order of a competent court. Any dealing with the said balance after receipt of this notice will be treated as non-compliance and reported to the registering authority.</p>
      <p class="notice-justified">4.&nbsp;&nbsp;Kindly acknowledge receipt of this notice and confirm compliance in writing to the undersigned, quoting the notice number above.</p>
      <section class="notice-methodology" aria-label="Trace methodology annex">
        <h2>Trace methodology annex</h2>
        <dl>
          <dt>Strategy</dt><dd>Dominant fund flow</dd>
          <dt>Allocation</dt><dd>outgoing_attributed_base = (outgoing_base * incoming_attributed_base + carried_residual) // balance_base</dd>
          <dt>Residual</dt><dd>The integer remainder is carried into the next ordered outgoing allocation; value is never created by rounding.</dd>
          <dt>Terminal</dt><dd>VASP deposit; case stage custody found; finding permitted: true.</dd>
        </dl>
        <p>The synchronous live helper uses max(incoming attributed amount, observed outgoing total) as its initial allocation denominator. This is a bounded approximation, not full historical balance reconstruction.</p>
        <p>Explicit limits: Live frontier addresses are unscored observed outgoing transfers only. Behavioural similarity is not custody attribution, account identity, intent or participation. The fixture registry is controlled demonstration data; it is not a live authoritative registry. Probabilities are disabled pending independent labelled data and calibration. An unsupported, failed or incomplete trace cannot create a custody finding. Unconfirmed observations are excluded from attribution, canonical evidence and terminal decisions. No adverse findings in available sources is not an assertion of legitimacy.</p>
      </section>
      <div class="notice-signature"><div><span>Yours faithfully,</span><i></i><strong>{_escape(p.get('officer_name'))}</strong><span>{_escape(p.get('officer_rank'), 'Investigating Officer')}, Cyber Crime Cell</span><small class="mono">PIS {_escape(p.get('officer_pis'))}</small></div></div>
      <div class="notice-enclosures"><div>Encl: {_escape(enclosure_summary)}.</div><div>Selected service channels: {_escape(service_summary)}.</div><div>Copy to:</div><div>&nbsp;&nbsp;1.&nbsp;State Nodal Officer, Cybercrime, Maharashtra — for information.</div><div>&nbsp;&nbsp;2.&nbsp;FIU-IND Liaison Cell — for information.</div><div>&nbsp;&nbsp;3.&nbsp;Case file {_escape(p.get('case_ref'))}.</div></div>
    </article>
  </div>
</body>
</html>"""


def render_version_pdf(
    session: Session,
    *,
    notice: Notice,
    draft: NoticeDraft,
    version: NoticeVersion,
    author_pis: str,
) -> NoticeAttachment:
    existing = session.exec(
        select(NoticeAttachment).where(
            NoticeAttachment.draft_id == draft.id,
            NoticeAttachment.version_id == version.id,
            NoticeAttachment.slot == "generated_notice_pdf",
        )
    ).first()
    if existing is not None:
        target = ROOT_DIR / existing.storage_ref
        if target.exists() and sha256_bytes(target.read_bytes()) == existing.sha256:
            return existing
    output_dir = ROOT_DIR / "output" / "notices"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_notice_no = re.sub(r"[^A-Za-z0-9._-]+", "-", notice.notice_no).strip("-_")
    temporary = output_dir / f".{safe_notice_no}-v{version.version_no}.pdf.tmp"
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(version.rendered_html, wait_until="load")
        page.pdf(path=str(temporary), format="A4", print_background=True, prefer_css_page_size=True)
        browser.close()
    data = temporary.read_bytes()
    digest = sha256_bytes(data)
    final_path = output_dir / f"{safe_notice_no}-v{version.version_no}-{digest[:12]}.pdf"
    if final_path.exists() and sha256_bytes(final_path.read_bytes()) != digest:
        temporary.unlink(missing_ok=True)
        raise NoticeWorkflowError("Immutable PDF filename collision detected.")
    if not final_path.exists():
        temporary.replace(final_path)
    else:
        temporary.unlink(missing_ok=True)
    reader = PdfReader(str(final_path))
    extracted = "\n".join((page.extract_text() or "") for page in reader.pages)
    if not reader.pages or notice.notice_no not in extracted:
        # The notice number may not be present in legacy-generated content; the
        # version heading and legal-review watermark must always be extractable.
        if "legal review" not in extracted.lower() or f"version {version.version_no}" not in extracted.lower():
            raise NoticeWorkflowError("Generated PDF failed extractability validation.")
    relative = final_path.relative_to(ROOT_DIR).as_posix()
    row = NoticeAttachment(
        draft_id=int(draft.id or 0),
        version_id=int(version.id or 0),
        slot="generated_notice_pdf",
        source="playwright",
        original_name=final_path.name,
        mime_type="application/pdf",
        size_bytes=len(data),
        sha256=digest,
        storage_ref=relative,
        provenance=f"Immutable rendering of notice version {version.version_no}; {len(reader.pages)} page(s)",
        simulated=not settings.legal_copy_approved,
        created_by_pis=author_pis,
        created_ts_ms=now_ms(),
    )
    session.add(row)
    notice.pdf_sha256 = digest
    session.add(notice)
    session.flush()
    return row


def version_diff(before: NoticeVersion, after: NoticeVersion) -> dict[str, Any]:
    old = before.content
    new = after.content
    old_p = old.get("parameters", {})
    new_p = new.get("parameters", {})
    keys = sorted(set(old_p) | set(new_p))
    fields = [
        {"field": key, "before": old_p.get(key), "after": new_p.get(key)}
        for key in keys
        if old_p.get(key) != new_p.get(key)
    ]
    old_a = {item["sha256"]: item for item in old.get("attachments", [])}
    new_a = {item["sha256"]: item for item in new.get("attachments", [])}
    return {
        "fields": fields,
        "attachments_added": [new_a[key] for key in sorted(set(new_a) - set(old_a))],
        "attachments_removed": [old_a[key] for key in sorted(set(old_a) - set(new_a))],
        "annex_changed": old.get("annex_enabled") != new.get("annex_enabled"),
        "statutory_label_changed": old_p.get("statutory_label") != new_p.get("statutory_label"),
    }


def _detect_mime(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "application/octet-stream"


def _clear_attestation(draft: NoticeDraft) -> None:
    draft.attested_by_pis = None
    draft.attested_ts_ms = None
    draft.attested_version_no = None


def _version_change_summary(previous: NoticeVersion | None, content: dict[str, Any]) -> str:
    if previous is None:
        return "Initial generated draft"
    before = previous.content
    changes = 0
    if before.get("parameters") != content.get("parameters"):
        changes += 1
    if before.get("attachments") != content.get("attachments"):
        changes += 1
    if before.get("annex_enabled") != content.get("annex_enabled"):
        changes += 1
    return f"Regenerated with {changes} changed section{'s' if changes != 1 else ''}"
