from __future__ import annotations

from copy import deepcopy

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
