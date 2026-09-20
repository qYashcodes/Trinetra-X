from __future__ import annotations

import csv
import json
import re
import secrets
import zipfile
from contextlib import asynccontextmanager
from io import BytesIO, StringIO
from pathlib import Path
from typing import Annotated
from hmac import compare_digest

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

try:
    from sse_starlette.sse import EventSourceResponse
except Exception:  # pragma: no cover - import guard for partial installs
    EventSourceResponse = None

from engine.adapters import tron

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
    CaseAssignment,
    CaseStage,
    DispatchRecord,
    Dispatch,
    Finding,
    FrontierItem,
    Notice,
    NoticeDraft,
    NoticeVersion,
    NoticeTrackerEvent,
    OfficerRole,
    OfficerProfile,
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
    reset_demo_notice_workflow,
    seed_demo,
)
from app.services.audit import (
    append_audit_event,
    flush_audit_outbox,
    queue_audit_event,
    read_audit_events,
    verify_audit_chain,
)
from app.services.demo import demo_case
from app.services.docket import docket_state
from app.services.exhibit import snapshot_svg
from app.services.explainability import (
    methodology_annex,
    methodology_annex_text,
    trace_explainability,
)
from app.services.dispatch_tracker import (
    RESPONSE_SUB_OUTCOMES,
    TRACKER_STATUS_LABELS,
    normalize_manual_status,
    record_tracker_event,
    tracker_counts,
    tracker_rows,
)
from app.services.dispatch_workflow import (
    DispatchWorkflowError,
    commit_and_flush_audit,
    dispatch_sla_view,
    ensure_dispatch_record,
    escalate_dispatch,
    register_sla_breach,
    set_dispatch_stage,
    version_for_draft,
)
from app.services.graph_view import omega_graph_payload
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
from app.services.notice_workflow import (
    STATUTORY_OPTIONS,
    NoticeWorkflowError,
    active_attachments,
    active_version,
    add_attachment,
    attest_version,
    ensure_notice_draft,
    generate_version,
    record_verification,
    render_version_pdf,
    update_notice_parameters,
    verification_state,
    version_diff,
)
from app.services.officers import active_officer_profiles, can_access_case, visible_cases
from app.services.role_views import role_view_registry
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
from app.services.strategy import engine_strategies, strategy_analysis, strategy_delta
from app.services.time import format_ist, now_ms
from app.services.work_context import read_working_context, write_working_context
from app.services.workspace import navigation_counts
from app.services.worker import SupervisedFrontierWorker
from app.services.workflow_seed import seed_workflow_states
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


def _format_usdt_decimal(amount_base: int) -> str:
    whole, fraction = divmod(int(amount_base), 1_000_000)
    return f"{whole}.{fraction:06d}"


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
    "sahyog": {
        "storage_key": "sahyog-simulated",
        "target": "SAHYOG specimen route — no external delivery",
        "simulated": True,
    },
}
NOTICE_DEADLINES = {24}


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
    user = request.session.get("user") or {}
    trace_mode = "fixture"
    if snapshot is not None:
        candidate_mode = str(snapshot.result_json.get("engine", {}).get("mode") or "fixture")
        trace_mode = "live" if candidate_mode == "live" else "fixture"
    write_working_context(
        request.session,
        role=str(user.get("role") or OfficerRole.io.value),
        patch={
            "case_id": case.id,
            "ack_no": case.ack_no,
            "snapshot_id": snapshot.id if snapshot else None,
            "finding_id": finding.id if finding else None,
            "notice_id": None,
            "dispatch_id": None,
            "mode": trace_mode,
        },
    )
    officer_session = getattr(request.state, "officer_session", None)
    if session is not None and officer_session is not None and case.id is not None:
        record_session_activity(session, officer_session, case_id=case.id)


def active_case_from_session(request: Request) -> dict:
    user = request.session.get("user") or {}
    context = read_working_context(
        request.session,
        role=str(user.get("role") or OfficerRole.io.value),
    )
    active = request.session.get("active_case") or {}
    return {
        "id": context.get("case_id"),
        "ack_no": active.get("ack_no"),
        "snapshot_id": context.get("snapshot_id"),
        "finding_id": context.get("finding_id"),
        "notice_id": context.get("notice_id"),
        "dispatch_id": context.get("dispatch_id"),
        "mode": context.get("mode"),
        "strategy": context.get("strategy"),
        "focus": context.get("focus"),
    }


def get_active_case(request: Request, session: Session) -> Case | None:
    active = active_case_from_session(request)
    case_id = active.get("id")
    if not case_id:
        return None
    case = session.get(Case, int(case_id))
    user = request.session.get("user") or {}
    if case is not None and not can_access_case(
        session,
        case_id=int(case_id),
        officer_pis=str(user.get("pis") or ""),
        role=str(user.get("role") or OfficerRole.io.value),
    ):
        return None
    return case


def template_context(request: Request, user: dict | None = None) -> dict:
    role = str((user or {}).get("role") or OfficerRole.io.value)
    return {
        "request": request,
        "user": user,
        "settings": settings,
        "health": {"ok": True, "label": "Fixture systems operational"},
        "source_health": complaints.health(),
        "demo": demo_case(),
        "active_case": active_case_from_session(request),
        "working_context": read_working_context(request.session, role=role),
        "role_view_registry": role_view_registry(),
        "navigation_routes": {
            "case-intake": "/cases/new",
            "live-intake": "/cases/live/new",
            "docket": "/docket",
            "trace": "/traces/{snapshot_id}",
            "investigator-canvas": "/cases/{case_id}/canvas",
            "finding": "/findings/{finding_id}",
            "notice": "/notices/{notice_id}",
            "dispatch-tracker": "/dispatch-tracker",
            "risk": "/risk-check",
            "integrations": "/integrations",
            "audit-log": "/audit-log",
            "sessions": "/sessions",
        },
        "csrf_token": csrf_token(request) if user else "",
        "session_idle": {
            "obscure_seconds": settings.session_idle_obscure_seconds,
            "warning_seconds": settings.session_idle_warning_seconds,
            "timeout_seconds": settings.session_idle_timeout_seconds,
        },
    }


def _audit_filter_value(value: str | None) -> str:
    return (value or "").strip()


def _audit_log_rows(
    *,
    subject_filter: str = "",
    action_filter: str = "",
    actor_filter: str = "",
    limit: int = 250,
) -> tuple[list[dict], list[str], int]:
    rows = read_audit_events()
    action_options = sorted(
        {str(row.get("action")) for row in rows if row.get("action")}
    )
    filtered_rows = rows
    if subject_filter:
        needle = subject_filter.lower()
        filtered_rows = [
            row for row in filtered_rows if needle in str(row.get("subject", "")).lower()
        ]
    if action_filter:
        filtered_rows = [
            row for row in filtered_rows if str(row.get("action", "")) == action_filter
        ]
    if actor_filter:
        needle = actor_filter.lower()
        filtered_rows = [
            row for row in filtered_rows if needle in str(row.get("actor", "")).lower()
        ]

    rendered_rows: list[dict] = []
    for row in reversed(filtered_rows):
        data = row.get("data")
        if not isinstance(data, dict):
            data = {}
        rendered_rows.append(
            {
                "ts_ms": row.get("ts_ms"),
                "actor": str(row.get("actor") or ""),
                "action": str(row.get("action") or ""),
                "subject": str(row.get("subject") or ""),
                "prev_hash": str(row.get("prev_hash") or ""),
                "row_hash": str(row.get("row_hash") or ""),
                "data_json": json.dumps(data, indent=2, sort_keys=True),
            }
        )
        if len(rendered_rows) >= limit:
            break
    return rendered_rows, action_options, len(filtered_rows)


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
    next_url: Annotated[str | None, Form()] = None,
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
    redirect_to = (
        next_url
        if next_url and next_url.startswith("/") and not next_url.startswith("//")
        else "/docket"
    )
    return RedirectResponse(redirect_to, status_code=303)


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
        # Preserve the established controlled-fixture rehearsal reset. The v3
        # decision omits a new reset control; it does not remove this behavior.
        reset_summary = reset_demo_notice_workflow(session)
        if reset_summary["notices"]:
            append_audit_event(
                row.officer_pis,
                "demo_notice.reset_on_logout",
                "controlled_fixture",
                reset_summary,
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


@app.get("/api/work-context")
def get_work_context(
    request: Request,
    user: dict = Depends(session_user),
) -> dict:
    return read_working_context(request.session, role=user["role"])


@app.post("/api/work-context")
async def update_work_context(
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(422, "Working context must be a JSON object.")
    require_csrf(request, request.headers.get("x-csrf-token") or payload.get("csrf_token"))
    patch = dict(payload.get("context") or payload)
    case_id = patch.get("case_id")
    if case_id is not None:
        try:
            case_id = int(case_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "Invalid case context.") from exc
        if session.get(Case, case_id) is None or not can_access_case(
            session,
            case_id=case_id,
            officer_pis=user["pis"],
            role=user["role"],
        ):
            raise HTTPException(404, "Case context is unavailable.")
        patch["case_id"] = case_id

    snapshot_id = patch.get("snapshot_id")
    if snapshot_id is not None:
        try:
            snapshot_id = int(snapshot_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "Invalid trace context.") from exc
        snapshot = session.get(TraceSnapshot, snapshot_id)
        if (
            snapshot is None
            or (case_id is not None and snapshot.case_id != case_id)
            or not can_access_case(
                session,
                case_id=snapshot.case_id if snapshot else -1,
                officer_pis=user["pis"],
                role=user["role"],
            )
        ):
            raise HTTPException(404, "Trace context is unavailable.")
        patch["snapshot_id"] = snapshot.id
        patch["case_id"] = snapshot.case_id
        patch["mode"] = (
            "live"
            if snapshot.result_json.get("engine", {}).get("mode") == "live"
            else "fixture"
        )

    finding_id = patch.get("finding_id")
    if finding_id is not None:
        try:
            finding_id = int(finding_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "Invalid finding context.") from exc
        finding = session.get(Finding, finding_id)
        if finding is None or (
            patch.get("case_id") is not None and finding.case_id != patch["case_id"]
        ) or not can_access_case(
            session,
            case_id=finding.case_id if finding else -1,
            officer_pis=user["pis"],
            role=user["role"],
        ):
            raise HTTPException(404, "Finding context is unavailable.")
        patch["finding_id"] = finding.id
        patch["case_id"] = finding.case_id

    notice_id = patch.get("notice_id")
    if notice_id is not None:
        try:
            notice_id = int(notice_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "Invalid notice context.") from exc
        notice = session.get(Notice, notice_id)
        if notice is None or (
            patch.get("case_id") is not None and notice.case_id != patch["case_id"]
        ) or not can_access_case(
            session,
            case_id=notice.case_id if notice else -1,
            officer_pis=user["pis"],
            role=user["role"],
        ):
            raise HTTPException(404, "Notice context is unavailable.")
        patch["notice_id"] = notice.id
        patch["case_id"] = notice.case_id

    dispatch_id = patch.get("dispatch_id")
    if dispatch_id is not None:
        try:
            dispatch_id = int(dispatch_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "Invalid dispatch context.") from exc
        dispatch = session.get(DispatchRecord, dispatch_id)
        if dispatch is None or not can_access_case(
            session,
            case_id=dispatch.case_id if dispatch else -1,
            officer_pis=user["pis"],
            role=user["role"],
        ):
            raise HTTPException(404, "Dispatch context is unavailable.")
        patch["dispatch_id"] = dispatch.id
        patch["case_id"] = dispatch.case_id

    return write_working_context(request.session, role=user["role"], patch=patch)


@app.get("/api/navigation-state")
def navigation_state(
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    return {
        "context": read_working_context(request.session, role=user["role"]),
        "counts": navigation_counts(
            session,
            officer_pis=user["pis"],
            role=user["role"],
        ),
    }


@app.get("/api/workspace/events")
def workspace_events(
    request: Request,
    transport: str | None = Query(default=None),
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
):
    """One scoped workspace stream with a JSON polling fallback."""
    case_ids = {
        int(item.id)
        for item in visible_cases(session, officer_pis=user["pis"], role=user["role"])
        if item.id is not None
    }
    records = (
        session.exec(
            select(DispatchRecord)
            .where(DispatchRecord.case_id.in_(case_ids))
            .order_by(DispatchRecord.updated_ts_ms.desc())
        ).all()
        if case_ids
        else []
    )
    payload = {
        "ts_ms": now_ms(),
        "context": read_working_context(request.session, role=user["role"]),
        "counts": navigation_counts(session, officer_pis=user["pis"], role=user["role"]),
        "dispatch": [
            {
                "id": row.id,
                "case_id": row.case_id,
                "notice_id": row.notice_id,
                "stage": row.stage,
                "acknowledgement_state": row.acknowledgement_state,
                "current_owner_pis": row.current_owner_pis,
                "escalation_level": row.escalation_level,
                "sla": dispatch_sla_view(row),
                "updated_ts_ms": row.updated_ts_ms,
            }
            for row in records
        ],
    }
    if transport == "json" or EventSourceResponse is None:
        return JSONResponse(payload)

    async def stream():
        yield {
            "id": str(payload["ts_ms"]),
            "event": "workspace",
            "data": json.dumps(payload, separators=(",", ":")),
        }

    return EventSourceResponse(stream(), ping=15)


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


@app.get("/audit-log", response_class=HTMLResponse)
def audit_log_page(
    request: Request,
    subject: str | None = Query(default=None),
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    subject_filter = _audit_filter_value(subject)
    action_filter = _audit_filter_value(action)
    actor_filter = _audit_filter_value(actor)
    rows, action_options, total_matches = _audit_log_rows(
        subject_filter=subject_filter,
        action_filter=action_filter,
        actor_filter=actor_filter,
    )
    return templates.TemplateResponse(
        request,
        "audit_log.html",
        {
            **template_context(request, user),
            "audit_rows": rows,
            "audit_actions": action_options,
            "audit_filters": {
                "subject": subject_filter,
                "action": action_filter,
                "actor": actor_filter,
            },
            "audit_total_matches": total_matches,
            "audit_limit": 250,
            "audit_chain_verified": verify_audit_chain(),
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
    reset_summary = reset_demo_notice_workflow(session)
    if reset_summary["notices"]:
        append_audit_event(
            user["pis"],
            "demo_notice.reset_on_logout",
            "controlled_fixture",
            reset_summary,
        )
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/docket", response_class=HTMLResponse)
def docket(request: Request, session: Session = Depends(get_session), user: dict = Depends(session_user)) -> HTMLResponse:
    case = get_active_case(request, session) or seed_demo(session)
    database_name = getattr(session.get_bind().url, "database", None)
    use_dynamic_docket = (
        settings.mode == "fixture"
        and database_name not in {None, "", ":memory:"}
        and request.headers.get("user-agent", "").lower() != "testclient"
    )
    if use_dynamic_docket:
        seed_workflow_states(session)
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
        "docket": (
            docket_state(
                session,
                officer_pis=user["pis"],
                role=user["role"],
                active_case_id=int(case.id or 0),
            )
            if use_dynamic_docket
            else docket_fixture(case)
        ),
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
    seed_value: Annotated[str | None, Form()] = None,
    amount: Annotated[str | None, Form()] = None,
    csrf_token: Annotated[str | None, Form()] = None,
    address_seed_value: Annotated[str | None, Form()] = None,
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
        "seed_value": seed_value or "",
        "address_seed_value": address_seed_value or "",
        "recipient_address": recipient_address or "",
        "amount": amount or "",
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
        normalized_seed = (seed_value or "").strip()
        if not normalized_seed and seed_kind == "txid":
            raise LiveTraceInputError("Enter a transaction hash.")
        if seed_kind == "txid":
            if not re.fullmatch(r"[a-fA-F0-9]{64}", normalized_seed):
                raise LiveTraceInputError("Transaction hash must be 64 hexadecimal characters.")
            try:
                resolved = tron.resolve_usdt_transfer(normalized_seed)
            except tron.ProviderConfigurationError as exc:
                raise LiveTraceInputError(str(exc)) from exc
            except tron.ProviderResponseError as exc:
                raise LiveTraceInputError(str(exc)) from exc
            payment_txid = str(resolved["txid"])
            reported_address = str(resolved["destination"])
            amount_base = int(resolved["amount_base"])
            payment_ts_ms = int(resolved["ts_ms"])
            values["seed_value"] = payment_txid
            values["recipient_address"] = reported_address
            values["amount"] = _format_usdt_decimal(amount_base)
            values["payment_ts_ist"] = ""
        else:
            payment_txid = None
            reported_address = (address_seed_value or normalized_seed).strip()
            if not reported_address:
                raise LiveTraceInputError("Enter a TRON address.")
            if not amount:
                raise LiveTraceInputError("An address seed requires the confirmed USDT amount.")
            amount_base = parse_amount_base(amount)
            payment_ts_ms = parse_ist_timestamp(payment_ts_ist)
            if payment_ts_ms is None:
                raise LiveTraceInputError(
                    "An address seed requires the confirmed payment date and time in IST."
                )
            values["seed_value"] = reported_address
            values["address_seed_value"] = reported_address
        family = detect_chain(reported_address)
        if family != "TRON":
            raise LiveTraceInputError(
                f"{family} live tracing is unavailable; this stage supports TRON mainnet only."
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
            "transaction_resolved": seed_kind == "txid",
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
    if case is None or not can_access_case(
        session,
        case_id=snapshot.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)
    finding = session.exec(select(Finding).where(Finding.snapshot_id == snapshot.id)).first()
    promoted = (
        snapshot.parent_snapshot_id is None
        or snapshot.status not in {"failed", "unsupported"}
        or (
            session.get(TraceSnapshot, snapshot.parent_snapshot_id) is not None
            and session.get(TraceSnapshot, snapshot.parent_snapshot_id).superseded_by_id == snapshot.id
        )
    )
    if case and promoted:
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
    if user["role"] not in {"io", "admin"}:
        raise HTTPException(403, "Only an investigating officer may re-run a trace.")
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404)
    case = session.get(Case, snapshot.case_id)
    if not case:
        raise HTTPException(404)
    if not can_access_case(
        session,
        case_id=case.id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
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
    promoted = latest.status not in {"failed", "unsupported"}
    if promoted:
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
            "promoted": promoted,
        },
    )
    return RedirectResponse(f"/traces/{latest.id}", status_code=303)


@app.get("/api/traces/{snapshot_id}/status")
def trace_runtime_status(
    snapshot_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if snapshot is None or not can_access_case(
        session,
        case_id=snapshot.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)
    return snapshot_runtime_status(session, snapshot)


@app.get("/api/traces/{snapshot_id}/explanations")
def trace_explanations(
    snapshot_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if snapshot is None or not can_access_case(
        session,
        case_id=snapshot.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)
    return snapshot_explanation(session, snapshot)


@app.get("/api/traces/{snapshot_id}/strategy-view")
def trace_strategy_view(
    snapshot_id: int,
    strategy: str = Query(...),
    previous_strategy: str | None = Query(default=None),
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if snapshot is None or not can_access_case(
        session,
        case_id=snapshot.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)
    if strategy not in engine_strategies():
        raise HTTPException(422, "Unknown trace strategy.")
    current = strategy_analysis(snapshot.result_json, strategy)
    if previous_strategy and previous_strategy in engine_strategies():
        previous = strategy_analysis(snapshot.result_json, previous_strategy)
        current["delta"] = strategy_delta(previous, current)
    else:
        current["delta"] = {
            "from": None,
            "to": strategy,
            "summary": current["criterion"],
            "primary_path_changed": False,
            "terminal_changed": False,
        }
    return current


@app.get("/api/traces/{snapshot_id}/stream")
def trace_stream(
    snapshot_id: int,
    request: Request,
    transport: str | None = Query(default=None),
    after: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
):
    snapshot = session.get(TraceSnapshot, snapshot_id)
    if snapshot is None or not can_access_case(
        session,
        case_id=snapshot.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)

    resume_after = after
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        try:
            resume_after = max(resume_after, int(last_event_id))
        except ValueError:
            pass
    events = session.exec(
        select(TraceEvent)
        .where(TraceEvent.snapshot_id == snapshot_id, TraceEvent.seq > resume_after)
        .order_by(TraceEvent.seq)
    ).all()

    async def stream():
        for event in events:
            yield {
                "id": str(event.seq),
                "event": event.event_type,
                "data": json.dumps(event.data, separators=(",", ":")),
            }

    if transport == "json" or EventSourceResponse is None:
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
    if not case or not can_access_case(
        session,
        case_id=case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
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
            "finding": finding,
            "view": view,
            "runtime_status": snapshot_runtime_status(session, snap) if snap else None,
            "explainability": snapshot_explanation(session, snap) if snap else None,
            "omega_graph": omega_graph_payload(snap.result_json if snap else {}, case=case),
            "trace_strategy": str(((snap.result_json if snap else {}).get("params") or {}).get("strategy") or "dominant_fund_flow"),
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
    """Resolve only within the officer's explicit active case context."""
    active = active_case_from_session(request)
    latest_notice = None
    if active.get("id"):
        latest_notice = session.exec(
            select(Notice)
            .where(Notice.case_id == int(active["id"]))
            .order_by(Notice.created_ts_ms.desc(), Notice.id.desc())
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
    if latest_finding:
        return RedirectResponse(f"/findings/{latest_finding.id}", status_code=307)
    return RedirectResponse("/docket", status_code=307)


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
    if not can_access_case(
        session,
        case_id=notice.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)
    case = session.get(Case, notice.case_id)
    finding = session.get(Finding, notice.finding_id)
    snapshot = session.get(TraceSnapshot, finding.snapshot_id)
    if case and finding and snapshot:
        set_active_case(request, case, snapshot, finding, session)
        write_working_context(
            request.session,
            role=user["role"],
            patch={"notice_id": notice.id},
        )
    vm = notice_view_model(case.model_dump(), snapshot.result_json, notice.model_dump())
    dispatches = session.exec(
        select(Dispatch).where(Dispatch.notice_id == notice.id).order_by(Dispatch.id)
    ).all()
    tracker_events = session.exec(
        select(NoticeTrackerEvent)
        .where(NoticeTrackerEvent.notice_id == notice.id)
        .order_by(NoticeTrackerEvent.created_ts_ms.desc(), NoticeTrackerEvent.id.desc())
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
    workflow_draft = ensure_notice_draft(session, notice, author=user)
    workflow_version = active_version(session, workflow_draft)
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
            "dispatch_audit_event": tracker_events[0] if tracker_events else None,
            "countersigner": countersigner,
            "workflow_draft": workflow_draft,
            "workflow_version": workflow_version,
        },
    )


def _notice_workflow_context(
    session: Session,
    notice_id: int,
    user: dict,
) -> tuple[Notice, Case, Finding, TraceSnapshot, NoticeDraft]:
    notice = session.get(Notice, notice_id)
    if notice is None or not can_access_case(
        session,
        case_id=notice.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)
    case = session.get(Case, notice.case_id)
    finding = session.get(Finding, notice.finding_id)
    snapshot = session.get(TraceSnapshot, finding.snapshot_id) if finding else None
    if case is None or finding is None or snapshot is None:
        raise HTTPException(409, "Notice workflow context is incomplete.")
    draft = ensure_notice_draft(session, notice, author=user)
    return notice, case, finding, snapshot, draft


@app.get("/notices/{notice_id}/workflow", response_class=HTMLResponse)
def notice_workflow_view(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    notice, case, finding, snapshot, draft = _notice_workflow_context(session, notice_id, user)
    set_active_case(request, case, snapshot, finding, session)
    write_working_context(
        request.session,
        role=user["role"],
        patch={"notice_id": notice.id},
    )
    attachments = active_attachments(session, int(draft.id or 0))
    version = active_version(session, draft)
    versions = session.exec(
        select(NoticeVersion)
        .where(NoticeVersion.draft_id == draft.id)
        .order_by(NoticeVersion.version_no.desc())
    ).all()
    verification = verification_state(
        session,
        draft,
        int(request.state.officer_session.id),
    )
    session.commit()
    return templates.TemplateResponse(
        request,
        "notice_workflow.html",
        {
            **template_context(request, user),
            "notice": notice,
            "case": case,
            "finding": finding,
            "snapshot": snapshot,
            "draft": draft,
            "version": version,
            "versions": versions,
            "attachments": attachments,
            "verification": verification,
            "statutory_options": STATUTORY_OPTIONS,
            "workflow_mode": snapshot.result_json.get("engine", {}).get("mode", "fixture"),
        },
    )


@app.post("/notices/{notice_id}/workflow/parameters")
async def save_notice_workflow_parameters(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice, _case, _finding, _snapshot, draft = _notice_workflow_context(session, notice_id, user)
    if user["role"] != "admin" and user["pis"] != draft.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may edit notice parameters.")
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    current = dict(draft.parameters)
    try:
        amount_base = int(str(form.get("amount_base") or ""))
        duration_hours = int(str(form.get("duration_hours") or ""))
    except ValueError as exc:
        raise HTTPException(422, "Amount and duration must be whole numbers.") from exc
    hashes = [value.strip() for value in str(form.get("transaction_hashes") or "").splitlines() if value.strip()]
    current.update(
        {
            "transaction_hashes": hashes,
            "amount_base": amount_base,
            "duration_hours": duration_hours,
            "vasp_name": str(form.get("vasp_name") or "").strip(),
            "vasp_contact": str(form.get("vasp_contact") or "").strip(),
            "jurisdiction": str(form.get("jurisdiction") or "").strip(),
            "officer_pis": str(form.get("officer_pis") or "").strip(),
            "officer_name": str(form.get("officer_name") or "").strip(),
            "officer_rank": str(form.get("officer_rank") or "").strip(),
            "supervisor_pis": str(form.get("supervisor_pis") or "").strip(),
            "supervisor_name": str(form.get("supervisor_name") or "").strip(),
            "statutory_key": str(form.get("statutory_key") or "").strip(),
        }
    )
    try:
        update_notice_parameters(session, draft, current)
    except NoticeWorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    annex_enabled = str(form.get("annex_enabled") or "") == "on"
    if draft.annex_enabled != annex_enabled:
        draft.annex_enabled = annex_enabled
        draft.dirty = draft.generated
        draft.attested_by_pis = None
        draft.attested_ts_ms = None
        draft.attested_version_no = None
        session.add(draft)
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="notice.draft_saved",
        subject=notice.notice_no,
        entity_type="notice_draft",
        entity_id=draft.id,
        case_id=notice.case_id,
        summary="Notice workflow parameters saved",
        data={"dirty": draft.dirty, "annex_enabled": draft.annex_enabled},
    )
    commit_and_flush_audit(session)
    return RedirectResponse(f"/notices/{notice.id}/workflow", status_code=303)


@app.post("/notices/{notice_id}/workflow/attachments")
async def upload_notice_workflow_attachment(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice, _case, _finding, _snapshot, draft = _notice_workflow_context(session, notice_id, user)
    if user["role"] != "admin" and user["pis"] != draft.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may change attachments.")
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(422, "Select an attachment.")
    data = await upload.read()
    try:
        add_attachment(
            session,
            draft,
            slot=str(form.get("slot") or ""),
            source="officer_upload",
            original_name=upload.filename or "attachment",
            mime_type=upload.content_type or "application/octet-stream",
            data=data,
            provenance="Uploaded by the authenticated drafting officer",
            simulated=False,
            author_pis=user["pis"],
        )
    except NoticeWorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="notice.attachment_added",
        subject=notice.notice_no,
        entity_type="notice_draft",
        entity_id=draft.id,
        case_id=notice.case_id,
        summary="Notice attachment added",
        data={"slot": str(form.get("slot") or ""), "size_bytes": len(data)},
    )
    commit_and_flush_audit(session)
    return RedirectResponse(f"/notices/{notice.id}/workflow", status_code=303)


@app.post("/notices/{notice_id}/workflow/attachments/from-complaint-source")
async def import_notice_source_attachment(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice, case, _finding, _snapshot, draft = _notice_workflow_context(session, notice_id, user)
    if user["role"] != "admin" and user["pis"] != draft.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may change attachments.")
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    slot = str(form.get("slot") or "complaint")
    source_document = complaints.fetch_document(case.ack_no, slot)
    if source_document is None:
        raise HTTPException(404, "No fixture source document is available.")
    try:
        add_attachment(
            session,
            draft,
            slot=slot,
            source="fixture_complaint_source",
            original_name=source_document["name"],
            mime_type=source_document["mime_type"],
            data=source_document["data"],
            provenance=source_document["provenance"],
            simulated=True,
            author_pis=user["pis"],
        )
    except NoticeWorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="notice.simulated_attachment_imported",
        subject=notice.notice_no,
        entity_type="notice_draft",
        entity_id=draft.id,
        case_id=notice.case_id,
        summary="Simulated portal document attached",
        data={"slot": slot, "simulated": True},
        demo_session=True,
    )
    commit_and_flush_audit(session)
    return RedirectResponse(f"/notices/{notice.id}/workflow", status_code=303)


@app.post("/notices/{notice_id}/workflow/generate")
async def generate_notice_workflow_version(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice, _case, _finding, _snapshot, draft = _notice_workflow_context(session, notice_id, user)
    if user["role"] != "admin" and user["pis"] != draft.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may generate a notice version.")
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    try:
        version = generate_version(session, draft, author_pis=user["pis"])
    except NoticeWorkflowError as exc:
        raise HTTPException(422, str(exc)) from exc
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="notice.version_generated",
        subject=notice.notice_no,
        entity_type="notice_version",
        entity_id=version.id,
        case_id=notice.case_id,
        summary=f"Immutable notice version {version.version_no} generated",
        data={"version_no": version.version_no, "sha256": version.content_sha256},
    )
    commit_and_flush_audit(session)
    return RedirectResponse(f"/notices/{notice.id}/workflow#preview", status_code=303)


@app.post("/notices/{notice_id}/workflow/attest")
async def attest_notice_workflow_version(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice, _case, _finding, _snapshot, draft = _notice_workflow_context(session, notice_id, user)
    if user["role"] != "admin" and user["pis"] != draft.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may attest this notice version.")
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    if str(form.get("preview_complete") or "") != "true":
        raise HTTPException(409, "Review the complete paginated preview before attesting.")
    try:
        attest_version(session, draft, officer_pis=user["pis"])
    except NoticeWorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="notice.version_attested",
        subject=notice.notice_no,
        entity_type="notice_version",
        entity_id=active_version(session, draft).id,
        case_id=notice.case_id,
        summary="Officer attested the reviewed notice version",
        data={"version_no": draft.attested_version_no},
    )
    commit_and_flush_audit(session)
    return RedirectResponse(f"/notices/{notice.id}/workflow#verification", status_code=303)


@app.post("/notices/{notice_id}/workflow/verify")
async def verify_notice_workflow_session(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice, _case, _finding, snapshot, draft = _notice_workflow_context(session, notice_id, user)
    if user["role"] != "admin" and user["pis"] != draft.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may re-verify this notice.")
    if draft.attested_version_no is None:
        raise HTTPException(409, "Attest the current version before re-verification.")
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    method = str(form.get("method") or "")
    mode = snapshot.result_json.get("engine", {}).get("mode", "fixture")
    success = method in {"demo_io", "demo_acp"} and mode == "fixture"
    try:
        state = record_verification(
            session,
            draft,
            officer_session_id=int(request.state.officer_session.id),
            method=method,
            success=success,
            fixture_mode=mode == "fixture",
        )
    except NoticeWorkflowError as exc:
        raise HTTPException(423 if "locked" in str(exc).lower() else 422, str(exc)) from exc
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="notice.reverified" if success else "notice.reverification_failed",
        subject=notice.notice_no,
        entity_type="notice_draft",
        entity_id=draft.id,
        case_id=notice.case_id,
        summary="Notice re-verification recorded",
        data={"method": method, "success": success, "failure_count": state.failure_count},
        demo_session=method.startswith("demo_"),
    )
    commit_and_flush_audit(session)
    if not success:
        raise HTTPException(503, "The selected external sign-in is not configured in this prototype.")
    return RedirectResponse(f"/notices/{notice.id}/workflow#routing", status_code=303)


@app.post("/notices/{notice_id}/workflow/route")
async def route_notice_workflow(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice, case, finding, _snapshot, draft = _notice_workflow_context(session, notice_id, user)
    if user["role"] != "admin" and user["pis"] != draft.created_by_pis:
        raise HTTPException(403, "Only the drafting officer may route this notice.")
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    state = verification_state(session, draft, int(request.state.officer_session.id))
    if state.verified_ts_ms is None or draft.attested_version_no != draft.active_version_no:
        raise HTTPException(409, "Re-verify the attested current version before routing.")
    route = str(form.get("route") or "")
    if route not in {"acp", "direct"}:
        raise HTTPException(422, "Select ACP countersignature or direct dispatch review.")
    if route == "direct" and finding.amount_credited_base is not None and finding.amount_credited_base >= demo_case()["notice"]["threshold_countersign_base"]:
        raise HTTPException(409, "This amount requires ACP countersignature.")
    if route == "acp":
        notice.status = "awaiting_countersignature"
        case.stage = CaseStage.awaiting_countersignature
        action = "notice.routed_to_acp"
    else:
        notice.status = "countersigned"
        case.stage = CaseStage.countersigned
        action = "notice.direct_dispatch_review"
    case.updated_ts_ms = now_ms()
    session.add(notice)
    session.add(case)
    record = ensure_dispatch_record(session, notice, version=active_version(session, draft))
    record.stage = notice.status
    record.updated_ts_ms = now_ms()
    session.add(record)
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action=action,
        subject=notice.notice_no,
        entity_type="notice",
        entity_id=notice.id,
        case_id=notice.case_id,
        summary="Verified notice routed to the next workflow stage",
        data={"route": route, "version_no": draft.active_version_no},
    )
    commit_and_flush_audit(session)
    return RedirectResponse(f"/notices/{notice.id}", status_code=303)


@app.get("/notices/{notice_id}/versions/{version_no}/pdf")
def download_notice_version_pdf(
    notice_id: int,
    version_no: int,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice = session.get(Notice, notice_id)
    if notice is None or not can_access_case(
        session,
        case_id=notice.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)
    draft = session.exec(select(NoticeDraft).where(NoticeDraft.notice_id == notice.id)).first()
    version = session.exec(
        select(NoticeVersion).where(
            NoticeVersion.draft_id == (draft.id if draft else -1),
            NoticeVersion.version_no == version_no,
        )
    ).first()
    if draft is None or version is None:
        raise HTTPException(404)
    try:
        artifact = render_version_pdf(
            session,
            notice=notice,
            draft=draft,
            version=version,
            author_pis=user["pis"],
        )
    except Exception as exc:
        raise HTTPException(503, f"PDF generation failed: {type(exc).__name__}") from exc
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="notice.pdf_exported",
        subject=notice.notice_no,
        entity_type="notice_version",
        entity_id=version.id,
        case_id=notice.case_id,
        summary="Immutable notice PDF exported",
        data={"version_no": version.version_no, "sha256": artifact.sha256},
    )
    commit_and_flush_audit(session)
    path = ROOT_DIR / artifact.storage_ref
    return Response(
        path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{artifact.original_name}"'},
    )


@app.get("/api/notices/{notice_id}/versions/{version_no}/diff")
def notice_version_diff_api(
    notice_id: int,
    version_no: int,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> dict:
    notice, _case, _finding, _snapshot, draft = _notice_workflow_context(session, notice_id, user)
    current = session.exec(
        select(NoticeVersion).where(
            NoticeVersion.draft_id == draft.id,
            NoticeVersion.version_no == version_no,
        )
    ).first()
    if current is None or current.parent_version_id is None:
        return {"notice_no": notice.notice_no, "version_no": version_no, "initial": True}
    previous = session.get(NoticeVersion, current.parent_version_id)
    if previous is None:
        raise HTTPException(409, "Parent notice version is missing.")
    return {"notice_no": notice.notice_no, "version_no": version_no, **version_diff(previous, current)}


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
    dispatch_record = ensure_dispatch_record(
        session,
        notice,
        version=version_for_draft(session, notice),
    )
    dispatch_record.stage = "awaiting_countersignature"
    dispatch_record.updated_ts_ms = now_ms()
    session.add(dispatch_record)
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
    workflow_draft = session.exec(
        select(NoticeDraft).where(NoticeDraft.notice_id == notice.id)
    ).first()
    workflow_version = active_version(session, workflow_draft) if workflow_draft else None
    if workflow_version is not None:
        workflow_version.countersigned_by_pis = user["pis"]
        workflow_version.countersigned_ts_ms = notice.countersigned_ts_ms
        workflow_version.immutable = True
        session.add(workflow_version)
    case = session.get(Case, notice.case_id)
    if case:
        case.stage = CaseStage.countersigned
        case.updated_ts_ms = notice.countersigned_ts_ms
        session.add(case)
    session.add(notice)
    dispatch_record = ensure_dispatch_record(
        session,
        notice,
        version=version_for_draft(session, notice),
    )
    dispatch_record.stage = "countersigned"
    dispatch_record.updated_ts_ms = notice.countersigned_ts_ms
    session.add(dispatch_record)
    session.commit()
    append_audit_event(user["pis"], "notice.countersign", notice.notice_no)
    return RedirectResponse(f"/notices/{notice.id}", status_code=303)


@app.post("/notices/{notice_id}/return-for-amendment")
async def return_notice_for_amendment(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    if user["role"] != "supervisor":
        raise HTTPException(403, "Only the supervising ACP may return a version for amendment.")
    notice = session.get(Notice, notice_id)
    if notice is None or notice.status != "awaiting_countersignature":
        raise HTTPException(409, "This notice is not awaiting ACP review.")
    if not can_access_case(
        session,
        case_id=notice.case_id,
        officer_pis=user["pis"],
        role=user["role"],
    ):
        raise HTTPException(404)
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    remarks = str(form.get("remarks") or "").strip()
    if not remarks:
        raise HTTPException(422, "ACP remarks are required when returning a notice.")
    draft = session.exec(select(NoticeDraft).where(NoticeDraft.notice_id == notice.id)).first()
    version = active_version(session, draft) if draft else None
    if draft is None or version is None:
        raise HTTPException(409, "Generate an immutable workflow version before ACP review.")
    if version.countersigned_by_pis:
        raise HTTPException(409, "A countersigned version cannot be returned or altered.")
    version.acp_remarks = remarks
    version.immutable = True
    draft.stage = "parameters"
    draft.dirty = True
    draft.attested_by_pis = None
    draft.attested_ts_ms = None
    draft.attested_version_no = None
    draft.updated_ts_ms = now_ms()
    notice.status = "draft"
    case = session.get(Case, notice.case_id)
    if case:
        case.stage = CaseStage.notice_draft
        case.updated_ts_ms = now_ms()
        session.add(case)
    session.add(version)
    session.add(draft)
    session.add(notice)
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="notice.returned_for_amendment",
        subject=notice.notice_no,
        entity_type="notice_version",
        entity_id=version.id,
        case_id=notice.case_id,
        summary=f"Notice version {version.version_no} returned for amendment",
        data={"version_no": version.version_no, "remarks": remarks},
    )
    commit_and_flush_audit(session)
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
    deadline_hours = 24
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
    notice.dispatched_ts_ms = now
    for channel_key in channel_keys:
        channel = NOTICE_CHANNELS[channel_key]
        failed = channel_key == "nodal-copy"
        simulated = bool(channel.get("simulated"))
        session.add(
            Dispatch(
                notice_id=notice.id,
                channel=channel["storage_key"],
                target=channel["target"],
                status="failed" if failed else "simulated" if simulated else "sent",
                attempts=1,
                last_error="Fixture nodal-copy delivery unavailable" if failed else None,
                created_ts_ms=now,
                updated_ts_ms=now,
            )
        )
    record_tracker_event(
        session,
        notice,
        actor_pis=user["pis"],
        to_status="dispatched",
        note="Status updated manually by officer at dispatch in the prototype.",
        ts_ms=now,
    )
    session.add(notice)
    case = session.get(Case, notice.case_id)
    if case:
        case.stage = CaseStage.notice_out
        case.updated_ts_ms = now
        session.add(case)
    dispatch_record = ensure_dispatch_record(
        session,
        notice,
        version=version_for_draft(session, notice),
        dispatched_ts_ms=now,
    )
    queue_audit_event(
        session,
        actor=user["pis"],
        actor_name=user["name"],
        actor_rank=user["rank"],
        actor_role=user["role"],
        action="dispatch.recorded",
        subject=notice.notice_no,
        entity_type="dispatch_record",
        entity_id=dispatch_record.id,
        case_id=notice.case_id,
        summary="Dispatch attempts recorded; external delivery is not inferred",
        data={
            "channels": channel_keys,
            "simulated_channels": [
                key for key in channel_keys if NOTICE_CHANNELS[key].get("simulated")
            ],
        },
    )
    session.commit()
    flush_audit_outbox(session)
    append_audit_event(
        user["pis"],
        "notice.dispatch",
        notice.notice_no,
        {"channels": channel_keys, "deadline_hours": deadline_hours},
    )
    return RedirectResponse(f"/notices/{notice.id}", status_code=303)


@app.get("/dispatch-tracker", response_class=HTMLResponse)
def dispatch_tracker(
    request: Request,
    status: str | None = Query(default=None),
    vasp: str | None = Query(default=None),
    case: str | None = Query(default=None),
    io: str | None = Query(default=None),
    acp: str | None = Query(default=None),
    notice_type: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    escalation: bool = Query(default=False),
    sla_breach: bool = Query(default=False),
    active_case: int | None = Query(default=None),
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> HTMLResponse:
    normalized_status = None
    if status:
        normalized_status = status.strip().lower().replace("-", "_")
        if normalized_status not in TRACKER_STATUS_LABELS:
            normalized_status = None
    accessible_cases = visible_cases(session, officer_pis=user["pis"], role=user["role"])
    visible_case_ids = {int(item.id) for item in accessible_cases if item.id is not None}
    breached_added = False
    records = session.exec(select(DispatchRecord).where(DispatchRecord.case_id.in_(visible_case_ids))).all() if visible_case_ids else []
    for record in records:
        breached_added = register_sla_breach(session, record) or breached_added
    if breached_added:
        commit_and_flush_audit(session)
    requested_filters = {
        "status": normalized_status or "",
        "vasp": vasp or "",
        "case": case or "",
        "io": io or "",
        "acp": acp or "",
        "notice_type": notice_type or "",
        "channel": channel or "",
        "escalation": bool(escalation),
        "sla_breach": bool(sla_breach),
        "active_case": active_case,
    }
    request.session["dispatch_filters"] = requested_filters
    rows = tracker_rows(
        session,
        status_filter=normalized_status,
        vasp_filter=vasp or None,
        case_filter=case or None,
        visible_case_ids=visible_case_ids,
        io_filter=io or None,
        acp_filter=acp or None,
        notice_type_filter=notice_type or None,
        channel_filter=channel or None,
        escalated_only=escalation,
        breached_only=sla_breach,
        active_case_id=active_case,
    )
    unfiltered_rows = tracker_rows(session, visible_case_ids=visible_case_ids)
    return templates.TemplateResponse(
        request,
        "dispatch_tracker.html",
        {
            **template_context(request, user),
            "rows": rows,
            "counts": tracker_counts(unfiltered_rows),
            "status_labels": TRACKER_STATUS_LABELS,
            "response_sub_outcomes": RESPONSE_SUB_OUTCOMES,
            "filters": requested_filters,
            "officers": active_officer_profiles(session),
        },
    )


def _filtered_tracker_rows_from_session(
    request: Request,
    session: Session,
    user: dict,
) -> list[dict]:
    filters = request.session.get("dispatch_filters") or {}
    visible_case_ids = {
        int(item.id)
        for item in visible_cases(session, officer_pis=user["pis"], role=user["role"])
        if item.id is not None
    }
    return tracker_rows(
        session,
        status_filter=filters.get("status") or None,
        vasp_filter=filters.get("vasp") or None,
        case_filter=filters.get("case") or None,
        visible_case_ids=visible_case_ids,
        io_filter=filters.get("io") or None,
        acp_filter=filters.get("acp") or None,
        notice_type_filter=filters.get("notice_type") or None,
        channel_filter=filters.get("channel") or None,
        escalated_only=bool(filters.get("escalation")),
        breached_only=bool(filters.get("sla_breach")),
        active_case_id=filters.get("active_case"),
    )


@app.get("/dispatch-tracker/export.csv")
def export_dispatch_tracker_csv(
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    rows = _filtered_tracker_rows_from_session(request, session, user)
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "case", "notice", "notice_type", "version_id", "vasp", "assigned_io",
        "supervising_acp", "current_owner", "stage", "acknowledgement",
        "dispatched_ts_ms", "sla_due_ts_ms", "sla_tone", "escalation_level",
    ])
    for row in rows:
        writer.writerow([
            row["case_ref"], row["notice_no"], row["notice_type"], row["notice_version_id"] or "legacy",
            row["vasp_label"], row["assigned_io_pis"], row["supervising_acp_pis"],
            row["current_owner_pis"], row["effective_status"], row["acknowledgement_state"],
            row["dispatch_ts_ms"] if row["dispatch_ts_ms"] is not None else "",
            row["deadline_ts_ms"] if row["deadline_ts_ms"] is not None else "",
            row["sla"]["tone"], row["escalation_level"],
        ])
    data = output.getvalue().encode("utf-8-sig")
    append_audit_event(
        user["pis"],
        "dispatch.register_export_csv",
        "current_filtered_register",
        {"row_count": len(rows), "sha256": sha256_bytes(data)},
    )
    return Response(
        data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=dispatch-register-filtered.csv"},
    )


@app.get("/dispatch-tracker/export.pdf")
def export_dispatch_tracker_pdf(
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    from pypdf import PdfReader
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    rows = _filtered_tracker_rows_from_session(request, session, user)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="TRINETRA filtered dispatch register",
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("TRINETRA — Filtered dispatch register", styles["Title"]),
        Paragraph(
            "Prototype register. Dispatch or acknowledgement state does not confirm restraint or external delivery.",
            styles["BodyText"],
        ),
    ]
    table_data = [["Case", "Notice", "VASP", "IO / ACP", "Stage", "SLA", "Owner / level"]]
    for row in rows:
        table_data.append([
            row["case_ref"], row["notice_no"], row["vasp_label"],
            f"{row['assigned_io_pis']} / {row['supervising_acp_pis'] or 'legacy'}",
            row["effective_status"], row["time_label"],
            f"{row['current_owner_pis']} / L{row['escalation_level']}",
        ])
    table = Table(table_data, repeatRows=1, colWidths=[42 * mm, 32 * mm, 34 * mm, 31 * mm, 27 * mm, 37 * mm, 33 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12325e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94a3b8")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    story.append(table)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(10 * mm, 7 * mm, f"Filtered rows: {len(rows)}")
        canvas.drawRightString(landscape(A4)[0] - 10 * mm, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    data = buffer.getvalue()
    reader = PdfReader(BytesIO(data))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not reader.pages or "Filtered dispatch register" not in extracted:
        raise HTTPException(503, "Dispatch register PDF failed validation.")
    digest = sha256_bytes(data)
    output_dir = ROOT_DIR / "output" / "dispatch_registers"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"dispatch-register-{now_ms()}-{digest[:12]}.pdf"
    if not path.exists():
        path.write_bytes(data)
    append_audit_event(
        user["pis"],
        "dispatch.register_export_pdf",
        "current_filtered_register",
        {"row_count": len(rows), "sha256": digest, "path": path.relative_to(ROOT_DIR).as_posix()},
    )
    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@app.post("/dispatch-tracker/bulk")
async def bulk_dispatch_tracker_action(
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> JSONResponse:
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    notice_ids: list[int] = []
    for value in form.getlist("notice_id"):
        try:
            notice_ids.append(int(str(value)))
        except ValueError:
            continue
    notice_ids = list(dict.fromkeys(notice_ids))
    action = str(form.get("action") or "")
    reason = str(form.get("reason") or "").strip()
    next_owner_pis = str(form.get("next_owner_pis") or "").strip()
    channel = str(form.get("channel") or "").strip()
    if not notice_ids:
        raise HTTPException(422, "Select at least one notice.")
    if action not in {"escalate", "reassign_io", "nudge"}:
        raise HTTPException(422, "Unknown bulk action.")
    if not reason:
        raise HTTPException(422, "A reason is required for every bulk action.")
    results: list[dict] = []
    for notice_id in notice_ids:
        notice = session.get(Notice, notice_id)
        record = session.exec(select(DispatchRecord).where(DispatchRecord.notice_id == notice_id)).first()
        if notice is None or record is None or not can_access_case(
            session,
            case_id=record.case_id,
            officer_pis=user["pis"],
            role=user["role"],
        ):
            results.append({"notice_id": notice_id, "ok": False, "error": "not_found_or_out_of_scope"})
            continue
        try:
            if action == "escalate":
                if user["role"] != "supervisor" or not next_owner_pis:
                    raise DispatchWorkflowError("The supervising ACP and a next owner are required.")
                escalate_dispatch(
                    session,
                    record,
                    next_owner_pis=next_owner_pis,
                    reason=reason,
                    assigning_acp_pis=user["pis"],
                )
            elif action == "reassign_io":
                if user["role"] not in {"supervisor", "admin"} or not next_owner_pis:
                    raise DispatchWorkflowError("A supervisor and a configured next IO are required.")
                officer = session.exec(select(OfficerProfile).where(OfficerProfile.pis == next_owner_pis)).first()
                if officer is None or not officer.active or officer.role != OfficerRole.io:
                    raise DispatchWorkflowError("The reassignment target must be an active IO.")
                assignment = session.exec(select(CaseAssignment).where(CaseAssignment.case_id == record.case_id)).first()
                if assignment is None:
                    raise DispatchWorkflowError("The case has no durable assignment.")
                previous = assignment.assigned_io_pis
                assignment.assigned_io_pis = next_owner_pis
                assignment.updated_ts_ms = now_ms()
                record.assigned_io_pis = next_owner_pis
                if record.current_owner_pis == previous:
                    record.current_owner_pis = next_owner_pis
                record.updated_ts_ms = now_ms()
                session.add(assignment)
                session.add(record)
                queue_audit_event(
                    session,
                    actor=user["pis"],
                    action="dispatch.io_reassigned",
                    subject=notice.notice_no,
                    entity_type="dispatch_record",
                    entity_id=record.id,
                    case_id=record.case_id,
                    summary="Assigned IO changed for this case",
                    data={"previous_io_pis": previous, "new_io_pis": next_owner_pis, "reason": reason},
                )
            else:
                if channel not in NOTICE_CHANNELS:
                    raise DispatchWorkflowError("Select a configured nudge channel.")
                channel_spec = NOTICE_CHANNELS[channel]
                session.add(
                    Dispatch(
                        notice_id=notice_id,
                        channel=f"nudge:{channel_spec['storage_key']}",
                        target=channel_spec["target"],
                        status="recorded_not_delivered",
                        attempts=1,
                        last_error="No external delivery evidence; nudge recorded locally",
                        created_ts_ms=now_ms(),
                        updated_ts_ms=now_ms(),
                    )
                )
                queue_audit_event(
                    session,
                    actor=user["pis"],
                    action="dispatch.nudge_recorded",
                    subject=notice.notice_no,
                    entity_type="dispatch_record",
                    entity_id=record.id,
                    case_id=record.case_id,
                    summary="Nudge recorded without claiming external delivery",
                    data={"channel": channel, "reason": reason, "delivered": False},
                )
            results.append({"notice_id": notice_id, "ok": True})
        except DispatchWorkflowError as exc:
            results.append({"notice_id": notice_id, "ok": False, "error": str(exc)})
    if any(item["ok"] for item in results):
        commit_and_flush_audit(session)
    return JSONResponse(
        {
            "requested_count": len(notice_ids),
            "succeeded_count": sum(item["ok"] for item in results),
            "failed_count": sum(not item["ok"] for item in results),
            "results": results,
        }
    )
@app.post("/dispatch-tracker/{notice_id}/status")
async def update_dispatch_tracker_status(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(404)
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    try:
        to_status = normalize_manual_status(str(form.get("tracker_status") or ""))
    except ValueError as exc:
        raise HTTPException(422, "Unknown tracker status.") from exc
    sub_outcome = str(form.get("sub_outcome") or "").strip()
    if sub_outcome and sub_outcome not in RESPONSE_SUB_OUTCOMES:
        raise HTTPException(422, "Unknown tracker response outcome.")
    note = str(form.get("note") or "").strip()
    record_tracker_event(
        session,
        notice,
        actor_pis=user["pis"],
        to_status=to_status,
        sub_outcome=sub_outcome or None,
        note=note or "Status updated manually by officer.",
    )
    dispatch_record = session.exec(
        select(DispatchRecord).where(DispatchRecord.notice_id == notice.id)
    ).first()
    if dispatch_record is not None and to_status != "escalated":
        set_dispatch_stage(
            session,
            dispatch_record,
            stage=to_status,
            actor_pis=user["pis"],
            acknowledgement_state=("received" if to_status in {"acknowledged", "responded"} else None),
        )
        commit_and_flush_audit(session)
    else:
        session.commit()
    append_audit_event(
        user["pis"],
        "notice.tracker_update",
        notice.notice_no,
        {"status": to_status, "sub_outcome": sub_outcome or None},
    )
    return RedirectResponse("/dispatch-tracker", status_code=303)


@app.post("/dispatch-tracker/{notice_id}/escalate")
async def escalate_dispatch_tracker_notice(
    notice_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: dict = Depends(session_user),
) -> Response:
    notice = session.get(Notice, notice_id)
    if not notice:
        raise HTTPException(404)
    form = await request.form()
    require_csrf(request, str(form.get("csrf_token") or ""))
    note = str(form.get("note") or "").strip()
    dispatch_record = session.exec(
        select(DispatchRecord).where(DispatchRecord.notice_id == notice.id)
    ).first()
    next_owner_pis = str(form.get("next_owner_pis") or "").strip()
    if dispatch_record is not None:
        if user["role"] != "supervisor":
            raise HTTPException(403, "Only the supervising ACP may assign the next escalation owner.")
        if not note or not next_owner_pis:
            raise HTTPException(422, "An escalation reason and next owner are required.")
        try:
            escalate_dispatch(
                session,
                dispatch_record,
                next_owner_pis=next_owner_pis,
                reason=note,
                assigning_acp_pis=user["pis"],
            )
        except DispatchWorkflowError as exc:
            raise HTTPException(422, str(exc)) from exc
    else:
        note = note or "Manual escalation recorded by officer."
    record_tracker_event(
        session,
        notice,
        actor_pis=user["pis"],
        to_status="escalated",
        note=note,
    )
    if dispatch_record is not None:
        commit_and_flush_audit(session)
    else:
        session.commit()
    append_audit_event(user["pis"], "notice.tracker_escalate", notice.notice_no, {"note": note})
    return RedirectResponse("/dispatch-tracker", status_code=303)


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
        finding = session.exec(
            select(Finding)
            .where(Finding.deposit_address == normalized_address)
            .order_by(Finding.created_ts_ms.desc(), Finding.id.desc())
        ).first()
        if (
            finding is None
            and normalized_mode == "fixture"
            and normalized_address == demo_case()["terminal"]["deposit_address"]
        ):
            fixture_case = seed_demo(session)
            _fixture_snapshot, finding = get_or_create_trace(
                session,
                fixture_case,
                trace_mode="fixture",
            )
        if finding is not None:
            case = session.get(Case, finding.case_id)
            snapshot = session.get(TraceSnapshot, finding.snapshot_id)
            if case is not None and snapshot is not None and can_access_case(
                session,
                case_id=case.id,
                officer_pis=user["pis"],
                role=user["role"],
            ):
                set_active_case(request, case, snapshot, finding, session)
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
