from __future__ import annotations

import json
import re
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

from app.db import engine, get_session, init_db
from app.docket_fixture import docket_fixture
from app.engine_bridge import (
    ChainRef,
    TraceParams,
    TraceSeed,
    detect_chain,
    resolve_chain_activity,
    run_trace,
    trace_params_from_json,
)
from app.integrations.complaints.fixture import FixtureComplaintSource
from app.models import (
    CanonicalTraceEvent,
    Case,
    CaseStage,
    Dispatch,
    Finding,
    FrontierItem,
    Notice,
    OfficerRole,
    OfficerSession,
    SourceCoverage,
    TraceEvent,
    TraceSnapshot,
)
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
from app.services.audit import append_audit_event, read_audit_events, verify_audit_chain
from app.services.demo import demo_case
from app.services.exhibit import snapshot_svg
from app.services.explainability import (
    methodology_annex,
    methodology_annex_text,
    trace_explainability,
)
from app.services.hash import sha256_bytes, sha256_json
from app.services.integrations import integration_status
from app.services.feature_flags import feature_flags
from app.services.live_trace import (
    LiveTraceInputError,
    parse_amount_base,
    parse_ist_timestamp,
    parse_trace_params,
    snapshot_runtime_status,
)
from app.services.money import format_amount, format_millions
from app.services.notices import notice_view_model, sahyog_manifest
from app.services.risk import live_risk_check, risk_check, risk_page_data
from app.services.search import search_records
from app.services.sessions import (
    active_officer_sessions,
    create_officer_session,
    end_officer_session,
    officer_identity,
    record_session_activity,
    resolve_officer_session,
    revoke_all_officer_sessions,
    signout_summary,
)
from app.services.time import format_ist, now_ms
from app.services.worker import SupervisedFrontierWorker
from app.settings import ROOT_DIR, settings


def require_session_secret(mode: str, configured_secret: str | None) -> None:
    if configured_secret:
        return
    if mode == "fixture":
        return
    raise RuntimeError(
        "SESSION_SECRET is required outside fixture mode; refusing to start with persistent "
        "login sessions disabled."
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    require_session_secret(settings.mode, settings.session_secret)
    init_db()
    frontier_worker = SupervisedFrontierWorker.from_environment(engine)
    _app.state.frontier_worker = frontier_worker
    frontier_worker.start()
    try:
        yield
    finally:
        frontier_worker.stop()


app = FastAPI(title="TRINETRA", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or secrets.token_urlsafe(48),
    same_site="lax",
    https_only=settings.session_cookie_secure,
    max_age=settings.session_idle_timeout_seconds + 300,
)
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


def artifact_identity(
    user: dict,
    generated_ts_ms: int,
    audit_row: dict | None,
) -> dict:
    audit_row = audit_row or {}
    demonstration = user.get("authentication_kind") == "prototype_demonstration"
    return {
        "generated_ts_ms": generated_ts_ms,
        "generated_by": {
            "name": user.get("name"),
            "service_id": user.get("pis"),
            "unit": user.get("desk"),
            "role": user.get("role"),
        },
        "authentication_kind": user.get("authentication_kind"),
        "artifact_posture": (
            "demonstration_generated" if demonstration else "authenticated_export"
        ),
        "audit_chain_reference": {
            "action": audit_row.get("action"),
            "ts_ms": audit_row.get("ts_ms"),
            "row_hash": audit_row.get("row_hash"),
        },
    }


def snapshot_explanation(session: Session, snapshot: TraceSnapshot) -> dict:
    canonical_events = session.exec(
        select(CanonicalTraceEvent)
        .where(CanonicalTraceEvent.snapshot_id == snapshot.id)
        .order_by(CanonicalTraceEvent.tx_index, CanonicalTraceEvent.event_index, CanonicalTraceEvent.id)
    ).all()
    frontier_items = session.exec(
        select(FrontierItem)
        .where(FrontierItem.snapshot_id == snapshot.id)
        .order_by(FrontierItem.depth, FrontierItem.priority_base.desc(), FrontierItem.id)
    ).all()
    source_coverage = session.exec(
        select(SourceCoverage)
        .where(SourceCoverage.snapshot_id == snapshot.id)
        .order_by(SourceCoverage.retrieval_ts_ms, SourceCoverage.id)
    ).all()
    return trace_explainability(
        snapshot.result_json,
        canonical_events=canonical_events,
        frontier_items=frontier_items,
        source_coverage=source_coverage,
    )


def evidence_manifest(
    case: Case,
    snapshot: TraceSnapshot | None,
    finding: Finding | None,
    notice: Notice | None,
    dispatches: list[Dispatch],
    artifact: dict | None = None,
) -> dict:
    graph_svg = snapshot_svg(snapshot.result_json).encode("utf-8") if snapshot else b""
    annex = methodology_annex(snapshot.result_json) if snapshot else None
    return {
        "schema": "trinetra.evidence_manifest/1",
        "artifact": artifact,
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
            "trace_mode": snapshot.result_json.get("engine", {}).get("mode"),
            "params": snapshot.result_json.get("params"),
            "data_freshness": snapshot.result_json.get("data_freshness"),
            "provider_evidence": snapshot.result_json.get("provider_evidence", []),
            "unconfirmed_observations": {
                "count": len(snapshot.result_json.get("unconfirmed_observations") or []),
                "warning": (
                    "Observation only; excluded from attribution, canonical evidence and "
                    "terminal decisions."
                ),
            },
            "parent_snapshot_id": snapshot.parent_snapshot_id,
            "superseded_by_id": snapshot.superseded_by_id,
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
        "methodology_annex": annex,
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
    artifact: dict | None = None,
) -> bytes:
    manifest = evidence_manifest(
        case,
        snapshot,
        finding,
        dispatches=dispatches,
        notice=notice,
        artifact=artifact,
    )
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
                    f"Trace mode: {snapshot.result_json.get('engine', {}).get('mode', 'none') if snapshot else 'none'}",
                    f"Chain data as of (UTC ms): {snapshot.result_json.get('data_freshness', {}).get('chain_data_as_of_ms') if snapshot else 'not available'}",
                    "Purpose: offline investigative-aid export for review.",
                    "Methodology: methodology_annex.json and methodology_annex.txt are included when a trace snapshot exists.",
                    "Legal posture: specimen prototype; not for live dispatch unless configured and approved.",
                    "No offence finding is made by this bundle.",
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
            archive.writestr(
                "methodology_annex.json",
                json.dumps(methodology_annex(snapshot.result_json), indent=2, sort_keys=True),
            )
            archive.writestr(
                "methodology_annex.txt",
                methodology_annex_text(snapshot.result_json),
            )
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


def session_user(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    row = resolve_officer_session(session, request.session.get("session_token"))
    if row is None:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    request.state.officer_session = row
    user = officer_identity(row)
    request.session["user"] = user
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


def set_active_case(
    request: Request,
    case: Case,
    snapshot: TraceSnapshot | None = None,
    finding: Finding | None = None,
    session: Session | None = None,
) -> None:
    request.session["active_case"] = {
        "id": case.id,
        "ack_no": case.ack_no,
        "snapshot_id": snapshot.id if snapshot else None,
        "finding_id": finding.id if finding else None,
    }
    officer_session = getattr(request.state, "officer_session", None)
    if session is not None and officer_session is not None and case.id is not None:
        record_session_activity(session, officer_session, case_id=case.id)


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
        "session_idle": {
            "obscure_seconds": settings.session_idle_obscure_seconds,
            "warning_seconds": settings.session_idle_warning_seconds,
            "timeout_seconds": settings.session_idle_timeout_seconds,
        },
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
def prototype_login(
    request: Request,
    role: Annotated[str, Form()] = "io",
    session: Session = Depends(get_session),
) -> Response:
    if role not in {OfficerRole.io.value, OfficerRole.supervisor.value}:
        raise HTTPException(400, "Unknown demonstration account role.")
    selected_role = OfficerRole(role)
    officer = demo_case()["officers"][role]
    request.session.clear()
    officer_session, raw_token = create_officer_session(
        session,
        officer,
        selected_role,
        request.headers.get("user-agent"),
    )
    user = officer_identity(officer_session)
    request.session["user"] = user
    request.session["session_token"] = raw_token
    append_audit_event(
        officer["pis"],
        "login.prototype",
        f"session:{officer_session.id}",
        {"role": role, "authentication_kind": "prototype_demonstration"},
    )
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
def logout(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    reason: Annotated[str, Form()] = "officer_signout",
    session: Session = Depends(get_session),
) -> Response:
    require_csrf(request, csrf_token)
    row = end_officer_session(
        session,
        request.session.get("session_token"),
        reason="idle_timeout" if reason == "idle_timeout" else "officer_signout",
    )
    if row is not None:
        append_audit_event(
            row.officer_pis,
            "session.signout",
            f"session:{row.id}",
            signout_summary(row),
        )
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.post("/auth/session/extend")
def extend_session(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    _user: dict = Depends(session_user),
) -> dict:
    require_csrf(request, csrf_token)
    row: OfficerSession = request.state.officer_session
    return {"status": "extended", "expires_ts_ms": row.expires_ts_ms}


@app.get("/sessions", response_class=HTMLResponse)
def sessions_page(
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    rows = active_officer_sessions(session, user["pis"])
    current: OfficerSession = request.state.officer_session
    return templates.TemplateResponse(
        request,
        "sessions.html",
        {
            **template_context(request, user),
            "sessions": rows,
            "current_session_id": current.id,
        },
    )


@app.post("/auth/logout-all")
def logout_all_sessions(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    require_csrf(request, csrf_token)
    rows = revoke_all_officer_sessions(
        session,
        user["pis"],
        reason="officer_signout_all",
    )
    append_audit_event(
        user["pis"],
        "session.signout_all",
        f"officer:{user['pis']}",
        {"session_ids": [row.id for row in rows], "session_count": len(rows)},
    )
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/docket", response_class=HTMLResponse)
def docket(request: Request, session: Session = Depends(get_session), user: dict = Depends(session_user)) -> HTMLResponse:
    case = get_active_case(request, session) or seed_demo(session)
    trace_mode = "fixture" if complaints.fetch(case.ack_no) else "auto"
    snapshot, finding = get_or_create_trace(session, case, trace_mode=trace_mode)
    set_active_case(request, case, snapshot, finding, session)
    notice = session.exec(select(Notice).where(Notice.case_id == case.id)).first()
    dispatches = (
        session.exec(select(Dispatch).where(Dispatch.notice_id == notice.id).order_by(Dispatch.id)).all()
        if notice
        else []
    )
    snapshot_history = session.exec(
        select(TraceSnapshot)
        .where(TraceSnapshot.case_id == case.id)
        .order_by(TraceSnapshot.id.desc())
    ).all()
    retrace_audit = read_audit_events(subject=case.ack_no, actions={"trace.retrace"})
    ctx = {
        **template_context(request, user),
        "case": case,
        "cases": [case],
        "snapshot": snapshot,
        "finding": finding,
        "notice": notice,
        "case_trail": case_trail(case, snapshot, finding, notice, dispatches),
        "docket": docket_fixture(case),
        "snapshot_history": snapshot_history,
        "retrace_audit": retrace_audit,
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


def _live_intake_response(
    request: Request,
    user: dict,
    *,
    values: dict | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    live_flag = feature_flags()["live_tron_trace"]
    return templates.TemplateResponse(
        request,
        "live_intake.html",
        {
            **template_context(request, user),
            "live_flag": live_flag,
            "values": values or {},
            "intake_error": error,
        },
        status_code=status_code,
    )


@app.get("/cases/live/new", response_class=HTMLResponse)
def new_live_case(request: Request, user: dict = Depends(session_user)) -> HTMLResponse:
    draft = request.session.get("risk_case_draft") or {}
    values = (
        {
            "seed_kind": "address",
            "seed_value": str(draft.get("address") or ""),
            "case_reference": "",
        }
        if draft.get("address")
        else None
    )
    return _live_intake_response(request, user, values=values)


@app.post("/cases/live/start", response_class=HTMLResponse)
def start_live_case(
    request: Request,
    seed_kind: Annotated[str, Form()],
    seed_value: Annotated[str, Form()],
    amount: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
    recipient_address: Annotated[str | None, Form()] = None,
    payment_ts_ist: Annotated[str | None, Form()] = None,
    case_reference: Annotated[str | None, Form()] = None,
    max_depth: Annotated[str, Form()] = "5",
    time_window_hours: Annotated[str, Form()] = "8",
    value_floor_share: Annotated[str, Form()] = "0.02",
    breadth_cap: Annotated[str, Form()] = "3",
    address_budget: Annotated[str, Form()] = "60",
    strategy: Annotated[str, Form()] = "dominant_fund_flow",
    include_unconfirmed: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    require_csrf(request, csrf_token)
    values = {
        "seed_kind": seed_kind,
        "seed_value": seed_value,
        "recipient_address": recipient_address or "",
        "amount": amount,
        "payment_ts_ist": payment_ts_ist or "",
        "case_reference": case_reference or "",
        "max_depth": max_depth,
        "time_window_hours": time_window_hours,
        "value_floor_share": value_floor_share,
        "breadth_cap": breadth_cap,
        "address_budget": address_budget,
        "strategy": strategy,
        "include_unconfirmed": include_unconfirmed,
    }
    live_flag = feature_flags()["live_tron_trace"]
    if not live_flag["enabled"]:
        missing = ", ".join(str(name) for name in live_flag.get("missing_gates") or [])
        detail = f" Missing: {missing}." if missing else ""
        return _live_intake_response(
            request,
            user,
            values=values,
            error=f"Live TRON tracing is unavailable.{detail}",
            status_code=409,
        )
    try:
        if seed_kind not in {"address", "txid"}:
            raise LiveTraceInputError("Select address or transaction hash as the seed type.")
        normalized_seed = seed_value.strip()
        normalized_recipient = (recipient_address or "").strip()
        if seed_kind == "txid":
            if not re.fullmatch(r"[a-fA-F0-9]{64}", normalized_seed):
                raise LiveTraceInputError("Transaction hash must be 64 hexadecimal characters.")
            if not normalized_recipient:
                raise LiveTraceInputError("A transaction-hash seed requires its recipient address.")
            payment_txid = normalized_seed
            reported_address = normalized_recipient
        else:
            payment_txid = None
            reported_address = normalized_seed
        family = detect_chain(reported_address)
        if family != "TRON":
            raise LiveTraceInputError(
                f"{family} live tracing is unavailable; this stage supports TRON mainnet only."
            )
        amount_base = parse_amount_base(amount)
        payment_ts_ms = parse_ist_timestamp(payment_ts_ist)
        if seed_kind == "address" and payment_ts_ms is None:
            raise LiveTraceInputError(
                "An address seed requires the confirmed payment date and time in IST."
            )
        params = parse_trace_params(values)
        ack_no = (case_reference or "").strip()
        if not ack_no:
            ack_no = f"LIVE/{now_ms()}/{secrets.token_hex(4).upper()}"
        if len(ack_no) > 96:
            raise LiveTraceInputError("Case reference must be 96 characters or fewer.")
        if session.exec(select(Case).where(Case.ack_no == ack_no)).first():
            raise LiveTraceInputError("That case reference is already in use.")
    except LiveTraceInputError as exc:
        return _live_intake_response(
            request,
            user,
            values=values,
            error=str(exc),
            status_code=422,
        )

    ts = now_ms()
    case = case_from_complaint(
        session,
        {
            "ack_no": ack_no,
            "category": "live blockchain trace",
            "jurisdiction": user["desk"],
            "filed_ts_ms": ts,
            "amount_reported_base": amount_base,
            "asset": {
                "symbol": "USDT",
                "contract": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                "decimals": 6,
            },
            "chain": {"family": "TRON", "network": "mainnet"},
            "reported_address": reported_address,
            "payment_txid": payment_txid,
            "victim_payment_ts_ms": payment_ts_ms,
            "complainant_contact_redacted": None,
        },
    )
    snapshot, finding = get_or_create_trace(
        session,
        case,
        trace_mode="live",
        params=params,
    )
    request.session.pop("risk_case_draft", None)
    set_active_case(request, case, snapshot, finding, session)
    record_session_activity(session, request.state.officer_session, trace_run=True)
    append_audit_event(
        user["pis"],
        "trace.start",
        case.ack_no,
        {
            "snapshot_id": snapshot.id,
            "trace_mode": "live",
            "seed_kind": seed_kind,
            "params": snapshot.result_json.get("params"),
        },
    )
    return RedirectResponse(f"/traces/{snapshot.id}", status_code=303)


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
    snapshot, finding = get_or_create_trace(session, case, trace_mode="fixture")
    set_active_case(request, case, snapshot, finding, session)
    record_session_activity(session, request.state.officer_session, trace_run=True)
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
        latest, _finding = get_or_create_trace(session, case, trace_mode="fixture")
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
    if case:
        set_active_case(request, case, snapshot, finding, session)
    events = session.exec(select(TraceEvent).where(TraceEvent.snapshot_id == snapshot.id).order_by(TraceEvent.seq)).all()
    runtime_status = snapshot_runtime_status(session, snapshot)
    template_name = (
        "live_trace.html"
        if snapshot.result_json.get("engine", {}).get("mode") == "live"
        else "trace.html"
    )
    return templates.TemplateResponse(
        request,
        template_name,
        {
            **template_context(request, user),
            "case": case,
            "snapshot": snapshot,
            "finding": finding,
            "events": events,
            "runtime_status": runtime_status,
            "explainability": snapshot_explanation(session, snapshot),
        },
    )


@app.post("/traces/{snapshot_id}/retrace")
def retrace_snapshot(
    snapshot_id: int,
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    require_csrf(request, csrf_token)
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404)
    case = session.get(Case, snapshot.case_id)
    if not case:
        raise HTTPException(404)
    mode = str(snapshot.result_json.get("engine", {}).get("mode") or "fixture")
    if mode == "live" and not feature_flags()["live_tron_trace"]["enabled"]:
        raise HTTPException(409, "Live re-tracing is unavailable because its provider gates are unmet.")
    params = trace_params_from_json(snapshot.result_json.get("params"), trace_mode=mode)
    prior_exports = read_audit_events(
        subject=case.ack_no,
        actions={"artifact.export_manifest", "artifact.export_bundle"},
    )
    latest, finding = get_or_create_trace(
        session,
        case,
        trace_mode=mode,
        params=params,
        force_new=True,
    )
    set_active_case(request, case, latest, finding, session)
    record_session_activity(
        session,
        request.state.officer_session,
        case_id=case.id,
        trace_run=True,
    )
    append_audit_event(
        user["pis"],
        "trace.retrace",
        case.ack_no,
        {
            "previous_snapshot_id": snapshot.id,
            "snapshot_id": latest.id,
            "trace_mode": mode,
            "prior_export_count": len(prior_exports),
        },
    )
    return RedirectResponse(f"/traces/{latest.id}", status_code=303)


@app.get("/api/traces/{snapshot_id}/status")
def trace_runtime_status(
    snapshot_id: int,
    session: Session = Depends(get_session),
    _user: dict = Depends(session_user),
) -> dict:
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404)
    return snapshot_runtime_status(session, snapshot)


@app.get("/api/traces/{snapshot_id}/explanations")
def trace_explanations(
    snapshot_id: int,
    session: Session = Depends(get_session),
    _user: dict = Depends(session_user),
) -> dict:
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404)
    return snapshot_explanation(session, snapshot)


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
    if snap and snap.case_id != case.id:
        raise HTTPException(404)
    if not snap:
        snap = session.exec(select(TraceSnapshot).where(TraceSnapshot.case_id == case_id).order_by(TraceSnapshot.id.desc())).first()
    finding = session.exec(select(Finding).where(Finding.snapshot_id == snap.id)).first() if snap else None
    set_active_case(request, case, snap, finding, session)
    template_name = (
        "live_canvas.html"
        if snap and snap.result_json.get("engine", {}).get("mode") == "live"
        else "canvas.html"
    )
    return templates.TemplateResponse(
        request,
        template_name,
        {
            **template_context(request, user),
            "case": case,
            "snapshot": snap,
            "view": view,
            "runtime_status": snapshot_runtime_status(session, snap) if snap else None,
            "explainability": snapshot_explanation(session, snap) if snap else None,
        },
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
        set_active_case(request, case, snapshot, finding, session)
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
    elif active.get("id"):
        active_snapshot = (
            session.get(TraceSnapshot, int(active["snapshot_id"]))
            if active.get("snapshot_id")
            else session.exec(
                select(TraceSnapshot)
                .where(TraceSnapshot.case_id == int(active["id"]))
                .order_by(TraceSnapshot.id.desc())
            ).first()
        )
        if active_snapshot:
            return RedirectResponse(f"/traces/{active_snapshot.id}", status_code=307)
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
        set_active_case(request, case, snapshot, finding, session)
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
            "methodology_annex": methodology_annex(snapshot.result_json),
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


def _risk_case_context(session: Session, address: str) -> tuple[list[dict], set[str]]:
    normalized = address.lower()
    cases = session.exec(select(Case).order_by(Case.updated_ts_ms.desc(), Case.id.desc())).all()
    canonical = session.exec(
        select(CanonicalTraceEvent).order_by(
            CanonicalTraceEvent.created_ts_ms.desc(),
            CanonicalTraceEvent.id.desc(),
        )
    ).all()
    refs_by_case: dict[int, list[str]] = {}
    for row in canonical:
        if (
            str(row.source_address or "").lower() == normalized
            or str(row.destination_address or "").lower() == normalized
        ):
            refs_by_case.setdefault(row.case_id, []).append(row.evidence_ref)
    records: list[dict] = []
    for case in cases:
        if case.id is None:
            continue
        evidence_refs = list(refs_by_case.get(case.id, []))
        if case.reported_address.lower() == normalized:
            evidence_refs.insert(0, f"case:{case.id}:reported_address")
        if not evidence_refs:
            continue
        stage = case.stage.value if hasattr(case.stage, "value") else str(case.stage)
        records.append(
            {
                "reference": case.ack_no,
                "amount": format_amount(
                    case.amount_reported_base,
                    case.asset_decimals,
                    case.asset_symbol,
                ),
                "stage": stage.replace("_", " ").title(),
                "href": f"/cases/{case.id}/canvas",
                "retrieval_ts_ms": case.updated_ts_ms,
                "provenance": "TRINETRA local case and canonical trace records",
                "evidence_refs": list(dict.fromkeys(evidence_refs)),
            }
        )
    reported = {case.reported_address for case in cases if case.reported_address}
    return records, reported


def _recent_risk_checks() -> list[dict]:
    rows = read_audit_events(actions={"risk.check"})[-4:]
    return [
        {
            "address": str(row.get("subject") or ""),
            "label": short_value(str(row.get("subject") or ""), 6, 4),
            "time": format_ist(row.get("ts_ms")),
            "tone": {
                "alert": "alert",
                "medium": "medium",
                "low": "success",
            }.get(str((row.get("data") or {}).get("band")), "muted"),
        }
        for row in reversed(rows)
    ]


def _run_risk_lookup(session: Session, address: str, mode: str) -> dict:
    if mode == "live":
        local_records, reported_addresses = _risk_case_context(session, address)
        return live_risk_check(
            address,
            evidence_root=settings.var_dir,
            local_records=local_records,
            reported_addresses=reported_addresses,
            lookback_days=max(1, int(getattr(settings, "risk_lookback_days", 30))),
        )
    return risk_check(address, notice_state=risk_notice_state(session))


def _record_risk_lookup(user: dict, result: dict) -> dict:
    feature_set = result.get("feature_set") or {}
    return append_audit_event(
        user["pis"],
        "risk.check",
        result["address"],
        {
            "mode": result.get("mode", "fixture"),
            "band": result["band"],
            "feature_revision": feature_set.get("schema"),
            "observed_event_count": feature_set.get("observed_event_count"),
            "calibration_status": result["calibration_status"],
            "lookup_ref": result.get("lookup_ref"),
            "result_sha256": sha256_json(result),
        },
    )


def _risk_template_context(request: Request, user: dict) -> dict:
    return {
        **template_context(request, user),
        **risk_page_data(_recent_risk_checks()),
        "live_risk_flag": feature_flags()["live_tron_provider"],
    }


@app.get("/risk-check", response_class=HTMLResponse)
def risk_page(request: Request, user: dict = Depends(session_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "risk.html",
        {
            **_risk_template_context(request, user),
            "result": None,
            "address": "",
            "risk_error": None,
            "risk_mode": "fixture",
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
    mode: Annotated[str, Form()] = "fixture",
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    normalized_address = address.strip()
    normalized_mode = mode if mode in {"fixture", "live"} else "fixture"
    risk_error = None
    result = None
    if len(normalized_address) < 26:
        risk_error = "That does not look like a full address. Paste the complete recipient address."
    elif normalized_mode == "live" and not feature_flags()["live_tron_provider"]["enabled"]:
        live_flag = feature_flags()["live_tron_provider"]
        missing = ", ".join(str(item) for item in live_flag.get("missing_gates") or [])
        risk_error = f"Live history is unavailable. Missing: {missing}."
        append_audit_event(
            user["pis"],
            "risk.check_blocked",
            normalized_address,
            {"mode": "live", "missing_gates": list(live_flag.get("missing_gates") or [])},
        )
    else:
        result = _run_risk_lookup(session, normalized_address, normalized_mode)
        _record_risk_lookup(user, result)
    return templates.TemplateResponse(
        request,
        "risk.html",
        {
            **_risk_template_context(request, user),
            "result": result,
            "address": normalized_address,
            "risk_error": risk_error,
            "risk_mode": normalized_mode,
        },
    )


@app.post("/risk-check/escalate")
def prepare_risk_case(
    request: Request,
    address: Annotated[str, Form()],
    mode: Annotated[str, Form()] = "fixture",
    csrf_token: Annotated[str | None, Form()] = None,
    user: dict = Depends(session_user),
) -> Response:
    require_csrf(request, csrf_token)
    normalized = address.strip()
    if detect_chain(normalized) != "TRON":
        raise HTTPException(409, "Only TRON addresses can enter the current live case intake.")
    source_mode = mode if mode in {"fixture", "live"} else "fixture"
    request.session["risk_case_draft"] = {
        "address": normalized,
        "source_mode": source_mode,
    }
    append_audit_event(
        user["pis"],
        "risk.prepare_case_intake",
        normalized,
        {"source_mode": source_mode, "case_created": False},
    )
    return RedirectResponse("/cases/live/new", status_code=303)


@app.get("/api/chains/resolve")
def api_chain_resolve(seed: str = Query(...)) -> dict:
    return {"seed": seed, "family": detect_chain(seed), "activity": [item.__dict__ for item in resolve_chain_activity(seed)]}


@app.post("/api/cases/{case_id}/traces")
def api_create_trace(
    case_id: int,
    request: Request,
    session: Session = Depends(get_session),
    _user: dict = Depends(session_user),
) -> dict:
    case = session.get(Case, case_id)
    if not case:
        raise HTTPException(404)
    snapshot, finding = get_or_create_trace(session, case)
    record_session_activity(
        session,
        request.state.officer_session,
        case_id=case_id,
        trace_run=True,
    )
    return {"snapshot_id": snapshot.id, "finding_id": finding.id if finding else None, "sha256": snapshot.sha256}


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
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
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
    generated = now_ms()
    audit_row = append_audit_event(
        user["pis"],
        "artifact.export_manifest",
        case.ack_no,
        {"case_id": case.id, "snapshot_id": snapshot.id if snapshot else None},
    )
    record_session_activity(
        session,
        request.state.officer_session,
        case_id=case.id,
        artifact_export=True,
    )
    return evidence_manifest(
        case,
        snapshot,
        finding,
        notice,
        dispatches,
        artifact_identity(user, generated, audit_row),
    )


@app.get("/api/cases/{case_id}/evidence-bundle.zip")
def api_case_evidence_bundle(
    case_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
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
    generated = now_ms()
    audit_row = append_audit_event(
        user["pis"],
        "artifact.export_bundle",
        case.ack_no,
        {"case_id": case.id, "snapshot_id": snapshot.id if snapshot else None},
    )
    record_session_activity(
        session,
        request.state.officer_session,
        case_id=case.id,
        artifact_export=True,
    )
    filename = case.ack_no.replace("/", "-") + "-evidence-bundle.zip"
    return Response(
        evidence_bundle_bytes(
            case,
            snapshot,
            finding,
            notice,
            dispatches,
            artifact_identity(user, generated, audit_row),
        ),
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
def api_risk_check(
    payload: dict,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    address = str(payload.get("address") or "").strip()
    mode = str(payload.get("mode") or "fixture")
    if len(address) < 26:
        raise HTTPException(422, "A complete recipient address is required.")
    if mode not in {"fixture", "live"}:
        raise HTTPException(422, "Mode must be fixture or live.")
    if mode == "live" and not feature_flags()["live_tron_provider"]["enabled"]:
        live_flag = feature_flags()["live_tron_provider"]
        missing_gates = list(live_flag.get("missing_gates") or [])
        append_audit_event(
            user["pis"],
            "risk.check_blocked",
            address,
            {"mode": "live", "missing_gates": missing_gates},
        )
        raise HTTPException(
            409,
            {
                "message": "Live history is unavailable.",
                "missing_gates": missing_gates,
            },
        )
    result = _run_risk_lookup(session, address, mode)
    _record_risk_lookup(user, result)
    return result


@app.get("/api/notices/{notice_id}/sahyog-export")
def api_sahyog_export(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(404)
    finding = session.get(Finding, notice.finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found for notice.")
    snapshot = session.get(TraceSnapshot, finding.snapshot_id)
    if not snapshot:
        raise HTTPException(404, "Trace snapshot not found for notice.")
    generated = now_ms()
    audit_row = append_audit_event(
        user["pis"],
        "artifact.export_sahyog_specimen",
        notice.notice_no,
        {"notice_id": notice.id, "snapshot_id": snapshot.id},
    )
    record_session_activity(
        session,
        request.state.officer_session,
        case_id=notice.case_id,
        artifact_export=True,
    )
    result = sahyog_manifest(snapshot.result_json, notice.notice_no)
    result["artifact"] = artifact_identity(user, generated, audit_row)
    return result


@app.get("/api/webauthn/register/options")
def webauthn_options(request: Request, user: dict = Depends(session_user)) -> dict:
    return registration_options(user["pis"], str(request.base_url))


@app.post("/api/webauthn/register/verify")
def webauthn_verify(
    payload: dict,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    result = verify_registration(payload)
    if result.get("verified") is True:
        rows = revoke_all_officer_sessions(
            session,
            user["pis"],
            reason="credential_change",
        )
        append_audit_event(
            user["pis"],
            "session.revoke_after_credential_change",
            f"officer:{user['pis']}",
            {"session_ids": [row.id for row in rows]},
        )
        request.session.clear()
    return result


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
