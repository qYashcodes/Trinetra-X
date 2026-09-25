from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "pdf" / "trinetra_technical_flow_handoff.pdf"


class ArrowFlow(Flowable):
    def __init__(self, labels: list[str], width: float, box_height: float = 1.15 * cm):
        super().__init__()
        self.labels = labels
        self.width = width
        self.box_height = box_height
        self.height = len(labels) * box_height + (len(labels) - 1) * 0.35 * cm

    def wrap(self, availWidth, availHeight):
        return min(self.width, availWidth), self.height

    def draw(self):
        c = self.canv
        box_w = self.width
        y = self.height - self.box_height
        for index, label in enumerate(self.labels):
            c.setFillColor(colors.HexColor("#F5F7FA"))
            c.setStrokeColor(colors.HexColor("#2D4A6A"))
            c.roundRect(0, y, box_w, self.box_height, 5, stroke=1, fill=1)
            c.setFillColor(colors.HexColor("#152536"))
            c.setFont("Helvetica-Bold", 8.5)
            lines = label.split("\n")
            line_y = y + self.box_height / 2 + (len(lines) - 1) * 4
            for line in lines:
                c.drawCentredString(box_w / 2, line_y, line)
                line_y -= 9
            if index < len(self.labels) - 1:
                c.setStrokeColor(colors.HexColor("#5D6B7A"))
                x = box_w / 2
                c.line(x, y - 0.04 * cm, x, y - 0.28 * cm)
                c.line(x, y - 0.28 * cm, x - 3, y - 0.20 * cm)
                c.line(x, y - 0.28 * cm, x + 3, y - 0.20 * cm)
            y -= self.box_height + 0.35 * cm


def styles():
    base = getSampleStyleSheet()
    base.add(
        ParagraphStyle(
            name="CoverTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=29,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#102030"),
            spaceAfter=10,
        )
    )
    base.add(
        ParagraphStyle(
            name="Subtitle",
            parent=base["BodyText"],
            fontSize=10.5,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#52606D"),
            spaceAfter=14,
        )
    )
    base.add(
        ParagraphStyle(
            name="Section",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#193B5A"),
            spaceBefore=12,
            spaceAfter=6,
        )
    )
    base.add(
        ParagraphStyle(
            name="Subsection",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            textColor=colors.HexColor("#1F465F"),
            spaceBefore=8,
            spaceAfter=4,
        )
    )
    base.add(
        ParagraphStyle(
            name="Body",
            parent=base["BodyText"],
            fontSize=9,
            leading=12.5,
            textColor=colors.HexColor("#1E2933"),
            spaceAfter=5,
        )
    )
    base.add(
        ParagraphStyle(
            name="Small",
            parent=base["BodyText"],
            fontSize=8,
            leading=10.5,
            textColor=colors.HexColor("#3E4C59"),
            spaceAfter=4,
        )
    )
    base.add(
        ParagraphStyle(
            name="BoxTitle",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=colors.white,
        )
    )
    base.add(
        ParagraphStyle(
            name="MonoBlock",
            fontName="Courier",
            fontSize=7.4,
            leading=9.2,
            textColor=colors.HexColor("#12263A"),
        )
    )
    base.add(
        ParagraphStyle(
            name="CustomBullet",
            parent=base["BodyText"],
            fontSize=8.8,
            leading=11.8,
            leftIndent=0,
            bulletIndent=0,
            textColor=colors.HexColor("#1E2933"),
        )
    )
    return base


S = styles()


def bullets(items: list[str]) -> ListFlowable:
    rows = [[Paragraph(f"- {item}", S["CustomBullet"])] for item in items]
    table = Table(rows, colWidths=[16.2 * cm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def note_box(title: str, body: list[str], fill="#F7FAFC", stroke="#B8C4CF") -> Table:
    rows = [[Paragraph(title, S["BoxTitle"])]]
    rows.extend([[Paragraph(line, S["Small"])] for line in body])
    table = Table(rows, colWidths=[16.4 * cm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#193B5A")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor(fill)),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(stroke)),
                ("INNERPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def table_from_pairs(rows: list[tuple[str, str]]) -> Table:
    data = [[Paragraph(f"<b>{left}</b>", S["Small"]), Paragraph(right, S["Small"])] for left, right in rows]
    table = Table(data, colWidths=[4.1 * cm, 12.1 * cm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8DEE6")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("INNERPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#708090"))
    canvas.drawString(1.7 * cm, 1.05 * cm, "TRINETRA technical flow PPT handoff")
    canvas.drawRightString(A4[0] - 1.7 * cm, 1.05 * cm, f"Page {doc.page}")
    canvas.restoreState()


def build() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.7 * cm,
        bottomMargin=1.55 * cm,
    )
    story = []

    story.append(Spacer(1, 2.0 * cm))
    story.append(Paragraph("TRINETRA Technical Flow PPT Handoff", S["CoverTitle"]))
    story.append(
        Paragraph(
            "Slide-ready architecture, data flow, tech stack, feasibility, and speaker notes for a judge-friendly presentation.",
            S["Subtitle"],
        )
    )
    story.append(HRFlowable(width="70%", thickness=1, color=colors.HexColor("#8FA6B8")))
    story.append(Spacer(1, 0.7 * cm))
    story.append(
        note_box(
            "User Goal",
            [
                "Redesign the technical flow slide(s) for TRINETRA so that a judge or evaluator with low technical/domain knowledge can understand the prototype flow clearly.",
                "The slide must explain the full technical journey in simple language, without being over-explanatory or too vague.",
            ],
        )
    )
    story.append(Spacer(1, 0.35 * cm))
    story.append(
        note_box(
            "Core Accuracy Constraint",
            [
                "Do not claim that CCTNS, SAHYOG SSO, or live SAHYOG submission are fully integrated.",
                "Use: Prototype login and specimen SAHYOG export are implemented. Production CCTNS/SAHYOG SSO and live portal submission require approved credentials, schemas, and authority configuration.",
                "Present TRINETRA as an investigative aid, not an accusation engine.",
            ],
            fill="#FFF8E8",
            stroke="#D9B44A",
        )
    )
    story.append(PageBreak())

    story.append(Paragraph("Explicit Instructions Provided By User", S["Section"]))
    story.append(Paragraph("Key Components of the Technical Flow Slide", S["Subsection"]))
    story.append(
        bullets(
            [
                "<b>System Architecture Diagram:</b> A high-level block diagram showing key components such as the Frontend, Backend, Database, and Cloud Services.",
                "<b>Data Flow:</b> A clear input-process-output cycle, such as Candidate Inputs -> AI Processing -> Response Evaluation -> Feedback Generation.",
                "<b>Tech Stack:</b> Specific listing of technologies used, including programming languages, frameworks, AI/ML tools, and APIs.",
                "<b>Feasibility Statement:</b> A brief explanation of why the solution is buildable within the hackathon timeframe, including prototype status or workflow logic.",
            ]
        )
    )
    story.append(Paragraph("Best Practices for This Slide", S["Subsection"]))
    story.append(
        bullets(
            [
                "<b>Avoid walls of text:</b> Use bullets, max 6 per slide, and diagrams instead of paragraphs.",
                "<b>Be specific:</b> Do not just say AI; specify models or libraries and how they integrate into the architecture.",
                "<b>Visual clarity:</b> Clearly distinguish user interface, processing logic, and data storage.",
                "<b>Alignment with PS:</b> Ensure the technical flow directly addresses the constraints and requirements of the problem statement.",
            ]
        )
    )
    story.append(Paragraph("Example Structure Provided", S["Subsection"]))
    story.append(
        bullets(
            [
                "Title: Technical Approach & System Architecture.",
                "Visual: High-level architecture diagram, Input -> Processing -> Output.",
                "Tech Stack: Frontend, Backend, AI, Database.",
                "Process Flow: Step-by-step logic such as Data Fetch -> Preprocess -> Model Inference -> Alert Generation.",
                "Feasibility: Note on data sources and prototype readiness.",
            ]
        )
    )

    story.append(Paragraph("TRINETRA-Specific Adjustment", S["Section"]))
    story.append(
        Paragraph(
            "The generic examples mention React, Flask, TensorFlow, OpenCV, AWS, Azure, IMD, and Bhashini. Do not include those unless actually used. For TRINETRA, the accurate stack is Python, FastAPI, Jinja2, vanilla JavaScript, SQLite WAL, SQLModel, Cytoscape.js, Dagre, TronGrid APIs, SHA-256 evidence storage, server sessions, CSRF, and an append-only audit chain.",
            S["Body"],
        )
    )
    story.append(
        table_from_pairs(
            [
                ("Avoid saying", "AI proves guilt; wallet is criminal; exchange account is frozen automatically; SAHYOG dispatch is live; CCTNS login is fully integrated."),
                ("Use instead", "Investigative aid; evidence boundary; custody attribution; confirmed blockchain transfer; trace snapshot; audit trail; specimen export; unknown where evidence is insufficient."),
            ]
        )
    )
    story.append(PageBreak())

    story.append(Paragraph("Recommended Slide", S["Section"]))
    story.append(Paragraph("Slide Title: Technical Approach & System Architecture", S["Subsection"]))
    story.append(Paragraph("Main Architecture Diagram", S["Subsection"]))
    arch_labels = [
        "Officer / Supervisor",
        "Frontend UI\nFastAPI + Jinja2 screens",
        "Backend Logic\nTrace engine, validation, allocation, audit",
        "Blockchain / Case Inputs\nReport ID, wallet, tx hash, confirmed USDT data",
        "Database + Evidence Store\nSQLite WAL, raw evidence, snapshots, audit logs",
        "Outputs\nGraph, boundary result, evidence bundle, notice draft",
    ]
    story.append(ArrowFlow(arch_labels, 16.2 * cm))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("Slide Bullet Points", S["Subsection"]))
    story.append(
        bullets(
            [
                "<b>Input:</b> Officer enters cyber-crime report ID or live TRON wallet / transaction hash.",
                "<b>Validation:</b> System checks chain, asset, amount, timestamp, and transaction match before tracing.",
                "<b>Processing:</b> Trace engine follows confirmed blockchain transfers hop-by-hop using deterministic fund-flow logic.",
                "<b>Evidence:</b> Raw provider data, trace snapshots, hashes, and audit records are stored for review.",
                "<b>Review:</b> Custody finding is created only when required evidence is present; otherwise the system stops at an evidence boundary.",
                "<b>Output:</b> Graph view, explanation panel, evidence bundle, notice draft, and SAHYOG specimen export.",
            ]
        )
    )
    story.append(PageBreak())

    story.append(Paragraph("Tech Stack Box", S["Section"]))
    story.append(
        table_from_pairs(
            [
                ("Frontend", "Jinja2 templates, vanilla JavaScript, local CSS, Alpine.js, Cytoscape.js, Dagre."),
                ("Backend", "Python, FastAPI, Uvicorn."),
                ("Database", "SQLite in WAL mode, SQLModel."),
                ("Evidence / Documents", "JSON evidence store, SHA-256 hashing, pypdf, Playwright."),
                ("Blockchain Provider", "TRON / USDT live trace via TronGrid APIs."),
                ("Security / Workflow", "Server-side sessions, CSRF protection, role-based officer/supervisor workflow, append-only audit chain."),
            ]
        )
    )
    story.append(Spacer(1, 0.25 * cm))
    story.append(
        note_box(
            "Do Not List",
            [
                "Do not list React, Flask, TensorFlow, OpenCV, AWS, Azure, IMD, or Bhashini unless the project actually uses them.",
                "For this prototype, the stronger point is that it is a working offline-first FastAPI product rather than a generic cloud/AI idea.",
            ],
            fill="#FFF5F5",
            stroke="#D66A6A",
        )
    )

    story.append(Paragraph("Data Flow", S["Section"]))
    data_labels = [
        "Cyber-crime Report ID / Wallet / Tx Hash",
        "Case Details + Payment Particulars",
        "Validation and Trace Setup",
        "Confirmed Blockchain Transfer Reading",
        "Hop-by-Hop Fund Flow Attribution",
        "Graph + Explanation + Evidence Hashes",
        "Finding Review / Notice Draft / Evidence Export",
    ]
    story.append(ArrowFlow(data_labels, 16.2 * cm, box_height=0.95 * cm))
    story.append(PageBreak())

    story.append(Paragraph("Feasibility Statement", S["Section"]))
    story.append(
        note_box(
            "Prototype Status",
            [
                "Core workflow is already implemented: login, case intake, TRON/USDT tracing, graph view, evidence hashing, audit trail, notice drafting, supervisor countersignature, and specimen SAHYOG export.",
                "Production integrations such as CCTNS/SAHYOG SSO and live legal dispatch require approved credentials, schemas, and authority configuration.",
            ],
            fill="#F0FFF4",
            stroke="#68A06D",
        )
    )
    story.append(Paragraph("Compact One-Slide Version", S["Section"]))
    compact = """Technical Approach & System Architecture

[Officer Login]
      |
      v
[Case Intake: Report ID / Wallet / Tx Hash]
      |
      v
[Validation: chain, asset, amount, timestamp]
      |
      v
[Trace Engine: confirmed blockchain fund flow]
      |
      v
[Evidence Store: snapshots, raw data, hashes, audit]
      |
      v
[Outputs: graph, explanation, finding review, notice, export]

Tech Stack:
Python, FastAPI, Jinja2, JavaScript, SQLite WAL,
SQLModel, Cytoscape.js, TronGrid API, SHA-256 evidence store

Feasibility:
Working prototype covers end-to-end investigation flow.
Government SSO and SAHYOG live submission remain integration-pending."""
    story.append(Preformatted(compact, S["MonoBlock"]))
    story.append(PageBreak())

    story.append(Paragraph("Speaker Notes", S["Section"]))
    story.append(
        Paragraph(
            "Trinetra starts from a cyber-crime report ID or a blockchain transaction. It verifies the reported payment, follows confirmed blockchain transfers, records every step as evidence, and shows the result as a graph. It does not automatically accuse anyone. If evidence is insufficient, it stops and says so. If a custody point is supported, the officer can review it and prepare a notice package.",
            S["Body"],
        )
    )
    story.append(Paragraph("Suggested PPT Layout", S["Section"]))
    story.append(
        table_from_pairs(
            [
                ("Option A", "Single technical flow slide: left side architecture diagram, right side tech stack box, bottom strip feasibility statement."),
                ("Option B", "Two slides: Slide 1 system architecture; Slide 2 technical process flow."),
            ]
        )
    )
    story.append(Paragraph("Final Design Guidance", S["Section"]))
    story.append(
        bullets(
            [
                "Use icons for officer, UI, server, database, blockchain, and document/export.",
                "Keep each box to 1-2 lines.",
                "Avoid paragraphs on the slide; put detail in speaker notes.",
                "Make Prototype and Production Pending visually distinct.",
                "Clearly separate user interface, processing logic, data storage, external provider/portal integration, and final outputs.",
            ]
        )
    )

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


if __name__ == "__main__":
    build()
