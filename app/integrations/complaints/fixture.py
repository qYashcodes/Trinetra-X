from __future__ import annotations

from copy import deepcopy
from io import BytesIO

from app.services.demo import demo_case


COMPLETE_SAMPLE_ACK = "NCRP/2026/MH/0091001"
INCOMPLETE_SAMPLE_ACK = "NCRP/2026/MH/0091002"


class FixtureComplaintSource:
    def health(self) -> dict:
        return {"name": "NCRP fixture feed", "status": "ok", "detail": "seeded demo data"}

    def fetch(self, ack_no: str) -> dict | None:
        data = demo_case()
        if ack_no == data["case"]["ack_no"]:
            return data["case"]
        if ack_no == COMPLETE_SAMPLE_ACK:
            complete = deepcopy(data["case"])
            complete["ack_no"] = ack_no
            return complete
        if ack_no == INCOMPLETE_SAMPLE_ACK:
            incomplete = deepcopy(data["case"])
            incomplete["ack_no"] = ack_no
            incomplete["payment_txid"] = None
            incomplete["complainant_contact_redacted"] = None
            return incomplete
        if ack_no == "NCRP/2026/MH/0084231":
            clone = dict(data["case"])
            clone["ack_no"] = ack_no
            clone["reported_address"] = "TStationaryFunds9Eo3xWw7q9LLQaZz"
            return clone
        return None

    def referrals_today(self) -> list[dict]:
        # Return fresh row dictionaries so presentation code cannot mutate the
        # deterministic fixture shared by the rest of the prototype.
        return [dict(item) for item in demo_case()["referrals_today"]]

    def sample_references(self) -> list[dict[str, str]]:
        return [
            {
                "ack_no": COMPLETE_SAMPLE_ACK,
                "label": "Complete record",
                "status": "complete",
            },
            {
                "ack_no": INCOMPLETE_SAMPLE_ACK,
                "label": "2 details missing",
                "status": "missing",
            },
        ]

    def fetch_document(self, ack_no: str, document_type: str) -> dict | None:
        """Return a visibly simulated complaint/FIR document for attachment tests.

        This deliberately extends the complaint-source boundary instead of
        introducing a second portal client. It never represents live retrieval.
        """
        if document_type not in {"complaint", "fir"} or self.fetch(ack_no) is None:
            return None
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen.canvas import Canvas

        buffer = BytesIO()
        canvas = Canvas(buffer, pagesize=A4)
        width, height = A4
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(54, height - 64, "SIMULATED PORTAL DOCUMENT")
        canvas.setFont("Helvetica", 11)
        canvas.drawString(54, height - 94, f"Reference: {ack_no}")
        canvas.drawString(54, height - 114, f"Type: {document_type.title()}")
        canvas.drawString(54, height - 144, "Fixture source only — not retrieved from NCRP or CCTNS.")
        canvas.setFillColorRGB(0.8, 0.1, 0.1, alpha=0.16)
        canvas.setFont("Helvetica-Bold", 42)
        canvas.saveState()
        canvas.translate(width / 2, height / 2)
        canvas.rotate(32)
        canvas.drawCentredString(0, 0, "SIMULATED")
        canvas.restoreState()
        canvas.save()
        return {
            "name": f"{document_type}-{ack_no.replace('/', '-')}-simulated.pdf",
            "mime_type": "application/pdf",
            "data": buffer.getvalue(),
            "provenance": "Fixture complaint-source adapter; simulated portal document",
            "simulated": True,
        }
