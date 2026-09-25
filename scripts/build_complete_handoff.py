from __future__ import annotations

import ast
import html
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageBreakIfNotEmpty,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "output" / "trinetra-complete-handoff-2026-09-20" / "HANDOFF_SOURCE.md"
CHAPTER_DIR = ROOT / "output" / "trinetra-complete-handoff-2026-09-20"
MASTER = ROOT / "output" / "TRINETRA_COMPLETE_PROJECT_HANDOFF_2026-09-20.md"
TRACEABILITY = ROOT / "output" / "TRINETRA_HANDOFF_TRACEABILITY_MATRIX_2026-09-20.md"
PDF_OUT = ROOT / "output" / "pdf" / "TRINETRA_COMPLETE_PROJECT_HANDOFF_2026-09-20.pdf"


@dataclass(frozen=True)
class Chapter:
    filename: str
    body: str


def compact(value: str, limit: int = 150) -> str:
    value = re.sub(r"\{[%{].*?[%}]\}", " ", value, flags=re.DOTALL)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(re.sub(r"\s+", " ", value)).strip()
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def expression_text(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return node.__class__.__name__


def route_catalog() -> str:
    path = ROOT / "app" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str, int]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            owner = decorator.func.value
            if not isinstance(owner, ast.Name) or owner.id != "app":
                continue
            method = decorator.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            route = expression_text(decorator.args[0]) if decorator.args else ""
            route = route.strip("'\"")
            rows.append((method, route, node.name, node.lineno))
    lines = [
        "# Appendix A - Complete route catalogue",
        "",
        "This appendix is generated from `app/main.py`. It lists every FastAPI route in the current code. Authentication, role and CSRF details are explained in the related feature chapters; the source line makes each row auditable.",
        "",
        "| # | Method | Path | Handler | Source |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for index, (method, route, handler, line) in enumerate(rows, 1):
        lines.append(f"| {index} | `{method}` | `{route}` | `{handler}` | `app/main.py:{line}` |")
    lines.extend(["", f"Total routes: **{len(rows)}**.", ""])
    return "\n".join(lines)


def model_catalog() -> str:
    path = ROOT / "app" / "models.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    models: list[tuple[str, int, list[tuple[str, str, str]]]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        is_table = any(
            keyword.arg == "table" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
            for keyword in node.keywords
        )
        if not is_table:
            continue
        fields: list[tuple[str, str, str]] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.append(
                    (
                        item.target.id,
                        expression_text(item.annotation),
                        expression_text(item.value),
                    )
                )
        models.append((node.name, node.lineno, fields))
    lines = [
        "# Appendix B - Persisted model catalogue",
        "",
        "This appendix is generated from `app/models.py`. A table is a stored record type. The type and default columns are shown so that future developers can see what is evidence, workflow state, identity state or configuration state.",
        "",
    ]
    for name, line, fields in models:
        lines.extend(
            [
                f"## {name}",
                "",
                f"Source: `app/models.py:{line}`.",
                "",
                "| Field | Type | Default or field rule |",
                "| --- | --- | --- |",
            ]
        )
        for field, field_type, default in fields:
            lines.append(f"| `{field}` | `{field_type}` | `{compact(default, 110)}` |")
        lines.append("")
    lines.append(f"Total persisted tables: **{len(models)}**.\n")
    return "\n".join(lines)


def service_catalog() -> str:
    lines = [
        "# Appendix C - Service-module map",
        "",
        "Services keep business rules out of the page templates. This list is generated from `app/services/` and names the public classes and functions in every current module.",
        "",
        "| Module | Public classes and functions |",
        "| --- | --- |",
    ]
    modules = sorted((ROOT / "app" / "services").glob("*.py"))
    modules = [item for item in modules if item.name != "__init__.py"]
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        ]
        lines.append(f"| `app/services/{path.name}` | {', '.join(f'`{name}`' for name in names) or 'No public symbol'} |")
    lines.extend(["", f"Total service modules: **{len(modules)}**.", ""])
    return "\n".join(lines)


TAG_PATTERN = re.compile(
    r"<(button|a|input|select|textarea)\b([^>]*)>(.*?)</\1>|<(input)\b([^>]*)/?>",
    re.IGNORECASE | re.DOTALL,
)


def attr(attrs: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*=\s*([\"'])(.*?)\1", attrs, flags=re.IGNORECASE | re.DOTALL)
    return compact(match.group(2), 90) if match else ""


def ui_control_catalog() -> str:
    rows: list[tuple[str, int, str, str, str, str]] = []
    for path in sorted((ROOT / "app" / "templates").glob("*.html")):
        source = path.read_text(encoding="utf-8")
        for match in TAG_PATTERN.finditer(source):
            tag = (match.group(1) or match.group(4) or "").lower()
            attrs = match.group(2) or match.group(5) or ""
            inner = match.group(3) or ""
            line = source.count("\n", 0, match.start()) + 1
            label = attr(attrs, "aria-label") or compact(inner, 95) or attr(attrs, "name") or attr(attrs, "id")
            target = attr(attrs, "href") or attr(attrs, "action") or attr(attrs, "name") or "-"
            lowered = attrs.lower()
            if "disabled" in lowered:
                state = "disabled by page state"
            elif tag == "a" and target == "#":
                state = "presentational placeholder"
            elif tag in {"input", "select", "textarea"}:
                state = "form input"
            elif "type=\"submit\"" in lowered or "type='submit'" in lowered:
                state = "server form action"
            elif "data-" in lowered:
                state = "JavaScript-enhanced control"
            elif tag == "a":
                state = "navigation or download link"
            else:
                state = "page control; inspect related script"
            rows.append((path.name, line, tag, label or "Unlabelled in source", target, state))
    lines = [
        "# Appendix D - Complete UI control catalogue",
        "",
        "This appendix inventories buttons, links and fields directly from every Jinja template. It includes very small features such as sign-out, clear, close, copy and disabled buttons. Some controls are role-gated or only visible in a particular workflow state. A placeholder is not described as a working integration.",
        "",
        "| # | Template | Line | Control | Visible label or accessible name | Target/name | Current behavior class |",
        "| ---: | --- | ---: | --- | --- | --- | --- |",
    ]
    for index, (template, line, tag, label, target, state) in enumerate(rows, 1):
        safe_label = label.replace("|", "\\|")
        safe_target = target.replace("|", "\\|")
        lines.append(f"| {index} | `{template}` | {line} | `{tag}` | {safe_label} | `{safe_target}` | {state} |")
    lines.extend(["", f"Total template controls found: **{len(rows)}**.", ""])
    return "\n".join(lines)


def configuration_catalog() -> str:
    names: dict[str, set[str]] = {}
    pattern = re.compile(r"(?:os\.getenv|os\.environ\.get)\(\s*[\"']([A-Z0-9_]+)[\"']")
    for path in sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "engine").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            names.setdefault(match.group(1), set()).add(path.relative_to(ROOT).as_posix())
    example_names: set[str] = set()
    example = ROOT / ".env.example"
    if example.exists():
        for line in example.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                example_names.add(line.split("=", 1)[0].strip())
    lines = [
        "# Appendix E - Configuration and feature-gate catalogue",
        "",
        "Only variable names are listed. Secret values are never read or printed. A variable that is referenced by code but missing from `.env.example` is marked so it can be documented or added later without silently changing behavior.",
        "",
        "| Variable | Present in `.env.example` | Referenced by |",
        "| --- | --- | --- |",
    ]
    for name in sorted(names):
        lines.append(f"| `{name}` | {'Yes' if name in example_names else 'No'} | {', '.join(f'`{item}`' for item in sorted(names[name]))} |")
    lines.extend(["", f"Total code-referenced environment variables: **{len(names)}**.", ""])
    return "\n".join(lines)


def test_catalog() -> str:
    rows: list[tuple[str, list[str]]] = []
    total = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        ]
        rows.append((path.name, names))
        total += len(names)
    lines = [
        "# Appendix F - Test traceability catalogue",
        "",
        "This appendix lists every collected top-level test function. Parametrized cases can produce more than one collected test, which is why the verified Pytest total can be higher than the number of function names shown here.",
        "",
    ]
    for filename, names in rows:
        lines.extend([f"## {filename}", ""])
        lines.extend(f"- `{name}`" for name in names)
        lines.append("")
    lines.append(f"Top-level test functions listed: **{total}**. Verified collected suite: **169 tests**.\n")
    return "\n".join(lines)


def parse_chapters(source: str) -> list[Chapter]:
    pattern = re.compile(r"<!-- CHAPTER: ([A-Za-z0-9_.-]+) -->\s*")
    matches = list(pattern.finditer(source))
    chapters: list[Chapter] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        chapters.append(Chapter(match.group(1), source[start:end].strip() + "\n"))
    return chapters


def write_outputs() -> str:
    source = SOURCE.read_text(encoding="utf-8")
    chapters = parse_chapters(source)
    if not chapters:
        raise RuntimeError("No chapter markers were found in the handoff source")
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
    for old in CHAPTER_DIR.glob("[0-9][0-9]_*.md"):
        old.unlink()
    for chapter in chapters:
        (CHAPTER_DIR / chapter.filename).write_text(chapter.body, encoding="utf-8", newline="\n")
    generated = [route_catalog(), model_catalog(), service_catalog(), ui_control_catalog(), configuration_catalog(), test_catalog()]
    appendix_names = [
        "45_ROUTE_CATALOGUE.md",
        "46_MODEL_CATALOGUE.md",
        "47_SERVICE_CATALOGUE.md",
        "48_UI_CONTROL_CATALOGUE.md",
        "49_CONFIGURATION_CATALOGUE.md",
        "50_TEST_CATALOGUE.md",
    ]
    for name, body in zip(appendix_names, generated, strict=True):
        (CHAPTER_DIR / name).write_text(body, encoding="utf-8", newline="\n")
    master_parts = [chapter.body.rstrip() for chapter in chapters]
    master_parts.extend(item.rstrip() for item in generated)
    master = "\n\n".join(master_parts) + "\n"
    MASTER.parent.mkdir(parents=True, exist_ok=True)
    MASTER.write_text(master, encoding="utf-8", newline="\n")
    TRACEABILITY.write_text(traceability_matrix(), encoding="utf-8", newline="\n")
    return master


FEATURES = [
    ("F-001", "Prototype login", "Login, supervisor login, demonstration accounts and honest integration-pending links", "login.html; supervisor_login.html", "/login; /auth/prototype; /auth/prototype/supervisor", "sessions.py; officers.py", "OfficerProfile; OfficerSession", "integration-tested"),
    ("F-002", "Sign-out and session safety", "Visible sign-out, sign-out-all, idle obscuring, warning, extension, expiry and revocation", "_session_controls.html; sessions.html", "/auth/logout; /auth/logout-all; /auth/session/extend; /sessions", "sessions.py; audit.py", "OfficerSession; AuditOutbox", "integration-tested"),
    ("F-003", "Role and case scope", "IO, ACP and admin views, assignments, watchers, server work context and scoped navigation", "base.html; _workspace_nav.html", "/api/work-context; /api/navigation-state; /api/workspace/events", "officers.py; role_views.py; work_context.py; workspace.py", "CaseAssignment; CaseWatcher; OfficerProfile", "integration-tested"),
    ("F-004", "Fixture complaint intake", "Complaint reference validation, particulars review, sample references and protected fixture trace start", "case_intake.html", "/cases/new; /cases/ingest; /cases/start", "demo.py; docket.py; repository.py", "Case; TraceSnapshot", "fixture-tested"),
    ("F-005", "Live TRON intake", "Address or transaction seed, amount/time, sealed bounds, live-gate feedback and exact parsing", "live_intake.html", "/cases/live/new; /cases/live/start", "live_trace.py; feature_flags.py", "Case; TraceSnapshot", "live-verified"),
    ("F-006", "Case docket", "Role-scoped KPIs, filters, search, sorting, trail, exports, snapshot history and ACP queue", "docket.html", "/docket", "docket.py; workspace.py", "Case; CaseAssignment; DispatchRecord", "integration-tested"),
    ("F-007", "Trace engine", "Typed seed/parameters/result, fixture-live modes, fail-closed terminals and canonical snapshot hashing", "trace.html; live_trace.html", "/api/cases/{case_id}/traces", "engine_bridge.py; hash.py", "TraceSnapshot; TraceEvent", "live-verified"),
    ("F-008", "TRON provider contract", "Exact seed event verification, confirmed TRC-20 history, pagination, normalization and typed errors", "live_trace.html", "provider calls through trace routes", "engine/adapters/tron.py; evidence_store.py", "SourceCoverage; CanonicalTraceEvent", "live-verified"),
    ("F-009", "Integer attribution", "Proportional integer allocation, residual carry, value conservation and no overspend", "trace.html; canvas.html", "strategy and trace APIs", "allocation.py; accounting.py", "AttributedLot; CanonicalTraceEvent", "integration-tested"),
    ("F-010", "Frontier and worker", "Deferral reasons, resume cursors, leasing, retries, stale recovery and bounded supervision", "live_trace.html", "/api/traces/{snapshot_id}/status", "frontier.py; live_tron_resume.py; worker.py", "FrontierItem; SourceCoverage", "integration-tested"),
    ("F-011", "Trace event workspace", "SSE replay with JSON fallback, progress, hops, parked branches, terminal and live runtime status", "trace.html; live_trace.html", "/api/traces/{snapshot_id}/stream; /api/traces/{snapshot_id}/status", "live_trace.py; repository.py", "TraceEvent; TraceSnapshot", "integration-tested"),
    ("F-012", "Immutable retrace", "New child snapshots, retained failures, parent links and no overwrite of earlier exports", "trace.html; canvas.html; live_canvas.html", "/traces/{snapshot_id}/retrace", "repository.py; audit.py", "TraceSnapshot; AuditOutbox", "integration-tested"),
    ("F-013", "Graph and canvas", "Flow graph, details, toggles, zoom, pan, layout, fullscreen, strategy comparison and export", "canvas.html; live_canvas.html; _omega_graph.html", "/cases/{case_id}/canvas; /api/cases/{case_id}/graph", "graph_view.py; strategy.py", "TraceSnapshot; AttributedLot; FrontierItem", "integration-tested"),
    ("F-014", "Explainability", "Investigator and technical registers, evidence references, arithmetic, terminal reasons and annex", "_trace_explainability.html", "/api/traces/{snapshot_id}/explanations", "explainability.py", "CanonicalTraceEvent; SourceCoverage", "integration-tested"),
    ("F-015", "Risk check", "Fixture/live lookups, ten raw feature families, evidence bands, unknown result and case preparation", "risk.html", "/risk-check; /api/risk-check; /risk-check/escalate", "risk.py; behavior.py; review.py", "CanonicalTraceEvent; SourceCoverage", "live-verified"),
    ("F-016", "Probability and ML boundary", "Null scores/posteriors, disabled probability and no trained or adaptive model", "risk.html; integrations.html", "risk routes", "review.py; behavior.py", "None", "unavailable"),
    ("F-017", "Custody finding", "Strict terminal gate, nullable finding, deposit-address evidence and pre-notice review", "finding.html", "/findings/{finding_id}; /findings/{finding_id}/checks", "custody.py; repository.py", "CustodyAssertion; Finding", "fixture-tested"),
    ("F-018", "Provider response contracts", "Certified response, ledger, KYC, trade, withdrawal, session and balance records", "No public production import page", "gated service contract", "custody.py", "ProviderResponseRecord and six related tables", "fixture-tested"),
    ("F-019", "Notice authoring", "Six stages, validated parameters, attachments, immutable versions, review, attestation and routing", "notice_workflow.html", "/notices/{notice_id}/workflow and child actions", "notice_workflow.py", "Notice; NoticeDraft; NoticeVersion; NoticeAttachment", "integration-tested"),
    ("F-020", "Notice review authority", "Countersign request, separate ACP signature, return for amendment and regeneration", "notice.html; notice_workflow.html", "/notices/{notice_id}/countersign-request; /countersign; /return-for-amendment", "notice_workflow.py; dispatch_workflow.py", "Notice; NoticeTrackerEvent", "integration-tested"),
    ("F-021", "Immutable notice PDF", "A4 rendering, specimen watermark, hashes, extraction checks, version download and diff", "notice_document.html", "/notices/{notice_id}/versions/{version_no}/pdf; diff API", "notice_workflow.py", "NoticeVersion", "integration-tested"),
    ("F-022", "Dispatch recording", "Portal, email, nodal-copy and simulated SAHYOG attempt records without delivery overclaim", "notice.html", "/notices/{notice_id}/dispatch; SAHYOG export", "dispatch_workflow.py; notices.py", "Dispatch; DispatchRecord", "fixture-tested"),
    ("F-023", "Dispatch tracker", "Filters, totals, manual status, outcome, notes, history, SLA and exports", "dispatch_tracker.html", "/dispatch-tracker and export/status routes", "dispatch_tracker.py; dispatch_workflow.py", "DispatchRecord; NoticeTrackerEvent; VaspResponse", "integration-tested"),
    ("F-024", "Escalation", "Case-specific owner chain, original ACP watcher, breach registration and bulk service operations", "dispatch_tracker.html; docket.html", "/dispatch-tracker/{notice_id}/escalate; /bulk", "dispatch_workflow.py; dispatch_tracker.py", "CaseEscalationAssignment; AuditOutbox", "integration-tested"),
    ("F-025", "Evidence exports", "Manifest, ZIP bundle, deterministic SVG exhibit, graph exports and methodology annex", "docket.html; trace.html; canvas.html", "evidence-manifest; evidence-bundle.zip; exhibit SVG", "exhibit.py; notices.py; evidence_store.py", "SourceCoverage; CanonicalTraceEvent", "integration-tested"),
    ("F-026", "Audit log", "Append-only hash chain, transactional outbox, filters, row inspection and integrity summary", "audit_log.html", "/audit-log", "audit.py; dispatch_workflow.py", "AuditOutbox", "integration-tested"),
    ("F-027", "Integration status", "Capability levels, feature gates, worker/budget telemetry and secret-safe readiness", "integrations.html", "/integrations; /api/integrations/status", "integrations.py; capabilities.py; feature_flags.py", "None", "integration-tested"),
    ("F-028", "Wallet watch", "Automatic/manual watch, tiers, priorities, provider budget, alerts and graph extension", "No dedicated current page", "gated background service", "watch.py; provider_budget.py", "WalletWatch; WatchAlert; WatchAlertEvent; WatchGraphExtension", "integration-tested"),
    ("F-029", "Protocol evidence boundaries", "CCTP, swap, correlation and unknown-operation records with strict attribution rules", "No live decoder page", "gated service contract", "protocols.py", "OperationBridgeLink", "fixture-tested"),
    ("F-030", "EVM and Bitcoin boundaries", "Address-family detection and explicit unavailable adapters", "risk.html; intake responses", "/api/chains/resolve", "engine/adapters/evm.py; engine/adapters/btc.py", "None", "unavailable"),
    ("F-031", "Search", "Small local record search service and API", "navigation-related use", "/api/search", "search.py", "Local fixture records", "fixture-tested"),
    ("F-032", "Health check", "Small process health endpoint for local operation", "None", "/healthz", "app/main.py", "None", "enabled"),
    ("F-033", "Responsive and accessible UI", "Drawer navigation, keyboard controls, focus behavior, small-screen layouts and local fonts", "all templates", "all page routes", "app.js; CSS files; role-guard.js", "None", "integration-tested"),
    ("F-034", "Demonstration workflow seed", "Nine separate workflow states without changing protected canonical fixture data", "docket and workflow pages", "fixture startup", "workflow_seed.py", "case/workflow tables", "fixture-tested"),
    ("F-035", "Database upgrades", "SQLite WAL, table creation, idempotent additive columns/indexes and uniqueness guards", "None", "application startup", "db.py", "all persisted tables", "integration-tested"),
]


def traceability_matrix() -> str:
    lines = [
        "# TRINETRA feature traceability matrix",
        "",
        "Snapshot date: 2026-09-20 IST.",
        "",
        "This is the compact map from user-visible capability to implementation evidence. The detailed handoff and generated appendices provide the full route, table, service and control catalogues.",
        "",
        "| ID | Feature | Included behavior | Main UI | Main route(s) | Main implementation | Stored records | Level |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in FEATURES:
        lines.append("| " + " | ".join(item.replace("|", "\\|") for item in row) + " |")
    lines.extend(
        [
            "",
            "Capability levels have their repository meaning: `unavailable`, `fixture-tested`, `integration-tested`, `live-verified`, or `enabled`. A contract or screen alone does not make an external capability live.",
            "",
        ]
    )
    return "\n".join(lines)


DIAGRAMS: dict[str, list[str]] = {
    "end-to-end": [
        "Officer signs in",
        "Complaint or live payment is entered",
        "Trace snapshot is created",
        "Events and evidence are stored",
        "Graph and explanations are reviewed",
        "Custody finding only if strict evidence gate passes",
        "Notice is authored and countersigned",
        "Dispatch attempt is recorded and tracked",
    ],
    "architecture": [
        "Browser: Jinja pages + local CSS/JavaScript",
        "FastAPI routes: authentication, cases, traces and workflow",
        "Services: attribution, evidence, risk, notices and dispatch",
        "Engine bridge: fixture/live trace boundary",
        "Providers: TronGrid only when every gate passes",
        "SQLite WAL + raw evidence files + hash-chained audit log",
    ],
    "fixture-live": [
        "Input received",
        "Select controlled fixture or gated live mode",
        "Validate address, chain, asset, amount and time",
        "Fixture: replay protected deterministic evidence",
        "Live: verify exact TRON USDT seed event",
        "Return a typed result; failure never becomes fixture custody",
    ],
    "provider-evidence": [
        "Provider request with exact bounds",
        "Raw response captured before normalization",
        "SHA-256 and retrieval metadata stored",
        "Schema checked and response normalized",
        "Canonical event and source coverage recorded",
        "UI and export cite the stored evidence identity",
    ],
    "frontier": [
        "Queued",
        "Leased to bounded worker",
        "Expanded from confirmed provider history",
        "Completed or deferred",
        "Retry time reached after provider backoff",
        "Released to queue or closed at evidence boundary",
    ],
    "notice": [
        "1. Parameters",
        "2. Attachments",
        "3. Generate immutable version",
        "4. Review and attest",
        "5. Re-verify identity",
        "6. Route to ACP or confirmation",
        "Countersign or return for amendment",
        "Record specimen dispatch attempt",
    ],
    "custody": [
        "Observed value reaches an address",
        "Is terminal kind permitted?",
        "Is deposit address present?",
        "Is credited amount explicitly present?",
        "Fixture VASP deposit or verified custody evidence?",
        "Create finding; otherwise record a non-custody terminal",
    ],
    "risk": [
        "Confirmed history and separate attribution sources",
        "Compute ten transparent feature families",
        "Attach exact evidence references",
        "Check whether history is sufficient",
        "Return evidence band or unknown",
        "Keep score and posterior null; audit the lookup",
    ],
    "dispatch": [
        "Countersigned version selected",
        "Local channel attempts recorded",
        "Shared dispatch record starts SLA clock",
        "Officer records acknowledgement or response",
        "Breach creates one audit event",
        "Escalation changes owner but keeps original ACP watching",
        "Only explicit VASP evidence can record restrained amount",
    ],
    "session": [
        "Hashed server session created",
        "Activity updates last-active time",
        "Short idle period obscures case content",
        "Officer can re-enter or extend",
        "Idle timeout revokes session",
        "Sign-out clears browser state and writes audit summary",
    ],
    "gate-tree": [
        "Provider key present",
        "Live feature explicitly requested",
        "Schema verified",
        "Smoke test verified",
        "Bounded trace verified",
        "Worker/watch/other risky capability uses its own additional gate",
    ],
    "evidence-chain": [
        "Seed and parameters sealed",
        "Provider payload or fixture evidence retained",
        "Canonical events and coverage stored",
        "Snapshot receives canonical SHA-256",
        "Exports include method, identity and audit reference",
        "Later work creates a new artifact; old artifacts are unchanged",
    ],
}


class FlowDiagram(Flowable):
    def __init__(self, labels: list[str], width: float):
        super().__init__()
        self.labels = labels
        self.width = width
        self.box_h = 0.88 * cm
        self.gap = 0.28 * cm
        self.height = len(labels) * self.box_h + max(0, len(labels) - 1) * self.gap

    def wrap(self, avail_width: float, avail_height: float):
        self.width = min(self.width, avail_width)
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        y = self.height - self.box_h
        for index, label in enumerate(self.labels):
            canvas.setFillColor(colors.HexColor("#EEF4F7"))
            canvas.setStrokeColor(colors.HexColor("#527286"))
            canvas.roundRect(0, y, self.width, self.box_h, 5, fill=1, stroke=1)
            canvas.setFillColor(colors.HexColor("#17313F"))
            canvas.setFont("NotoSans", 8.2)
            lines = textwrap.wrap(label, width=88) or [label]
            text_y = y + self.box_h / 2 + (len(lines) - 1) * 4
            for item in lines[:2]:
                canvas.drawCentredString(self.width / 2, text_y, item)
                text_y -= 9
            if index < len(self.labels) - 1:
                x = self.width / 2
                canvas.setStrokeColor(colors.HexColor("#527286"))
                canvas.line(x, y, x, y - self.gap + 2)
                canvas.line(x, y - self.gap + 2, x - 3, y - self.gap + 6)
                canvas.line(x, y - self.gap + 2, x + 3, y - self.gap + 6)
            y -= self.box_h + self.gap


def register_fonts() -> None:
    regular = ROOT / "app" / "static" / "fonts" / "noto-sans-400.ttf"
    medium = ROOT / "app" / "static" / "fonts" / "noto-sans-600.ttf"
    mono = ROOT / "app" / "static" / "fonts" / "noto-sans-mono-400.ttf"
    pdfmetrics.registerFont(TTFont("NotoSans", str(regular)))
    pdfmetrics.registerFont(TTFont("NotoSansBold", str(medium)))
    pdfmetrics.registerFont(TTFont("NotoMono", str(mono)))


def pdf_styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle(name="HandoffTitle", fontName="NotoSansBold", fontSize=25, leading=31, alignment=TA_CENTER, textColor=colors.HexColor("#143447"), spaceAfter=12))
    base.add(ParagraphStyle(name="HandoffSubtitle", fontName="NotoSans", fontSize=11, leading=16, alignment=TA_CENTER, textColor=colors.HexColor("#506776"), spaceAfter=16))
    base.add(ParagraphStyle(name="H1x", fontName="NotoSansBold", fontSize=16, leading=20, textColor=colors.HexColor("#143447"), spaceBefore=5, spaceAfter=9))
    base.add(ParagraphStyle(name="H2x", fontName="NotoSansBold", fontSize=12.2, leading=16, textColor=colors.HexColor("#245266"), spaceBefore=9, spaceAfter=5))
    base.add(ParagraphStyle(name="H3x", fontName="NotoSansBold", fontSize=10.2, leading=14, textColor=colors.HexColor("#355F70"), spaceBefore=7, spaceAfter=4))
    base.add(ParagraphStyle(name="Bodyx", fontName="NotoSans", fontSize=8.7, leading=12.3, textColor=colors.HexColor("#1E2D35"), spaceAfter=5))
    base.add(ParagraphStyle(name="Bulletx", fontName="NotoSans", fontSize=8.5, leading=12, leftIndent=13, firstLineIndent=-7, bulletIndent=5, textColor=colors.HexColor("#1E2D35"), spaceAfter=2))
    base.add(ParagraphStyle(name="Smallx", fontName="NotoSans", fontSize=7.4, leading=10, textColor=colors.HexColor("#51636D"), spaceAfter=3))
    base.add(ParagraphStyle(name="Monox", fontName="NotoMono", fontSize=6.8, leading=9.2, textColor=colors.HexColor("#17313F"), backColor=colors.HexColor("#F3F6F8"), borderPadding=5, spaceAfter=6))
    return base


def inline_markup(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(r"`([^`]+)`", r"<font name='NotoMono'>\1</font>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", escaped)
    escaped = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", escaped)
    return escaped


def markdown_table(rows: list[str], styles) -> Table:
    data: list[list[Paragraph]] = []
    for row in rows:
        values = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", value.replace(" ", "")) for value in values):
            continue
        data.append([Paragraph(inline_markup(value), styles["Smallx"]) for value in values])
    if not data:
        return Table([[""]])
    count = max(len(row) for row in data)
    for row in data:
        row.extend([Paragraph("", styles["Smallx"])] * (count - len(row)))
    widths = [16.5 * cm / count] * count
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE8EE")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#143447")),
        ("FONTNAME", (0, 0), (-1, 0), "NotoSansBold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B9C8D0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def markdown_story(markdown: str):
    styles = pdf_styles()
    story: list = []
    lines = markdown.splitlines()
    index = 0
    first_h1 = True
    while index < len(lines):
        line = lines[index].rstrip()
        if line.startswith("<div style="):
            story.append(PageBreak())
            index += 1
            continue
        diagram = re.fullmatch(r"<!-- DIAGRAM:([a-z0-9-]+) -->", line.strip())
        if diagram:
            labels = DIAGRAMS.get(diagram.group(1), [])
            if labels:
                story.extend([Spacer(1, 4), FlowDiagram(labels, 16.2 * cm), Spacer(1, 8)])
            index += 1
            continue
        if line.startswith("# "):
            if not first_h1:
                # Blank Markdown lines are represented as Spacer flowables.
                # Removing chapter-end spacers prevents an otherwise invisible
                # spacer from spilling onto a new sheet just before the chapter
                # break and creating a header-only page.
                while story and isinstance(story[-1], Spacer):
                    story.pop()
                # Start every chapter on a fresh page, but do not emit an empty
                # sheet when the preceding chapter ended exactly at a frame
                # boundary and ReportLab has already advanced to the next page.
                story.append(PageBreakIfNotEmpty())
            story.append(Paragraph(inline_markup(line[2:]), styles["H1x"]))
            first_h1 = False
            index += 1
            continue
        if line.startswith("## "):
            story.append(Paragraph(inline_markup(line[3:]), styles["H2x"]))
            index += 1
            continue
        if line.startswith("### "):
            story.append(Paragraph(inline_markup(line[4:]), styles["H3x"]))
            index += 1
            continue
        if line.startswith("```"):
            language = line[3:].strip()
            block: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            story.append(Preformatted("\n".join(block), styles["Monox"]))
            index += 1
            continue
        if line.startswith("|"):
            block = []
            while index < len(lines) and lines[index].startswith("|"):
                block.append(lines[index])
                index += 1
            story.extend([markdown_table(block, styles), Spacer(1, 7)])
            continue
        if re.match(r"^[-*] ", line):
            story.append(Paragraph("- " + inline_markup(line[2:]), styles["Bulletx"]))
            index += 1
            continue
        if re.match(r"^\d+\. ", line):
            story.append(Paragraph(inline_markup(line), styles["Bulletx"]))
            index += 1
            continue
        if not line.strip():
            story.append(Spacer(1, 2))
            index += 1
            continue
        paragraph = [line]
        index += 1
        while index < len(lines):
            next_line = lines[index].rstrip()
            if not next_line or next_line.startswith(("#", "|", "```", "<!--", "- ", "* ")) or re.match(r"^\d+\. ", next_line):
                break
            paragraph.append(next_line)
            index += 1
        story.append(Paragraph(inline_markup(" ".join(paragraph)), styles["Bodyx"]))
    return story


def header_footer(canvas, doc):
    canvas.saveState()
    page = canvas.getPageNumber()
    width, height = A4
    canvas.setStrokeColor(colors.HexColor("#C8D4DA"))
    canvas.line(2 * cm, height - 1.35 * cm, width - 2 * cm, height - 1.35 * cm)
    canvas.setFont("NotoSans", 7)
    canvas.setFillColor(colors.HexColor("#607580"))
    canvas.drawString(2 * cm, height - 1.05 * cm, "TRINETRA complete project handoff - 2026-09-20")
    canvas.drawRightString(width - 2 * cm, 0.95 * cm, f"Page {page}")
    canvas.drawString(2 * cm, 0.95 * cm, "Investigative aid prototype - capability claims use the repository status vocabulary")
    canvas.restoreState()


def build_pdf(markdown: str) -> None:
    register_fonts()
    PDF_OUT.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(PDF_OUT),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.7 * cm,
        bottomMargin=1.5 * cm,
        title="TRINETRA Complete Project Handoff",
        author="TRINETRA project team",
        subject="Complete technical and product handoff as of 2026-09-20",
    )
    document.build(markdown_story(markdown), onFirstPage=header_footer, onLaterPages=header_footer)
    reader = PdfReader(str(PDF_OUT))
    if len(reader.pages) < 20:
        raise RuntimeError(f"Expected a multi-page handoff; generated only {len(reader.pages)} pages")
    extracted = "\n".join((page.extract_text() or "") for page in reader.pages)
    required = [
        "TRINETRA",
        "Frontend",
        "Backend",
        "Tracing engine",
        "Custody findings",
        "Freeze-notice workflow",
        "Dispatch tracker",
        "Complete route catalogue",
        "Complete UI control catalogue",
    ]
    missing = [item for item in required if item.lower() not in extracted.lower()]
    if missing:
        raise RuntimeError(f"PDF text validation failed; missing: {missing}")


def main() -> None:
    master = write_outputs()
    build_pdf(master)
    reader = PdfReader(str(PDF_OUT))
    print(f"chapters={len(parse_chapters(SOURCE.read_text(encoding='utf-8')))}")
    print(f"master={MASTER}")
    print(f"traceability={TRACEABILITY}")
    print(f"pdf={PDF_OUT}")
    print(f"pdf_pages={len(reader.pages)}")


if __name__ == "__main__":
    main()
