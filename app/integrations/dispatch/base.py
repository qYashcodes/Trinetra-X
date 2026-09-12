from __future__ import annotations

from typing import Protocol


class DispatchChannel(Protocol):
    def send(self, target: str, payload: dict) -> dict: ...
