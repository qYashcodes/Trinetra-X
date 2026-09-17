from __future__ import annotations

import os
import threading
from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Iterator

from app.services.time import now_ms
from app.settings import settings


@dataclass(frozen=True)
class ProviderBudgetDecision:
    allowed: bool
    category: str
    provider: str
    reason: str | None
    retry_after_ms: int | None
    used: int
    limit: int
    interactive_reserve: int


_CATEGORY: ContextVar[str] = ContextVar(
    "trinetra_provider_request_category",
    default="interactive_trace",
)
_LOCK = threading.Lock()
_REQUESTS: deque[tuple[int, str, str]] = deque()
_DENIALS: Counter[str] = Counter()
_LAST_DENIAL_TS_MS: int | None = None


@contextmanager
def provider_request_context(category: str) -> Iterator[None]:
    normalized = category.strip()
    if not normalized:
        raise ValueError("Provider request category is required.")
    token = _CATEGORY.set(normalized)
    try:
        yield
    finally:
        _CATEGORY.reset(token)


def reserve_provider_request(
    provider: str,
    *,
    now_ts_ms: int | None = None,
) -> ProviderBudgetDecision:
    global _LAST_DENIAL_TS_MS
    provider_key = provider.strip()
    if not provider_key:
        raise ValueError("Provider is required for budget reservation.")
    current = now_ms() if now_ts_ms is None else int(now_ts_ms)
    limit, reserve, window_ms = _limits()
    category = _CATEGORY.get()
    with _LOCK:
        _prune(current, window_ms)
        used = len(_REQUESTS)
        watch_ceiling = max(0, limit - reserve)
        reason = None
        if used >= limit:
            reason = "provider_budget_exhausted"
        elif category == "watch_poll" and used >= watch_ceiling:
            reason = "interactive_trace_reserve"
        if reason:
            _DENIALS[category] += 1
            _LAST_DENIAL_TS_MS = current
            retry_after_ms = _retry_after(current, window_ms)
            return ProviderBudgetDecision(
                allowed=False,
                category=category,
                provider=provider_key,
                reason=reason,
                retry_after_ms=retry_after_ms,
                used=used,
                limit=limit,
                interactive_reserve=reserve,
            )
        _REQUESTS.append((current, provider_key, category))
        return ProviderBudgetDecision(
            allowed=True,
            category=category,
            provider=provider_key,
            reason=None,
            retry_after_ms=None,
            used=used + 1,
            limit=limit,
            interactive_reserve=reserve,
        )


def provider_budget_status(*, now_ts_ms: int | None = None) -> dict:
    current = now_ms() if now_ts_ms is None else int(now_ts_ms)
    limit, reserve, window_ms = _limits()
    with _LOCK:
        _prune(current, window_ms)
        categories = Counter(category for _ts, _provider, category in _REQUESTS)
        providers = Counter(provider for _ts, provider, _category in _REQUESTS)
        used = len(_REQUESTS)
        return {
            "schema": "trinetra.provider_budget/1",
            "window_ms": window_ms,
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "interactive_reserve": reserve,
            "watch_capacity_remaining": max(0, limit - reserve - used),
            "by_category": dict(sorted(categories.items())),
            "by_provider": dict(sorted(providers.items())),
            "denied_by_category": dict(sorted(_DENIALS.items())),
            "last_denial_ts_ms": _LAST_DENIAL_TS_MS,
            "secrets_rendered": False,
        }


def reset_provider_budget() -> None:
    global _LAST_DENIAL_TS_MS
    with _LOCK:
        _REQUESTS.clear()
        _DENIALS.clear()
        _LAST_DENIAL_TS_MS = None


def decision_json(decision: ProviderBudgetDecision) -> dict:
    return asdict(decision)


def _limits() -> tuple[int, int, int]:
    limit = int(
        os.getenv(
            "TRINETRA_PROVIDER_REQUEST_BUDGET_PER_MINUTE",
            str(settings.provider_request_budget_per_minute),
        )
    )
    reserve = int(
        os.getenv(
            "TRINETRA_PROVIDER_INTERACTIVE_RESERVE_PER_MINUTE",
            str(settings.provider_interactive_reserve_per_minute),
        )
    )
    window_ms = int(os.getenv("TRINETRA_PROVIDER_BUDGET_WINDOW_MS", "60000"))
    if limit <= 0 or reserve < 0 or reserve > limit or window_ms <= 0:
        raise ValueError("Provider request budget settings are invalid.")
    return limit, reserve, window_ms


def _prune(current: int, window_ms: int) -> None:
    cutoff = current - window_ms
    while _REQUESTS and _REQUESTS[0][0] <= cutoff:
        _REQUESTS.popleft()


def _retry_after(current: int, window_ms: int) -> int:
    if not _REQUESTS:
        return window_ms
    return max(1, _REQUESTS[0][0] + window_ms - current)
