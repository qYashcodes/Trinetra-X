from __future__ import annotations

import html
import json
import re
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
from app.services.time import now_ms
from app.settings import ROOT_DIR, settings


ATTACHMENT_SLOTS = {
    "complaint",
    "fir",
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
WORKFLOW_STAGES = (
    "parameters",
    "attachments",
    "generated",
    "review",
    "verification",
    "routing",
)


class NoticeWorkflowError(ValueError):
    pass


def ensure_notice_draft(
    session: Session,
    notice: Notice,
    *,
    author: dict[str, str],
) -> NoticeDraft:
    row = session.exec(select(NoticeDraft).where(NoticeDraft.finding_id == notice.finding_id)).first()
    if row is not None:
        if row.notice_id is None:
            row.notice_id = notice.id
            session.add(row)
            session.commit()
            session.refresh(row)
        return row
    case = session.get(Case, notice.case_id)
    finding = session.get(Finding, notice.finding_id)
    if case is None or finding is None:
        raise NoticeWorkflowError("The notice is not linked to a complete case and finding.")
    assignment = session.exec(
        select(CaseAssignment).where(CaseAssignment.case_id == case.id)
    ).first()
    supervisor_pis = assignment.supervising_acp_pis if assignment else ""
    supervisor = (
        session.exec(select(OfficerProfile).where(OfficerProfile.pis == supervisor_pis)).first()
        if supervisor_pis
        else None
    )
    entity = demo_case()["entity"]
    timestamp = now_ms()
    row = NoticeDraft(
        case_id=int(case.id or 0),
        finding_id=int(finding.id or 0),
        notice_id=notice.id,
        parameters={
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
            "supervisor_pis": supervisor_pis,
            "supervisor_name": supervisor.name if supervisor else "",
            "statutory_key": "bnss_106",
            "statutory_label": STATUTORY_OPTIONS["bnss_106"],
            "legal_basis": "Generic legal-basis specimen wording; confirm authority before use.",
            "provenance": {
                "transaction_hashes": "case.payment_txid",
                "amount_base": "finding.amount_credited_base",
                "jurisdiction": "case.jurisdiction",
                "officer": "authenticated officer profile",
                "supervisor": "case assignment",
                "vasp": "controlled fixture directory",
            },
        },
        created_by_pis=author["pis"],
        created_ts_ms=timestamp,
        updated_ts_ms=timestamp,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


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


def render_notice_html(content: dict[str, Any], *, version_no: int) -> str:
    p = content["parameters"]
    tx_rows = "".join(f"<li>{html.escape(str(value))}</li>" for value in p["transaction_hashes"])
    attachment_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item['slot']).replace('_', ' ').title())}</td>"
        f"<td>{html.escape(str(item['name']))}</td>"
        f"<td>{html.escape(str(item['sha256']))}</td>"
        f"<td>{'SIMULATED' if item['simulated'] else 'RECORDED'}</td>"
        "</tr>"
        for item in content["attachments"]
    )
    annex = (
        "<section class='annex'><h2>Methodology annex</h2>"
        "<p>This annex records the prototype tracing method and does not assert account ownership.</p></section>"
        if content["annex_enabled"]
        else ""
    )
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
@page {{ size: A4; margin: 21mm 18mm 23mm; @bottom-center {{ content: 'Page ' counter(page) ' of ' counter(pages); font: 9px sans-serif; color:#475569; }} }}
body {{ font: 11px/1.5 Arial,sans-serif; color:#172033; }} h1 {{ font-size:18px; text-align:center; }} h2 {{ font-size:13px; break-after:avoid; }}
.letterhead {{ border-bottom:2px solid #1e3a8a; padding-bottom:8px; margin-bottom:18px; }}
.specimen {{ position:fixed; inset:42% 5%; transform:rotate(-27deg); font:bold 42px Arial; color:rgba(180,30,30,.13); text-align:center; z-index:-1; }}
table {{ width:100%; border-collapse:collapse; font-size:9px; }} th,td {{ border:1px solid #94a3b8; padding:5px; overflow-wrap:anywhere; }} thead {{ display:table-header-group; }} tr {{ break-inside:avoid; }}
.signature {{ break-inside:avoid; margin-top:28px; border-top:1px solid #475569; padding-top:12px; }} .mono {{ font-family:Consolas,monospace; }}
</style></head><body><div class='specimen'>SPECIMEN · LEGAL REVIEW PENDING</div>
<header class='letterhead'><strong>TRINETRA — Cyber Financial Crime Investigation Desk</strong><br>Prototype document · not proof of delivery or restraint</header>
<h1>Preservation / restraint request — version {version_no}</h1>
<p><strong>Notice:</strong> {html.escape(str(p.get('notice_no') or 'Pending'))}<br><strong>Case:</strong> {html.escape(str(p.get('case_ref') or 'Pending'))}</p>
<p><strong>To:</strong> {html.escape(str(p['vasp_name']))} ({html.escape(str(p['vasp_contact']))})</p>
<p><strong>Jurisdiction:</strong> {html.escape(str(p['jurisdiction']))}</p>
<p><strong>Specimen statutory label:</strong> {html.escape(str(p['statutory_label']))}</p>
<p>{html.escape(str(p['legal_basis']))}</p>
<h2>Referenced transactions</h2><ul class='mono'>{tx_rows}</ul>
<p>Requested amount in integer base units: <strong class='mono'>{int(p['amount_base'])}</strong>.</p>
<p>Requested response window: {int(p['duration_hours'])} hours after recorded dispatch.</p>
<h2>Annexure table</h2><table><thead><tr><th>Type</th><th>File</th><th>SHA-256</th><th>Provenance</th></tr></thead><tbody>{attachment_rows}</tbody></table>
{annex}<section class='signature'><strong>Prepared by</strong><br>{html.escape(str(p['officer_rank']))} {html.escape(str(p['officer_name']))}<br>PIS {html.escape(str(p['officer_pis']))}<br><br><strong>Supervising ACP</strong><br>{html.escape(str(p.get('supervisor_name') or p['supervisor_pis']))}</section>
</body></html>"""


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
