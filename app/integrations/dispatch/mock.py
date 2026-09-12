from __future__ import annotations


class MockDispatchChannel:
    def send(self, target: str, payload: dict) -> dict:
        return {
            "status": "sent",
            "target": target,
            "reference": f"MOCK-{payload.get('notice_no', 'NOTICE')}",
        }
