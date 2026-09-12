from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def iso_to_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1000)


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def format_ist(ts_ms: int) -> str:
    value = datetime.fromtimestamp(ts_ms / 1000, UTC).astimezone(IST)
    return value.strftime("%Y-%m-%d %H:%M:%S IST")
