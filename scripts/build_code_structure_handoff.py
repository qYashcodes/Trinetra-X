from __future__ import annotations

import ast
import html
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
PDF_OUT = ROOT / "output" / "pdf" / "TRINETRA_Code_Structure_Handoff_2026-09-20.pdf"
MD_OUT = ROOT / "output" / "TRINETRA_CODE_STRUCTURE_HANDOFF_2026-09-20.md"


@dataclass(frozen=True)
class ModuleSummary:
    path: str
    public_symbols: list[str]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _public_symbols(path: Path) -> list[str]:
    try:
        tree = ast.parse(_read(path))
    except SyntaxError:
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
    return names


def module_summaries(folder: str) -> list[ModuleSummary]:
    base = ROOT / folder
    rows: list[ModuleSummary] = []
    for path in sorted(base.glob("*.py")):
        if path.name == "__init__.py":
            continue
        rows.append(ModuleSummary(path.relative_to(ROOT).as_posix(), _public_symbols(path)))
    return rows


def route_rows() -> list[tuple[str, str, str]]:
    path = ROOT / "app" / "main.py"
    tree = ast.parse(_read(path))
    rows: list[tuple[str, str, str]] = []
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
            route = ast.unparse(decorator.args[0]).strip("'\"") if decorator.args else ""
            rows.append((method, route, node.name))
    return rows


def model_names() -> list[str]:
    path = ROOT / "app" / "models.py"
    tree = ast.parse(_read(path))
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if any(
            keyword.arg == "table"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        ):
            names.append(node.name)
    return names


def root_folder_rows() -> list[tuple[str, str, str]]:
    rows = [
        ("app/", "Runnable FastAPI product", "Routes, sessions, Jinja screens, services, persistence, static assets and integration shells."),
        ("engine/", "Trace/provider boundary", "Shared contracts plus blockchain adapters. TRON is operational behind gates; EVM and Bitcoin are explicit unavailable boundaries."),
        ("docs/", "Current briefs and controlled fixture", "Implementation status, build brief, decisions and `demo_case.json`, the protected canonical fixture source."),
        ("fixtures/", "Small deterministic fixture data", "Workflow seed JSON and TRON seed samples used by tests and demonstrations."),
        ("tests/", "Regression suite", "Truthfulness, trace, provider, UI, persistence, notice, dispatch, risk and workflow tests."),
        ("scripts/", "Local operation and artifact builders", "Demo reset/start helpers and ReportLab builders for handoff PDFs."),
        ("app/static/", "Vendored browser assets", "Local CSS, vanilla JavaScript, vendored Alpine, Cytoscape, Dagre, fonts and product marks."),
        ("app/templates/", "Server-rendered UI", "Jinja pages and partials for login, docket, trace, graph, findings, notices, risk and integrations."),
        ("omega/", "Separate prototype/reference package", "Independent multi-chain tracing prototype with its own static files, adapters and tests. It is not the primary runnable app."),
        ("kit/", "Historical archive", "Earlier product/architecture notes kept for context only. It does not override the root rulebook or current code."),
        ("output/", "Generated artifacts", "PDFs, source bundles, code zips and expanded handoff chapters. Treat as generated output, not active app source."),
        ("tmp/", "Temporary QA/runtime work", "Rendered PDF pages, Playwright screenshots and pytest temporary files."),
        ("var/", "Runtime state", "SQLite database, audit chain, raw provider evidence and local server logs. Generated and ignored by Git."),
        ("skills/", "Local Codex skill material", "Repository-specific agent support material; not imported by the app runtime."),
    ]
    return rows


def build_markdown() -> str:
    routes = route_rows()
    models = model_names()
    service_count = len(module_summaries("app/services"))
    template_count = len(list((ROOT / "app" / "templates").glob("*.html")))
    static_count = len([p for p in (ROOT / "app" / "static").rglob("*") if p.is_file()])
    tests = list((ROOT / "tests").glob("test_*.py"))

    rows = ["# TRINETRA Code Structure Handoff", ""]
    rows.extend(
        [
            "Generated: 2026-09-20 IST.",
            "",
            "Purpose: this is a compact codebase map for quickly understanding the cleaned-up TRINETRA repository. It explains what is now separated, what each folder contains, and which technologies power the frontend, backend, tracing engine, persistence and evidence workflow.",
            "",
            "Truthfulness note: TRINETRA is an investigative-aid prototype. The live trace can follow confirmed TRON/USDT transfers when gates and credentials are configured, but live custody/account attribution remains separate and gated. Fixture data under `engine/data/` and `docs/demo_case.json` is controlled demonstration data, not a production registry.",
            "",
            "## Quick Orientation",
            "",
            "| Area | Current shape |",
            "| --- | --- |",
            f"| FastAPI routes | {len(routes)} page/API handlers in `app/main.py` |",
            f"| SQLModel tables | {len(models)} persisted table models in `app/models.py` |",
            f"| Service modules | {service_count} modules under `app/services/` |",
            f"| Templates | {template_count} Jinja templates under `app/templates/` |",
            f"| Static files | {static_count} local browser assets under `app/static/` |",
            f"| Tests | {len(tests)} test files under `tests/` |",
            "",
            "The primary runnable product is the repository root. The `omega/`, `kit/`, `output/` and `tmp/` trees are useful, but they are not the main application path.",
            "",
            "## Repository And Folder Separation",
            "",
            "| Folder | Role | What it contains |",
            "| --- | --- | --- |",
        ]
    )
    for folder, role, content in root_folder_rows():
        rows.append(f"| `{folder}` | {role} | {content} |")

    rows.extend(
        [
            "",
            "## Main Application: `app/`",
            "",
            "The `app/` folder is the actual FastAPI product. Its split is now clear enough that a reader can understand the system without opening every file.",
            "",
            "| Subfolder/file | Responsibility |",
            "| --- | --- |",
            "| `app/main.py` | FastAPI application factory, lifespan, middleware, routes, session handling, evidence exports and page rendering. |",
            "| `app/engine_bridge.py` | Typed tracing boundary: chain/asset/seed/params/result types, fixture/live mode selection, validation, trace serialization and fail-closed terminals. |",
            "| `app/models.py` | SQLModel data model for cases, trace snapshots, evidence, frontier work, custody, notices, dispatch, sessions, watch alerts and audit outbox. |",
            "| `app/repository.py` | Main transactional persistence layer for cases, trace snapshots, events, canonical evidence, frontier rows, custody assertions and findings. |",
            "| `app/db.py` | SQLite engine, WAL activation and additive schema/index upgrades. There is no Alembic migration system. |",
            "| `app/settings.py` | Environment-backed settings and feature-related runtime paths. |",
            "| `app/services/` | Business logic modules so routes do not directly own attribution, evidence, risk, notice, dispatch or session rules. |",
            "| `app/templates/` | Server-rendered UI pages and reusable partials. |",
            "| `app/static/` | Local CSS, JavaScript, fonts, graph libraries and SVG marks. No CDN or build pipeline is used. |",
            "| `app/integrations/` | Complaint and dispatch connector contracts plus fixture/mock implementations. |",
            "| `app/providers/` | OIDC and WebAuthn integration shells. They are not production SSO. |",
            "",
            "### Backend Stack",
            "",
            "- Python `>=3.11,<3.13`.",
            "- FastAPI and Uvicorn for HTTP serving.",
            "- Jinja2 for server-rendered HTML.",
            "- SQLModel on SQLAlchemy with SQLite in WAL mode.",
            "- Starlette session middleware and CSRF checks for workflow forms.",
            "- `requests` for provider HTTP.",
            "- `sse-starlette` for trace event streaming, with JSON fallback.",
            "- pypdf, ReportLab and Playwright support document/export workflows.",
            "- pytest, HTTPX and pytest-playwright cover local verification.",
            "",
            "### Frontend Stack",
            "",
            "- Jinja2 templates with local CSS and vanilla JavaScript.",
            "- Vendored Alpine.js for small client-side state.",
            "- Vendored Cytoscape.js and Dagre for graph/canvas rendering.",
            "- Vendored Noto fonts under `app/static/fonts/`.",
            "- No React, no frontend build step, no CDN references.",
            "",
            "## Tracing Engine Separation",
            "",
            "The tracing logic is deliberately split between app orchestration and provider-specific adapters.",
            "",
            "| Layer | Main files | Responsibility |",
            "| --- | --- | --- |",
            "| Engine boundary | `app/engine_bridge.py` | Defines `TraceSeed`, `TraceParams`, `TraceResult`, fixture/live modes, cache identity, terminal mapping and `run_trace()`. |",
            "| Provider contracts | `engine/contracts.py` | Defines normalized transfer record shape shared by adapters. |",
            "| TRON adapter | `engine/adapters/tron.py` | TronGrid V1 HTTP, confirmed TRC-20 history, seed event verification, pagination, schema checks, raw evidence hooks and normalization. |",
            "| Unavailable adapters | `engine/adapters/evm.py`, `engine/adapters/btc.py` | Explicitly report unavailable EVM and Bitcoin tracing rather than pretending multi-chain support is live. |",
            "| Fixture data | `engine/data/` | Controlled registry fixtures used only for the demo and tests. |",
            "| Allocation | `app/services/allocation.py` | Integer-only proportional attribution and deterministic scheduling. |",
            "| Frontier queue | `app/services/frontier.py` | Persisted queued/deferred/leased frontier work and deferral reasons. |",
            "| Resume/worker expansion | `app/services/live_tron_resume.py`, `app/services/worker.py` | Bounded expansion of persisted TRON frontier rows, retries, provider backoff and supervised worker telemetry. |",
            "",
            "The fixture path replays protected deterministic evidence. The live path validates a TRON/USDT seed, verifies exact provider evidence and follows bounded confirmed transfers. Unsupported chains, invalid seeds, provider errors and disabled gates close with non-custody terminals.",
            "",
            "### Fund Attribution",
            "",
            "All money amounts are integer base units. Allocation uses proportional integer arithmetic with residual carry: `outgoing_attributed_base = (outgoing_base * attributed_base + residual_numerator) // balance_base`. This avoids floating-point drift and keeps zero values evidentiary.",
            "",
            "The synchronous live helper currently uses `max(incoming attributed amount, observed outgoing total)` as the allocation denominator. That is a bounded approximation, not full historical balance reconstruction.",
            "",
            "## Persistence And Evidence",
            "",
            "SQLite stores structured case/workflow/evidence state. Raw provider evidence and generated runtime artifacts live on disk under `var/`; exported artifacts live under `output/`.",
            "",
            "| Persisted group | Representative tables | Meaning |",
            "| --- | --- | --- |",
            "| Cases and assignments | `Case`, `CaseAssignment`, `CaseWatcher`, `OfficerProfile` | Case identity, officer scope and workflow ownership. |",
            "| Trace evidence | `TraceSnapshot`, `TraceEvent`, `CanonicalTraceEvent`, `SourceCoverage`, `AttributedLot`, `FrontierItem` | Sealed trace result, event replay, canonical evidence, provider coverage and resumable frontier state. |",
            "| Custody/provider contracts | `CustodyAssertion`, `ProviderResponseRecord`, ledger/KYC/trade/withdrawal/session rows, `CustodyAction` | Storage contracts for reviewed custody and provider evidence. Contracts exist locally; production imports remain gated. |",
            "| Notices and dispatch | `Finding`, `Notice`, `NoticeDraft`, `NoticeVersion`, `NoticeAttachment`, `Dispatch`, `DispatchRecord`, `NoticeTrackerEvent` | Review, immutable notice versions, specimen dispatch records and SLA tracker state. |",
            "| Identity and audit | `OfficerSession`, `WebAuthnCredential`, `OidcIdentity`, `AuditOutbox` | Prototype sessions, integration-shell credentials and transactional audit outbox. |",
            "| Watch and alerts | `WalletWatch`, `WatchAlert`, `WatchAlertEvent`, `WatchGraphExtension` | Watchlist and alert contracts; observation-only semantics are preserved. |",
            "",
            "Important persistence rule: snapshots, trace events, canonical evidence, frontier rows and exported artifacts are superseded, not deleted. Retracing creates a new snapshot while retaining prior evidence state.",
            "",
            "## Workflow Services",
            "",
            "| Service file | What it owns |",
            "| --- | --- |",
        ]
    )
    service_notes = {
        "accounting.py": "Per-asset conservation checks across custody, stationary, deferred and unresolved buckets.",
        "audit.py": "Append-only hash-chained audit JSONL and transactional outbox flushing.",
        "behavior.py": "Transparent behavioral feature extraction and evidence-band assignment.",
        "capabilities.py": "Capability matrix vocabulary and status rows.",
        "custody.py": "Custody/provider response import contracts and action-state validation.",
        "dispatch_workflow.py": "Dispatch records, SLA windows, breach events and escalation.",
        "evidence_store.py": "Raw provider payload capture, SHA-256, schema status and coverage persistence.",
        "explainability.py": "Investigator/technical explanation registers and methodology annex text.",
        "graph_view.py": "Canvas/graph payload construction.",
        "live_trace.py": "Live intake parsing, runtime status and stop summaries.",
        "notice_workflow.py": "Six-stage notice drafting, attachments, immutable versions, attestation and PDF rendering.",
        "risk.py": "Fixture/live risk check API shape, local case context and auditable lookup results.",
        "sessions.py": "Hashed server-side officer sessions, expiry, revocation and sign-out summaries.",
        "strategy.py": "Dominant-flow/value-weighted strategy projection and graph comparison.",
        "watch.py": "Wallet watch synchronization, alert lifecycle and graph extension contracts.",
        "worker.py": "Feature-gated supervised frontier worker lifecycle and telemetry.",
    }
    for summary in module_summaries("app/services"):
        name = Path(summary.path).name
        if name in service_notes:
            rows.append(f"| `{summary.path}` | {service_notes[name]} |")

    rows.extend(
        [
            "",
            "## User-Facing Screens",
            "",
            "| Screen group | Templates | Main routes |",
            "| --- | --- | --- |",
            "| Authentication and sessions | `login.html`, `supervisor_login.html`, `sessions.html`, `_session_controls.html` | `/login`, `/sessions`, logout and extension routes |",
            "| Case workspace | `docket.html`, `case_intake.html`, `live_intake.html`, `_workspace_nav.html` | `/docket`, `/cases/new`, `/cases/live/new` |",
            "| Trace and graph | `trace.html`, `live_trace.html`, `canvas.html`, `live_canvas.html`, `_trace_explainability.html`, `_omega_graph.html` | `/traces/{id}`, `/cases/{id}/canvas`, trace APIs |",
            "| Finding and notice | `finding.html`, `notice.html`, `notice_document.html`, `notice_workflow.html` | `/findings/{id}`, `/notices`, notice workflow routes |",
            "| Dispatch and integrations | `dispatch_tracker.html`, `integrations.html`, `integration_pending.html` | `/dispatch-tracker`, `/integrations` |",
            "| Risk and audit | `risk.html`, `audit_log.html` | `/risk-check`, `/audit-log` |",
            "",
            "## Current Capability Levels",
            "",
            "| Capability | Level | Notes |",
            "| --- | --- | --- |",
            "| Fixture complaint and canonical trace demo | `fixture-tested` | Protected deterministic flow; do not alter fixture wording/data unless explicitly asked. |",
            "| Bounded live TRON/USDT tracing | `live-verified` | Status document records credentialed verification on 2026-09-15. Requires gates and key in the user's environment. |",
            "| Frontier resume/worker mechanics | `integration-tested` | Leasing, retry and backoff are locally tested; production long-running recovery remains separately verified. |",
            "| Notice workflow and immutable PDF | `integration-tested` | Local PDF/workflow tests exist; legal copy remains pending review. |",
            "| SAHYOG route | `fixture-tested` | Specimen/simulated export only. No live government submission is claimed. |",
            "| EVM and Bitcoin tracing | `unavailable` | Address families may be detected; adapters are explicit unavailable boundaries. |",
            "| Machine learning/probabilities | `unavailable` | Scores/posteriors remain null; no trained or adaptive model is active. |",
            "| Production SSO, dispatch, freeze or restraint | `unavailable` | Requires approved credentials, schemas, authority and evidence configuration. |",
            "",
            "## Generated Bundles And Separate Trees",
            "",
            "`output/` contains many generated PDFs, source zips and expanded markdown chapters. These are useful handoff artifacts, but the runnable source of truth remains the root `app/`, `engine/`, `docs/`, `fixtures/`, `scripts/` and `tests/` folders.",
            "",
            "`omega/` is a separate prototype/reference package with its own adapters and static UI. It can inform ideas, but it is not wired as the main TRINETRA FastAPI product.",
            "",
            "`kit/` is historical source material. The root rulebook says it is archive/context only and never overrides the current code.",
            "",
            "## How A Request Moves Through The System",
            "",
            "1. Officer signs in through the prototype login and receives a server-side session.",
            "2. A fixture complaint or live TRON seed creates/updates a `Case`.",
            "3. `get_or_create_trace()` derives a cache identity and calls the engine boundary.",
            "4. Fixture mode replays protected evidence; live mode validates the seed and provider gates.",
            "5. The trace result is sealed into `TraceSnapshot`, ordered `TraceEvent` rows, canonical evidence, source coverage and frontier rows.",
            "6. The trace page streams events and the canvas renders graph state from the sealed snapshot.",
            "7. A finding is created only if the terminal kind, deposit address and credited amount gate passes.",
            "8. Notice workflow creates drafts, immutable versions, attachments, attestation and countersignature state.",
            "9. Dispatch tracker records specimen/local channel attempts and SLA state without claiming confirmed restraint.",
            "10. Evidence manifests and bundles export the case, trace, graph, methodology, identity and audit references.",
            "",
            "## Configuration And Runtime",
            "",
            "- Safe default mode is fixture mode: `TRINETRA_MODE=fixture`.",
            "- Live TRON requires `TRINETRA_MODE=live`, `TRINETRA_ENABLE_LIVE_TRON=true`, `TRONGRID_API_KEY`, schema/smoke/trace verification gates and relevant worker gates.",
            "- A provider key alone never enables risky behavior.",
            "- `.env` must not be printed. `.env.example` is the safe documentation surface.",
            "- Run locally with one Uvicorn worker because the prototype uses SQLite and local state.",
            "- Generated runtime state belongs in `var/`; final artifacts belong in `output/`; temporary render/test files belong in `tmp/`.",
            "",
            "## Test Map",
            "",
            "The suite is organized by risk area: allocation/scheduling, provider worker, engine truthfulness, live data operation, evidence store, database upgrades, frontend integrity, finding/notice integrity, dispatch workflow, notice workflow v3, behavioral risk, protocol boundaries, session lifecycle and app smoke coverage.",
            "",
            "## What To Read First",
            "",
            "For a new developer: `AGENTS.md`, `docs/CLAUDE_PROJECT_HANDOFF.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/V2_BUILD_BRIEF.md`, `app/engine_bridge.py`, `engine/adapters/tron.py`, `app/services/allocation.py`, `app/services/frontier.py`, `app/services/live_tron_resume.py`, `app/repository.py`, `app/models.py`, `app/main.py`, then nearest tests.",
            "",
        ]
    )
    return "\n".join(rows)


class FlowDiagram(Flowable):
    def __init__(self, labels: list[str], width: float):
        super().__init__()
        self.labels = labels
        self.width = width
        self.box_h = 0.92 * cm
        self.gap = 0.22 * cm
        self.height = len(labels) * self.box_h + max(0, len(labels) - 1) * self.gap

    def wrap(self, avail_width: float, avail_height: float):
        self.width = min(self.width, avail_width)
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        y = self.height - self.box_h
        for index, label in enumerate(self.labels):
            canvas.setFillColor(colors.HexColor("#F2F7FA"))
            canvas.setStrokeColor(colors.HexColor("#557789"))
            canvas.roundRect(0, y, self.width, self.box_h, 5, fill=1, stroke=1)
            canvas.setFillColor(colors.HexColor("#153241"))
            canvas.setFont("NotoSans", 8)
            wrapped = textwrap.wrap(label, width=82)[:2] or [label]
            text_y = y + self.box_h / 2 + (len(wrapped) - 1) * 4
            for line in wrapped:
                canvas.drawCentredString(self.width / 2, text_y, line)
                text_y -= 9
            if index < len(self.labels) - 1:
                x = self.width / 2
                canvas.setStrokeColor(colors.HexColor("#557789"))
                canvas.line(x, y, x, y - self.gap + 2)
            y -= self.box_h + self.gap


def register_fonts() -> None:
    font_dir = ROOT / "app" / "static" / "fonts"
    pdfmetrics.registerFont(TTFont("NotoSans", str(font_dir / "noto-sans-400.ttf")))
    pdfmetrics.registerFont(TTFont("NotoSansBold", str(font_dir / "noto-sans-700.ttf")))
    pdfmetrics.registerFont(TTFont("NotoMono", str(font_dir / "noto-sans-mono-400.ttf")))


def styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle(name="CoverTitle", fontName="NotoSansBold", fontSize=24, leading=30, alignment=TA_CENTER, textColor=colors.HexColor("#123444"), spaceAfter=10))
    base.add(ParagraphStyle(name="CoverSub", fontName="NotoSans", fontSize=10.5, leading=15, alignment=TA_CENTER, textColor=colors.HexColor("#596E78"), spaceAfter=16))
    base.add(ParagraphStyle(name="H1x", fontName="NotoSansBold", fontSize=15.5, leading=19, textColor=colors.HexColor("#123444"), spaceBefore=6, spaceAfter=8))
    base.add(ParagraphStyle(name="H2x", fontName="NotoSansBold", fontSize=11.5, leading=14, textColor=colors.HexColor("#245266"), spaceBefore=8, spaceAfter=5))
    base.add(ParagraphStyle(name="Bodyx", fontName="NotoSans", fontSize=8.5, leading=12, textColor=colors.HexColor("#1E2D35"), spaceAfter=5))
    base.add(ParagraphStyle(name="Smallx", fontName="NotoSans", fontSize=7.2, leading=9.4, textColor=colors.HexColor("#32454F"), spaceAfter=2))
    base.add(ParagraphStyle(name="Bulletx", fontName="NotoSans", fontSize=8.3, leading=11.5, leftIndent=12, firstLineIndent=-7, textColor=colors.HexColor("#1E2D35"), spaceAfter=2))
    base.add(ParagraphStyle(name="Monox", fontName="NotoMono", fontSize=7.0, leading=9.3, textColor=colors.HexColor("#153241"), backColor=colors.HexColor("#F3F6F8"), borderPadding=5, spaceAfter=6))
    return base


def inline_markup(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(r"`([^`]+)`", r"<font name='NotoMono'>\1</font>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def table_from_markdown(block: list[str], s) -> Table:
    data: list[list[Paragraph]] = []
    for row in block:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        data.append([Paragraph(inline_markup(cell), s["Smallx"]) for cell in cells])
    max_cols = max((len(row) for row in data), default=1)
    for row in data:
        row.extend([Paragraph("", s["Smallx"])] * (max_cols - len(row)))
    widths = [16.4 * cm / max_cols] * max_cols
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEBF0")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C8CF")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def story_from_markdown(markdown: str):
    s = styles()
    story = [
        Spacer(1, 2.0 * cm),
        Paragraph("TRINETRA Code Structure Handoff", s["CoverTitle"]),
        Paragraph("Repository map, separated responsibilities, tech stack and operating model", s["CoverSub"]),
        FlowDiagram(
            [
                "Browser UI: Jinja templates, local CSS and vanilla JavaScript",
                "FastAPI app: routes, sessions, forms, exports and evidence APIs",
                "Services: attribution, evidence, risk, notice, dispatch and audit rules",
                "Engine bridge: fixture/live trace boundary and fail-closed terminals",
                "Adapters: TronGrid provider contract; EVM/BTC unavailable boundaries",
                "Storage: SQLite WAL, raw evidence files, artifacts and audit chain",
            ],
            15.4 * cm,
        ),
        PageBreak(),
    ]
    lines = markdown.splitlines()
    index = 0
    first_heading = True
    while index < len(lines):
        line = lines[index].rstrip()
        if line.startswith("# "):
            if not first_heading:
                story.append(PageBreak())
            first_heading = False
            story.append(Paragraph(inline_markup(line[2:]), s["H1x"]))
            index += 1
            continue
        if line.startswith("## "):
            if not first_heading:
                story.append(PageBreak())
            story.append(Paragraph(inline_markup(line[3:]), s["H1x"]))
            index += 1
            continue
        if line.startswith("### "):
            story.append(Paragraph(inline_markup(line[4:]), s["H2x"]))
            index += 1
            continue
        if line.startswith("|"):
            block = []
            while index < len(lines) and lines[index].startswith("|"):
                block.append(lines[index])
                index += 1
            story.append(table_from_markdown(block, s))
            story.append(Spacer(1, 7))
            continue
        if line.startswith("- "):
            story.append(Paragraph("- " + inline_markup(line[2:]), s["Bulletx"]))
            index += 1
            continue
        if re.match(r"^\d+\. ", line):
            story.append(Paragraph(inline_markup(line), s["Bulletx"]))
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
            if not next_line or next_line.startswith(("#", "|", "- ")) or re.match(r"^\d+\. ", next_line):
                break
            paragraph.append(next_line)
            index += 1
        story.append(Paragraph(inline_markup(" ".join(paragraph)), s["Bodyx"]))
    return story


def header_footer(canvas, doc):
    canvas.saveState()
    page = canvas.getPageNumber()
    width, height = A4
    canvas.setStrokeColor(colors.HexColor("#C8D5DB"))
    canvas.line(1.8 * cm, height - 1.25 * cm, width - 1.8 * cm, height - 1.25 * cm)
    canvas.setFont("NotoSans", 7)
    canvas.setFillColor(colors.HexColor("#607580"))
    canvas.drawString(1.8 * cm, height - 0.95 * cm, "TRINETRA code structure handoff - 2026-09-20")
    canvas.drawRightString(width - 1.8 * cm, 0.9 * cm, f"Page {page}")
    canvas.drawString(1.8 * cm, 0.9 * cm, "Generated from the current repository; capability claims use repository status vocabulary")
    canvas.restoreState()


def build_pdf(markdown: str) -> None:
    register_fonts()
    PDF_OUT.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(PDF_OUT),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.55 * cm,
        bottomMargin=1.45 * cm,
        title="TRINETRA Code Structure Handoff",
        author="TRINETRA project team",
        subject="Code structure, repository map and technology handoff",
    )
    document.build(story_from_markdown(markdown), onFirstPage=header_footer, onLaterPages=header_footer)
    reader = PdfReader(str(PDF_OUT))
    extracted = "\n".join((page.extract_text() or "") for page in reader.pages)
    required = [
        "Repository And Folder Separation",
        "Main Application",
        "Tracing Engine Separation",
        "Persistence And Evidence",
        "Backend Stack",
        "Frontend Stack",
        "Generated Bundles",
    ]
    missing = [item for item in required if item.lower() not in extracted.lower()]
    if missing:
        raise RuntimeError(f"PDF text validation failed; missing: {missing}")
    if len(reader.pages) < 12:
        raise RuntimeError(f"Expected at least 12 pages; generated {len(reader.pages)}")


def main() -> None:
    markdown = build_markdown()
    MD_OUT.write_text(markdown, encoding="utf-8", newline="\n")
    build_pdf(markdown)
    reader = PdfReader(str(PDF_OUT))
    print(f"markdown={MD_OUT}")
    print(f"pdf={PDF_OUT}")
    print(f"pages={len(reader.pages)}")


if __name__ == "__main__":
    main()
