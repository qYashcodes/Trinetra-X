from __future__ import annotations

import json
import secrets
import zipfile
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Annotated
from hmac import compare_digest

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

try:
    from sse_starlette.sse import EventSourceResponse
except Exception:  # pragma: no cover - import guard for partial installs
    EventSourceResponse = None

from app.db import get_session, init_db
from app.docket_fixture import docket_fixture
from app.engine_bridge import ChainRef, TraceParams, TraceSeed, detect_chain, resolve_chain_activity, run_trace
from app.integrations.complaints.fixture import FixtureComplaintSource
from app.models import Case, CaseStage, Dispatch, Finding, Notice, TraceEvent, TraceSnapshot
from app.providers.oidc import build_authorization_request, verify_callback
from app.providers.webauthn import registration_options, verify_registration
from app.repository import (
    case_from_complaint,
    ensure_finding_review_checks,
    finding_review_specs,
    get_or_create_trace,
    prepare_notice,
    seed_demo,
)
from app.services.audit import append_audit_event, verify_audit_chain
from app.services.demo import demo_case
from app.services.exhibit import snapshot_svg
from app.services.hash import sha256_bytes
from app.services.integrations import integration_status
from app.services.money import format_amount, format_millions
from app.services.notices import notice_view_model, sahyog_manifest
from app.services.risk import risk_check, risk_page_data
from app.services.search import search_records
from app.services.time import format_ist, now_ms
from app.settings import ROOT_DIR, settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="TRINETRA", version="0.1.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=ROOT_DIR / "app" / "static"), name="static")

templates = Jinja2Templates(directory=ROOT_DIR / "app" / "templates")
templates.env.filters["amount"] = format_amount
templates.env.filters["ist"] = format_ist


def format_amount_number(amount_base: int, decimals: int = 6) -> str:
    return format_amount(amount_base, decimals, "").strip()


templates.env.filters["amount_number"] = format_amount_number
templates.env.filters["millions"] = format_millions


def short_value(value: str | None, left: int = 8, right: int = 6) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= left + right + 3:
        return text
    return f"{text[:left]}...{text[-right:]}"


def pct_bp(value: int | None) -> str:
    return f"{(value or 0) / 100:.2f}%"


def pct_bp_compact(value: int | None) -> str:
    number = f"{(value or 0) / 100:.2f}".rstrip("0").rstrip(".")
    return f"{number}%"


def labelize(value: str | None) -> str:
    return (value or "").replace("_", " ").replace("-", " ").capitalize()


templates.env.filters["short"] = short_value
templates.env.filters["pctbp"] = pct_bp
templates.env.filters["pctcompact"] = pct_bp_compact
templates.env.filters["labelize"] = labelize

complaints = FixtureComplaintSource()

NOTICE_CHANNELS = {
    "portal": {
        "storage_key": "le-portal",
        "target": "coinsphere-le / request 8841",
    },
    "email": {
        "storage_key": "compliance-email",
        "target": demo_case()["entity"]["le_contact"],
    },
    "nodal-copy": {
        "storage_key": "state-nodal-copy",
        "target": "nodal-mh@cybercell.example.invalid",
    },
}
NOTICE_DEADLINES = {24, 72, 168}


def risk_notice_state(session: Session) -> dict:
    """Return the persisted notice state for the fixture custody finding, if any."""
    fixture_address = demo_case()["terminal"]["deposit_address"]
    finding = session.exec(
        select(Finding)
        .where(Finding.deposit_address == fixture_address)
        .order_by(Finding.created_ts_ms.desc(), Finding.id.desc())
    ).first()
    if not finding:
        return {"status": "not_recorded"}
    notice = session.exec(
        select(Notice)
        .where(Notice.finding_id == finding.id)
        .order_by(Notice.created_ts_ms.desc(), Notice.id.desc())
    ).first()
    if not notice:
        return {"status": "not_prepared", "finding_id": finding.id}
    dispatches = session.exec(select(Dispatch).where(Dispatch.notice_id == notice.id)).all()
    return {
        "status": notice.status,
        "notice_no": notice.notice_no,
        "dispatch_count": len(dispatches),
        "failed_count": sum(row.status == "failed" for row in dispatches),
    }


def case_trail(
    case: Case,
    snapshot: TraceSnapshot | None,
    finding: Finding | None,
    notice: Notice | None,
    dispatches: list[Dispatch] | None = None,
) -> list[dict]:
    dispatches = dispatches or []
    steps = [
        {
            "label": "Complaint ingested",
            "detail": case.ack_no,
            "status": "complete",
            "ts_ms": case.created_ts_ms,
        },
        {
            "label": "Trace snapshot sealed",
            "detail": snapshot.sha256[:12] if snapshot else "Awaiting trace",
            "status": "complete" if snapshot else "pending",
            "ts_ms": snapshot.closed_ts_ms if snapshot else None,
        },
        {
            "label": "Custody finding recorded",
            "detail": finding.deposit_address[:6] + "..." + finding.deposit_address[-4:] if finding and finding.deposit_address else "Review pending",
            "status": "complete" if finding else "pending",
            "ts_ms": finding.created_ts_ms if finding else None,
        },
        {
            "label": "Freeze notice prepared",
            "detail": notice.notice_no if notice else "Not prepared",
            "status": "complete" if notice else "pending",
            "ts_ms": notice.created_ts_ms if notice else None,
        },
        {
            "label": "Dispatch recorded",
            "detail": f"{len(dispatches)} channel records" if dispatches else "Not dispatched",
            "status": "complete" if dispatches else "pending",
            "ts_ms": max((row.created_ts_ms for row in dispatches), default=None),
        },
    ]
    for index, step in enumerate(steps, start=1):
        step["index"] = index
    return steps


def evidence_manifest(
    case: Case,
    snapshot: TraceSnapshot | None,
    finding: Finding | None,
    notice: Notice | None,
    dispatches: list[Dispatch],
) -> dict:
    graph_svg = snapshot_svg(snapshot.result_json).encode("utf-8") if snapshot else b""
    return {
        "schema": "trinetra.evidence_manifest/1",
        "case": {
            "id": case.id,
            "ack_no": case.ack_no,
            "stage": case.stage.value if hasattr(case.stage, "value") else str(case.stage),
            "reported_address": case.reported_address,
            "amount_reported_base": case.amount_reported_base,
            "asset": case.asset_symbol,
        },
        "snapshot": None
        if not snapshot
        else {
            "id": snapshot.id,
            "schema": snapshot.snapshot_schema,
            "sha256": snapshot.sha256,
            "status": snapshot.status,
            "started_ts_ms": snapshot.started_ts_ms,
            "closed_ts_ms": snapshot.closed_ts_ms,
            "graph_exhibit_sha256": sha256_bytes(graph_svg),
        },
        "finding": None
        if not finding
        else {
            "id": finding.id,
            "terminal_kind": finding.terminal_kind,
            "custodian_key": finding.custodian_key,
            "deposit_address": finding.deposit_address,
            "amount_credited_base": finding.amount_credited_base,
            "review_checks": finding.review_checks,
        },
        "notice": None
        if not notice
        else {
            "id": notice.id,
            "notice_no": notice.notice_no,
            "status": notice.status,
            "deadline_hours": notice.deadline_hours,
            "pdf_sha256": notice.pdf_sha256,
            "countersigned": bool(notice.countersigned_by_pis),
        },
        "dispatches": [
            {
                "channel": row.channel,
                "target": row.target,
                "status": row.status,
                "attempts": row.attempts,
                "created_ts_ms": row.created_ts_ms,
            }
            for row in dispatches
        ],
        "audit": {
            "chain_verified": verify_audit_chain(),
            "storage": "var/audit.jsonl",
        },
    }


def evidence_bundle_bytes(
    case: Case,
    snapshot: TraceSnapshot | None,
    finding: Finding | None,
    notice: Notice | None,
    dispatches: list[Dispatch],
) -> bytes:
    manifest = evidence_manifest(case, snapshot, finding, dispatches=dispatches, notice=notice)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        archive.writestr(
            "README.txt",
            "\n".join(
                [
                    "TRINETRA evidence bundle",
                    "",
                    f"Case: {case.ack_no}",
                    "Purpose: offline investigative-aid export for review.",
                    "Legal posture: specimen prototype; not for live dispatch unless configured and approved.",
                    "No guilt assertion is made by this bundle.",
                    "",
                ]
            ),
        )
        archive.writestr("case.json", json.dumps(case.model_dump(mode="json"), indent=2, sort_keys=True))
        if snapshot:
            archive.writestr(
                "trace_snapshot.json",
                json.dumps(snapshot.result_json, indent=2, sort_keys=True),
            )
            archive.writestr("graph_exhibit.svg", snapshot_svg(snapshot.result_json))
        if finding:
            archive.writestr(
                "custody_finding.json",
                json.dumps(finding.model_dump(mode="json"), indent=2, sort_keys=True),
            )
        if notice:
            archive.writestr(
                "notice_state.json",
                json.dumps(notice.model_dump(mode="json"), indent=2, sort_keys=True),
            )
        if dispatches:
            archive.writestr(
                "dispatch_records.json",
                json.dumps([row.model_dump(mode="json") for row in dispatches], indent=2, sort_keys=True),
            )
    return buffer.getvalue()


def build_intake_particulars(record: dict | None, chain: str | None = None, *, attempted: bool = False) -> tuple[list[dict], int, str]:
    missing_text = "Manual addition required"

    def item(label: str, value: object, valid: bool) -> dict:
        if not record:
            status = "missing" if attempted else "pending"
            display = missing_text if attempted else "Awaiting input"
        elif valid:
            status = "complete"
            display = str(value)
        else:
            status = "missing"
            display = missing_text
        return {"label": label, "value": display, "status": status}

    if not record:
        rows = [
            item("Complainant jurisdiction", None, False),
            item("Complaint filed", None, False),
            item("Victim payment time", None, False),
            item("Amount reported", None, False),
            item("Recipient address", None, False),
            item("Chain and asset", None, False),
            item("Payment hash", None, False),
            item("Complainant contact on record", None, False),
        ]
    else:
        asset = record.get("asset") or {}
        txid = record.get("payment_txid")
        txid_valid = isinstance(txid, str) and len(txid) == 64 and all(char in "0123456789abcdefABCDEF" for char in txid)
        amount = record.get("amount_reported_base")
        rows = [
            item("Complainant jurisdiction", record.get("jurisdiction"), bool(record.get("jurisdiction"))),
            item("Complaint filed", format_ist(record["filed_ts_ms"]) if record.get("filed_ts_ms") else None, bool(record.get("filed_ts_ms"))),
            item("Victim payment time", format_ist(record["victim_payment_ts_ms"]) if record.get("victim_payment_ts_ms") else None, bool(record.get("victim_payment_ts_ms"))),
            item("Amount reported", format_amount(amount) if isinstance(amount, int) and amount > 0 else None, isinstance(amount, int) and amount > 0),
            item("Recipient address", short_value(record.get("reported_address"), 12, 8), bool(record.get("reported_address"))),
            item("Chain and asset", f"{chain} · {asset.get('symbol')}" if chain and asset.get("symbol") else None, bool(chain and asset.get("symbol"))),
            item("Payment hash", short_value(txid, 12, 8), txid_valid),
            item("Complainant contact on record", record.get("complainant_contact_redacted"), bool(record.get("complainant_contact_redacted"))),
        ]

    completed = sum(row["status"] == "complete" for row in rows)
    state = "complete" if completed == len(rows) else "missing" if attempted else "pending"
    return rows, completed, state


def session_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return str(token)


def require_csrf(request: Request, submitted_token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not submitted_token or not compare_digest(str(expected), str(submitted_token)):
        raise HTTPException(403, "Invalid or missing workflow token.")


def set_active_case(request: Request, case: Case, snapshot: TraceSnapshot | None = None, finding: Finding | None = None) -> None:
    request.session["active_case"] = {
        "id": case.id,
        "ack_no": case.ack_no,
        "snapshot_id": snapshot.id if snapshot else None,
        "finding_id": finding.id if finding else None,
    }


def active_case_from_session(request: Request) -> dict:
    active = request.session.get("active_case") or {}
    return {
        "id": active.get("id"),
        "ack_no": active.get("ack_no"),
        "snapshot_id": active.get("snapshot_id"),
        "finding_id": active.get("finding_id"),
    }


def get_active_case(request: Request, session: Session) -> Case | None:
    active = active_case_from_session(request)
    case_id = active.get("id")
    if not case_id:
        return None
    case = session.get(Case, int(case_id))
    return case


def template_context(request: Request, user: dict | None = None) -> dict:
    return {
        "request": request,
        "user": user,
        "settings": settings,
        "health": {"ok": True, "label": "Fixture systems operational"},
        "source_health": complaints.health(),
        "demo": demo_case(),
        "active_case": active_case_from_session(request),
        "csrf_token": csrf_token(request) if user else "",
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    if request.session.get("user"):
        return RedirectResponse("/docket", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", template_context(request))


@app.post("/auth/prototype")
def prototype_login(request: Request, role: Annotated[str, Form()] = "io") -> Response:
    officer = demo_case()["officers"]["supervisor" if role == "supervisor" else "io"]
    request.session["user"] = {"role": role, **officer}
    append_audit_event(officer["pis"], "login.prototype", "session")
    return RedirectResponse("/docket", status_code=303)


@app.get("/auth/prototype/supervisor", response_class=HTMLResponse)
def supervisor_login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "supervisor_login.html", template_context(request))


@app.get("/auth/login/{provider}")
def oidc_login(provider: str, request: Request) -> JSONResponse:
    request_data = build_authorization_request(provider, str(request.url_for("oidc_callback", provider=provider)))
    return JSONResponse(request_data, status_code=501)


@app.get("/auth/callback/{provider}", response_class=HTMLResponse)
def oidc_callback(provider: str, request: Request) -> HTMLResponse:
    result = verify_callback(provider, dict(request.query_params))
    return templates.TemplateResponse(request, "integration_pending.html", {**template_context(request), "result": result})


@app.post("/auth/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/docket", response_class=HTMLResponse)
def docket(request: Request, session: Session = Depends(get_session), user: dict = Depends(session_user)) -> HTMLResponse:
    case = get_active_case(request, session) or seed_demo(session)
    snapshot, finding = get_or_create_trace(session, case)
    set_active_case(request, case, snapshot, finding)
    notice = session.exec(select(Notice).where(Notice.case_id == case.id)).first()
    dispatches = (
        session.exec(select(Dispatch).where(Dispatch.notice_id == notice.id).order_by(Dispatch.id)).all()
        if notice
        else []
    )
    ctx = {
        **template_context(request, user),
        "case": case,
        "cases": [case],
        "snapshot": snapshot,
        "finding": finding,
        "notice": notice,
        "case_trail": case_trail(case, snapshot, finding, notice, dispatches),
        "docket": docket_fixture(case),
    }
    return templates.TemplateResponse(request, "docket.html", ctx)


@app.get("/cases/new", response_class=HTMLResponse)
def new_case(request: Request, user: dict = Depends(session_user)) -> HTMLResponse:
    request.session.pop("pending_case_ack", None)
    particulars, completed, intake_state = build_intake_particulars(None)
    return templates.TemplateResponse(
        request,
        "case_intake.html",
        {
            **template_context(request, user),
            "referrals": complaints.referrals_today(),
            "samples": complaints.sample_references(),
            "record": None,
            "ack_value": "",
            "particulars": particulars,
            "completed_particulars": completed,
            "intake_state": intake_state,
        },
    )


@app.post("/cases/ingest", response_class=HTMLResponse)
def ingest_case(
    request: Request,
    ack_no: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
    user: dict = Depends(session_user),
) -> HTMLResponse:
    require_csrf(request, csrf_token)
    normalized_ack = ack_no.strip()
    record = complaints.fetch(normalized_ack) if normalized_ack else None
    chain = detect_chain(record["reported_address"]) if record else None
    particulars, completed, intake_state = build_intake_particulars(
        record,
        chain,
        attempted=bool(normalized_ack),
    )
    if record and completed == len(particulars):
        request.session["pending_case_ack"] = record["ack_no"]
    else:
        request.session.pop("pending_case_ack", None)
    return templates.TemplateResponse(
        request,
        "case_intake.html",
        {
            **template_context(request, user),
            "referrals": complaints.referrals_today(),
            "samples": complaints.sample_references(),
            "record": record,
            "ack_value": normalized_ack,
            "chain": chain,
            "particulars": particulars,
            "completed_particulars": completed,
            "intake_state": intake_state,
        },
    )


@app.post("/cases/start")
def start_case(
    request: Request,
    ack_no: Annotated[str, Form()],
    reviewed: Annotated[str | None, Form()] = None,
    csrf_token: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    require_csrf(request, csrf_token)
    if not reviewed:
        raise HTTPException(400, "Imported particulars must be reviewed before tracing.")
    normalized_ack = ack_no.strip()
    if normalized_ack != request.session.get("pending_case_ack"):
        raise HTTPException(409, "Ingest the complaint record again before starting this trace.")
    record = complaints.fetch(normalized_ack)
    if not record:
        raise HTTPException(404, "Complaint reference not found in the fixture feed.")
    particulars, completed, _intake_state = build_intake_particulars(
        record,
        detect_chain(record["reported_address"]),
        attempted=True,
    )
    if completed != len(particulars):
        raise HTTPException(400, "All complaint particulars must be present before tracing.")
    case = case_from_complaint(session, record)
    snapshot, finding = get_or_create_trace(session, case)
    set_active_case(request, case, snapshot, finding)
    request.session.pop("pending_case_ack", None)
    append_audit_event(user["pis"], "trace.start", case.ack_no, {"snapshot_id": snapshot.id})
    return RedirectResponse(f"/traces/{snapshot.id}", status_code=303)


@app.get("/traces", response_class=HTMLResponse)
def traces(request: Request, session: Session = Depends(get_session), user: dict = Depends(session_user)) -> Response:
    active = active_case_from_session(request)
    latest = session.get(TraceSnapshot, int(active["snapshot_id"])) if active.get("snapshot_id") else None
    if not latest:
        active_case = get_active_case(request, session)
        if active_case:
            latest = session.exec(
                select(TraceSnapshot).where(TraceSnapshot.case_id == active_case.id).order_by(TraceSnapshot.id.desc())
            ).first()
    if not latest:
        case = seed_demo(session)
        latest, _finding = get_or_create_trace(session, case)
    return RedirectResponse(f"/traces/{latest.id}", status_code=307)


@app.get("/traces/{snapshot_id}", response_class=HTMLResponse)
def trace_view(
    snapshot_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404)
    case = session.get(Case, snapshot.case_id)
    finding = session.exec(select(Finding).where(Finding.snapshot_id == snapshot.id)).first()
    if case and finding:
        set_active_case(request, case, snapshot, finding)
    events = session.exec(select(TraceEvent).where(TraceEvent.snapshot_id == snapshot.id).order_by(TraceEvent.seq)).all()
    return templates.TemplateResponse(
        request,
        "trace.html",
        {**template_context(request, user), "case": case, "snapshot": snapshot, "finding": finding, "events": events},
    )


@app.get("/api/traces/{snapshot_id}/stream")
def trace_stream(snapshot_id: int, session: Session = Depends(get_session)):
    events = session.exec(select(TraceEvent).where(TraceEvent.snapshot_id == snapshot_id).order_by(TraceEvent.seq)).all()

    async def stream():
        for event in events:
            yield {
                "id": str(event.seq),
                "event": event.event_type,
                "data": json.dumps(event.data, separators=(",", ":")),
            }

    if EventSourceResponse is None:
        return JSONResponse([{"id": event.seq, "event": event.event_type, "data": event.data} for event in events])
    return EventSourceResponse(stream(), ping=15)


@app.get("/cases/{case_id}/canvas", response_class=HTMLResponse)
def canvas(
    case_id: int,
    request: Request,
    snapshot: int | None = None,
    view: str = "trace",
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    case = session.get(Case, case_id)
    if not case:
        raise HTTPException(404)
    snap = session.get(TraceSnapshot, snapshot) if snapshot else None
    if not snap:
        snap = session.exec(select(TraceSnapshot).where(TraceSnapshot.case_id == case_id).order_by(TraceSnapshot.id.desc())).first()
    finding = session.exec(select(Finding).where(Finding.snapshot_id == snap.id)).first() if snap else None
    set_active_case(request, case, snap, finding)
    return templates.TemplateResponse(
        request,
        "canvas.html",
        {**template_context(request, user), "case": case, "snapshot": snap, "view": view},
    )


@app.get("/findings/{finding_id}", response_class=HTMLResponse)
def finding_view(
    finding_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    finding = session.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404)
    finding = ensure_finding_review_checks(session, finding)
    case = session.get(Case, finding.case_id)
    snapshot = session.get(TraceSnapshot, finding.snapshot_id)
    if case and snapshot:
        set_active_case(request, case, snapshot, finding)
    specs = finding_review_specs()
    review_items = [
        {
            "key": str(spec["key"]),
            "label": str(spec["label"]),
            "checked": bool(finding.review_checks.get(str(spec["key"]), False)),
        }
        for spec in specs
    ]
    return templates.TemplateResponse(
        request,
        "finding.html",
        {
            **template_context(request, user),
            "case": case,
            "snapshot": snapshot,
            "finding": finding,
            "review_items": review_items,
        },
    )


@app.post("/findings/{finding_id}/checks")
async def update_checks(
    finding_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    finding = session.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404)
    finding = ensure_finding_review_checks(session, finding)
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    keys = [str(spec["key"]) for spec in finding_review_specs()]
    submitted = {str(value) for value in form.getlist("review_check")}
    unknown = submitted.difference(keys)
    if unknown:
        raise HTTPException(400, f"Unknown pre-notice review check: {sorted(unknown)[0]}")
    finding.review_checks = {key: key in submitted for key in keys}
    session.add(finding)
    session.commit()
    append_audit_event(
        user["pis"],
        "finding.review_checks",
        str(finding_id),
        {"checks": finding.review_checks},
    )
    if form.get("action") == "prepare":
        if not all(finding.review_checks.values()):
            raise HTTPException(400, "All pre-notice review checks must pass first.")
        notice = prepare_notice(session, finding, user["pis"])
        append_audit_event(user["pis"], "notice.prepare", notice.notice_no)
        return RedirectResponse(f"/notices/{notice.id}", status_code=303)
    return RedirectResponse(f"/findings/{finding_id}", status_code=303)


@app.post("/findings/{finding_id}/notice")
def create_notice(
    finding_id: int,
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    require_csrf(request, csrf_token)
    finding = session.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404)
    finding = ensure_finding_review_checks(session, finding)
    if not all(finding.review_checks.values()):
        raise HTTPException(400, "All pre-notice review checks must pass first.")
    notice = prepare_notice(session, finding, user["pis"])
    append_audit_event(user["pis"], "notice.prepare", notice.notice_no)
    return RedirectResponse(f"/notices/{notice.id}", status_code=303)


@app.get("/notices")
def notices(
    request: Request,
    session: Session = Depends(get_session),
    _user: dict = Depends(session_user),
) -> Response:
    """Open the latest notice, or return to the finding gate when none exists yet."""
    active = active_case_from_session(request)
    latest_notice = None
    if active.get("id"):
        latest_notice = session.exec(
            select(Notice)
            .where(Notice.case_id == int(active["id"]))
            .order_by(Notice.created_ts_ms.desc(), Notice.id.desc())
        ).first()
    if not latest_notice:
        latest_notice = session.exec(
            select(Notice).order_by(Notice.created_ts_ms.desc(), Notice.id.desc())
        ).first()
    if latest_notice:
        return RedirectResponse(f"/notices/{latest_notice.id}", status_code=307)

    latest_finding = None
    if active.get("finding_id"):
        latest_finding = session.get(Finding, int(active["finding_id"]))
    if not latest_finding:
        latest_finding = session.exec(
            select(Finding).order_by(Finding.created_ts_ms.desc(), Finding.id.desc())
        ).first()
    if not latest_finding:
        case = seed_demo(session)
        _snapshot, latest_finding = get_or_create_trace(session, case)
    return RedirectResponse(f"/findings/{latest_finding.id}", status_code=307)


@app.get("/notices/{notice_id}", response_class=HTMLResponse)
def notice_view(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(404)
    case = session.get(Case, notice.case_id)
    finding = session.get(Finding, notice.finding_id)
    snapshot = session.get(TraceSnapshot, finding.snapshot_id)
    if case and finding and snapshot:
        set_active_case(request, case, snapshot, finding)
    vm = notice_view_model(case.model_dump(), snapshot.result_json, notice.model_dump())
    dispatches = session.exec(
        select(Dispatch).where(Dispatch.notice_id == notice.id).order_by(Dispatch.id)
    ).all()
    dispatch_by_channel = {
        logical_key: next(
            (
                row
                for row in dispatches
                if row.channel == channel["storage_key"]
                or (logical_key == "portal" and row.channel == "mock-le-portal")
            ),
            None,
        )
        for logical_key, channel in NOTICE_CHANNELS.items()
    }
    supervisor = demo_case()["officers"]["supervisor"]
    countersigner = (
        supervisor
        if notice.countersigned_by_pis == supervisor["pis"]
        else {"name": notice.countersigned_by_pis or "", "pis": notice.countersigned_by_pis or ""}
    )
    return templates.TemplateResponse(
        request,
        "notice.html",
        {
            **template_context(request, user),
            "case": case,
            "finding": finding,
            "snapshot": snapshot,
            "notice": notice,
            "vm": vm,
            "dispatch_by_channel": dispatch_by_channel,
            "countersigner": countersigner,
        },
    )


@app.post("/notices/{notice_id}/countersign-request")
def request_notice_countersign(
    notice_id: int,
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    require_csrf(request, csrf_token)
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(404)
    if user["role"] != "admin" and user["pis"] != notice.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may request countersignature.")
    if notice.status == "awaiting_countersignature":
        return RedirectResponse(f"/notices/{notice.id}", status_code=303)
    if notice.status != "draft" or notice.countersigned_by_pis:
        raise HTTPException(409, "This notice is not awaiting a countersignature request.")
    notice.status = "awaiting_countersignature"
    case = session.get(Case, notice.case_id)
    if case:
        case.stage = CaseStage.awaiting_countersignature
        case.updated_ts_ms = now_ms()
        session.add(case)
    session.add(notice)
    session.commit()
    append_audit_event(
        user["pis"],
        "notice.countersign_request",
        notice.notice_no,
        {"requested_supervisor_pis": demo_case()["officers"]["supervisor"]["pis"]},
    )
    return RedirectResponse(f"/notices/{notice.id}", status_code=303)


@app.post("/notices/{notice_id}/countersign")
def countersign_notice(
    notice_id: int,
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    require_csrf(request, csrf_token)
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(404)
    if user["role"] != "supervisor":
        raise HTTPException(403, "Only a supervisor may countersign this notice.")
    if notice.created_by_pis == user["pis"]:
        raise HTTPException(403, "Self-signing is not allowed.")
    if notice.countersigned_by_pis:
        return RedirectResponse(f"/notices/{notice.id}", status_code=303)
    if notice.status != "awaiting_countersignature":
        raise HTTPException(409, "Countersignature must be requested by the drafting officer first.")
    notice.countersigned_by_pis = user["pis"]
    notice.countersigned_ts_ms = now_ms()
    notice.status = "countersigned"
    case = session.get(Case, notice.case_id)
    if case:
        case.stage = CaseStage.countersigned
        case.updated_ts_ms = notice.countersigned_ts_ms
        session.add(case)
    session.add(notice)
    session.commit()
    append_audit_event(user["pis"], "notice.countersign", notice.notice_no)
    return RedirectResponse(f"/notices/{notice.id}", status_code=303)


@app.post("/notices/{notice_id}/dispatch")
async def dispatch_notice(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(404)
    if user["role"] != "admin" and user["pis"] != notice.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may dispatch this notice.")
    if notice.status == "dispatched":
        raise HTTPException(409, "This notice has already been dispatched.")
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    channel_keys = [str(value) for value in form.getlist("channel")]
    if not channel_keys or len(channel_keys) != len(set(channel_keys)):
        raise HTTPException(422, "Select one or more unique dispatch channels.")
    unknown_channels = set(channel_keys).difference(NOTICE_CHANNELS)
    if unknown_channels:
        raise HTTPException(422, f"Unknown dispatch channel: {sorted(unknown_channels)[0]}")
    try:
        deadline_hours = int(str(form.get("deadline_hours", notice.deadline_hours)))
    except ValueError as exc:
        raise HTTPException(422, "Invalid response deadline.") from exc
    if deadline_hours not in NOTICE_DEADLINES:
        raise HTTPException(422, "Invalid response deadline.")
    finding = session.get(Finding, notice.finding_id)
    if (
        finding
        and finding.amount_credited_base
        and finding.amount_credited_base >= demo_case()["notice"]["threshold_countersign_base"]
        and (not notice.countersigned_by_pis or notice.status != "countersigned")
    ):
        raise HTTPException(409, "Supervisor countersignature is required before dispatch.")
    now = now_ms()
    payload = json.dumps(
        {
            "notice_no": notice.notice_no,
            "ts_ms": now,
            "deadline_hours": deadline_hours,
            "channels": channel_keys,
        },
        sort_keys=True,
    ).encode("utf-8")
    notice.pdf_sha256 = sha256_bytes(payload)
    notice.status = "dispatched"
    notice.deadline_hours = deadline_hours
    for channel_key in channel_keys:
        channel = NOTICE_CHANNELS[channel_key]
        failed = channel_key == "nodal-copy"
        session.add(
            Dispatch(
                notice_id=notice.id,
                channel=channel["storage_key"],
                target=channel["target"],
                status="failed" if failed else "sent",
                attempts=1,
                last_error="Fixture nodal-copy delivery unavailable" if failed else None,
                created_ts_ms=now,
                updated_ts_ms=now,
            )
        )
    session.add(notice)
    case = session.get(Case, notice.case_id)
    if case:
        case.stage = CaseStage.notice_out
        case.updated_ts_ms = now
        session.add(case)
    session.commit()
    append_audit_event(
        user["pis"],
        "notice.dispatch",
        notice.notice_no,
        {"channels": channel_keys, "deadline_hours": deadline_hours},
    )
    return RedirectResponse(f"/notices/{notice.id}", status_code=303)


@app.get("/risk-check", response_class=HTMLResponse)
def risk_page(request: Request, user: dict = Depends(session_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "risk.html",
        {
            **template_context(request, user),
            **risk_page_data(),
            "result": None,
            "address": "",
            "risk_error": None,
        },
    )


@app.get("/integrations", response_class=HTMLResponse)
def integrations_page(request: Request, user: dict = Depends(session_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "integrations.html",
        {
            **template_context(request, user),
            "integration_status": integration_status(),
        },
    )


@app.post("/risk-check", response_class=HTMLResponse)
def run_risk_page(
    request: Request,
    address: Annotated[str, Form()],
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    normalized_address = address.strip()
    result = (
        risk_check(normalized_address, notice_state=risk_notice_state(session))
        if len(normalized_address) >= 26
        else None
    )
    risk_error = None if result else "That does not look like a full address. Paste the complete recipient address."
    if result:
        append_audit_event(user["pis"], "risk.check", normalized_address, {"band": result["band"]})
    return templates.TemplateResponse(
        request,
        "risk.html",
        {
            **template_context(request, user),
            **risk_page_data(),
            "result": result,
            "address": normalized_address,
            "risk_error": risk_error,
        },
    )


@app.get("/api/chains/resolve")
def api_chain_resolve(seed: str = Query(...)) -> dict:
    return {"seed": seed, "family": detect_chain(seed), "activity": [item.__dict__ for item in resolve_chain_activity(seed)]}


@app.post("/api/cases/{case_id}/traces")
def api_create_trace(case_id: int, session: Session = Depends(get_session)) -> dict:
    case = session.get(Case, case_id)
    if not case:
        raise HTTPException(404)
    snapshot, finding = get_or_create_trace(session, case)
    return {"snapshot_id": snapshot.id, "finding_id": finding.id, "sha256": snapshot.sha256}


@app.post("/api/traces/{snapshot_id}/continuations")
def api_trace_continuation(snapshot_id: int, payload: dict, session: Session = Depends(get_session)) -> dict:
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404)
    return {
        "status": "candidate_recorded",
        "source_snapshot_id": snapshot_id,
        "candidate": payload,
        "note": "Continuation snapshots preserve the source-chain evidence boundary.",
    }


@app.get("/api/cases/{case_id}/graph")
def api_case_graph(case_id: int, view: str = "trace", session: Session = Depends(get_session)) -> dict:
    snapshot = session.exec(select(TraceSnapshot).where(TraceSnapshot.case_id == case_id).order_by(TraceSnapshot.id.desc())).first()
    if not snapshot:
        raise HTTPException(404)
    return {"view": view, "snapshot_id": snapshot.id, "nodes": snapshot.result_json["hops"], "terminal": snapshot.result_json["terminal"]}


@app.get("/api/cases/{case_id}/evidence-manifest")
def api_case_evidence_manifest(
    case_id: int,
    session: Session = Depends(get_session),
    _user: dict = Depends(session_user),
) -> dict:
    case = session.get(Case, case_id)
    if not case:
        raise HTTPException(404)
    snapshot = session.exec(
        select(TraceSnapshot).where(TraceSnapshot.case_id == case.id).order_by(TraceSnapshot.id.desc())
    ).first()
    finding = (
        session.exec(select(Finding).where(Finding.snapshot_id == snapshot.id)).first()
        if snapshot
        else None
    )
    notice = (
        session.exec(select(Notice).where(Notice.case_id == case.id).order_by(Notice.created_ts_ms.desc())).first()
        if finding
        else None
    )
    dispatches = (
        session.exec(select(Dispatch).where(Dispatch.notice_id == notice.id).order_by(Dispatch.id)).all()
        if notice
        else []
    )
    return evidence_manifest(case, snapshot, finding, notice, dispatches)


@app.get("/api/cases/{case_id}/evidence-bundle.zip")
def api_case_evidence_bundle(
    case_id: int,
    session: Session = Depends(get_session),
    _user: dict = Depends(session_user),
) -> Response:
    case = session.get(Case, case_id)
    if not case:
        raise HTTPException(404)
    snapshot = session.exec(
        select(TraceSnapshot).where(TraceSnapshot.case_id == case.id).order_by(TraceSnapshot.id.desc())
    ).first()
    finding = (
        session.exec(select(Finding).where(Finding.snapshot_id == snapshot.id)).first()
        if snapshot
        else None
    )
    notice = (
        session.exec(select(Notice).where(Notice.case_id == case.id).order_by(Notice.created_ts_ms.desc())).first()
        if finding
        else None
    )
    dispatches = (
        session.exec(select(Dispatch).where(Dispatch.notice_id == notice.id).order_by(Dispatch.id)).all()
        if notice
        else []
    )
    filename = case.ack_no.replace("/", "-") + "-evidence-bundle.zip"
    return Response(
        evidence_bundle_bytes(case, snapshot, finding, notice, dispatches),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/search")
def api_search(q: str, limit: int = 10) -> dict:
    return {"results": search_records(q, limit)}


@app.get("/api/integrations/status")
def api_integrations_status(_user: dict = Depends(session_user)) -> dict:
    return integration_status()


@app.post("/api/risk-check")
def api_risk_check(payload: dict, session: Session = Depends(get_session)) -> dict:
    return risk_check(payload.get("address", ""), notice_state=risk_notice_state(session))


@app.get("/api/notices/{notice_id}/sahyog-export")
def api_sahyog_export(notice_id: int, session: Session = Depends(get_session)) -> dict:
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(404)
    finding = session.get(Finding, notice.finding_id)
    snapshot = session.get(TraceSnapshot, finding.snapshot_id)
    return sahyog_manifest(snapshot.result_json, notice.notice_no)


@app.get("/api/webauthn/register/options")
def webauthn_options(request: Request, user: dict = Depends(session_user)) -> dict:
    return registration_options(user["pis"], str(request.base_url))


@app.post("/api/webauthn/register/verify")
def webauthn_verify(payload: dict, _user: dict = Depends(session_user)) -> dict:
    return verify_registration(payload)


@app.get("/api/exhibits/{snapshot_id}.svg")
def exhibit_svg(snapshot_id: int, session: Session = Depends(get_session)) -> Response:
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404)
    return Response(snapshot_svg(snapshot.result_json), media_type="image/svg+xml")


@app.get("/healthz")
def healthz() -> dict:
    status = integration_status()
    return {
        "ok": True,
        "mode": settings.mode,
        "browser_assets": "local",
        "legal_dispatch": "approved" if settings.legal_copy_approved else "fixture_only",
        "integrations": {
            group["key"]: group["status"]
            for group in status["groups"]
        },
    }


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False, workers=1)


if __name__ == "__main__":
    main()
