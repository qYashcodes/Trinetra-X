from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

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
    ensure_finding_review_checks,
    finding_review_specs,
    get_or_create_trace,
    prepare_notice,
    seed_demo,
)
from app.services.audit import append_audit_event
from app.services.demo import demo_case
from app.services.exhibit import snapshot_svg
from app.services.hash import sha256_bytes
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


def template_context(request: Request, user: dict | None = None) -> dict:
    return {
        "request": request,
        "user": user,
        "settings": settings,
        "health": {"ok": True, "label": "Fixture systems operational"},
        "source_health": complaints.health(),
        "demo": demo_case(),
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
    case = seed_demo(session)
    snapshot, finding = get_or_create_trace(session, case)
    notice = session.exec(select(Notice).where(Notice.case_id == case.id)).first()
    ctx = {
        **template_context(request, user),
        "case": case,
        "cases": [case],
        "snapshot": snapshot,
        "finding": finding,
        "notice": notice,
        "docket": docket_fixture(),
    }
    return templates.TemplateResponse(request, "docket.html", ctx)


@app.get("/cases/new", response_class=HTMLResponse)
def new_case(request: Request, user: dict = Depends(session_user)) -> HTMLResponse:
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
    user: dict = Depends(session_user),
) -> HTMLResponse:
    normalized_ack = ack_no.strip()
    record = complaints.fetch(normalized_ack) if normalized_ack else None
    chain = detect_chain(record["reported_address"]) if record else None
    particulars, completed, intake_state = build_intake_particulars(
        record,
        chain,
        attempted=bool(normalized_ack),
    )
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
    ack_no: Annotated[str, Form()],
    reviewed: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    if not reviewed:
        raise HTTPException(400, "Imported particulars must be reviewed before tracing.")
    case = seed_demo(session)
    snapshot, _finding = get_or_create_trace(session, case)
    append_audit_event(user["pis"], "trace.start", case.ack_no, {"snapshot_id": snapshot.id})
    return RedirectResponse(f"/traces/{snapshot.id}", status_code=303)


@app.get("/traces", response_class=HTMLResponse)
def traces(request: Request, session: Session = Depends(get_session), user: dict = Depends(session_user)) -> Response:
    latest = session.exec(select(TraceSnapshot).order_by(TraceSnapshot.id.desc())).first()
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
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
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
    session: Session = Depends(get_session),
    _user: dict = Depends(session_user),
) -> Response:
    """Open the latest notice, or return to the finding gate when none exists yet."""
    latest_notice = session.exec(
        select(Notice).order_by(Notice.created_ts_ms.desc(), Notice.id.desc())
    ).first()
    if latest_notice:
        return RedirectResponse(f"/notices/{latest_notice.id}", status_code=307)

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
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
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
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
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


@app.get("/api/search")
def api_search(q: str, limit: int = 10) -> dict:
    return {"results": search_records(q, limit)}


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
    return {
        "ok": True,
        "mode": settings.mode,
        "browser_assets": "local",
        "legal_dispatch": "approved" if settings.legal_copy_approved else "fixture_only",
    }


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False, workers=1)


if __name__ == "__main__":
    main()
