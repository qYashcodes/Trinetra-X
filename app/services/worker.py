from __future__ import annotations

import os
import threading
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import or_
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.engine_bridge import TraceParams
from app.models import FrontierItem
from app.services.feature_flags import feature_flags
from app.services.frontier import recover_stale_frontier_leases
from app.services.live_tron_resume import run_live_tron_frontier_cycle
from app.services.time import now_ms
from app.settings import settings


@dataclass
class WorkerTelemetry:
    state: str = "not_started"
    enabled: bool = False
    worker_id: str | None = None
    started_ts_ms: int | None = None
    stopped_ts_ms: int | None = None
    last_cycle_ts_ms: int | None = None
    cycles: int = 0
    expanded_items: int = 0
    deferred_items: int = 0
    released_retries: int = 0
    recovered_leases: int = 0
    failures: int = 0
    queued: int = 0
    leased: int = 0
    deferred: int = 0
    last_error_kind: str | None = None
    blocked_reason: str | None = None


_STATUS_LOCK = threading.Lock()
_STATUS = WorkerTelemetry()


def frontier_worker_status() -> dict:
    with _STATUS_LOCK:
        return asdict(_STATUS)


def _replace_status(status: WorkerTelemetry) -> None:
    global _STATUS
    with _STATUS_LOCK:
        _STATUS = status


class SupervisedFrontierWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        enabled: bool,
        blocked_reason: str | None = None,
        poll_interval_s: float = 3.0,
        batch_size: int = 3,
        lease_timeout_ms: int = 120_000,
        max_cases_per_cycle: int = 20,
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("Worker poll interval must be positive.")
        if batch_size <= 0 or lease_timeout_ms < 0 or max_cases_per_cycle <= 0:
            raise ValueError("Worker batch, lease and case limits are invalid.")
        self.engine = engine
        self.enabled = enabled
        self.blocked_reason = blocked_reason
        self.poll_interval_s = poll_interval_s
        self.batch_size = batch_size
        self.lease_timeout_ms = lease_timeout_ms
        self.max_cases_per_cycle = max_cases_per_cycle
        self.worker_id = f"frontier-{uuid.uuid4().hex[:12]}"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._telemetry = WorkerTelemetry(
            state="not_started",
            enabled=enabled,
            worker_id=self.worker_id,
            blocked_reason=blocked_reason,
        )
        _replace_status(self._telemetry)

    @classmethod
    def from_environment(cls, engine: Engine) -> "SupervisedFrontierWorker":
        flag = feature_flags()["supervised_frontier_worker"]
        enabled = settings.mode == "live" and bool(flag["enabled"])
        blocked_reason = None if enabled else str(flag.get("blocked_reason") or "Worker is gated.")
        return cls(
            engine,
            enabled=enabled,
            blocked_reason=blocked_reason,
            poll_interval_s=float(os.getenv("TRINETRA_WORKER_POLL_INTERVAL_S", "3")),
            batch_size=int(os.getenv("TRINETRA_WORKER_BATCH_SIZE", "3")),
            lease_timeout_ms=int(os.getenv("TRINETRA_WORKER_LEASE_TIMEOUT_MS", "120000")),
            max_cases_per_cycle=int(os.getenv("TRINETRA_WORKER_MAX_CASES_PER_CYCLE", "20")),
        )

    def start(self) -> None:
        if not self.enabled:
            self._telemetry.state = "gated"
            _replace_status(self._telemetry)
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._telemetry.state = "running"
        self._telemetry.started_ts_ms = now_ms()
        self._telemetry.stopped_ts_ms = None
        _replace_status(self._telemetry)
        self._thread = threading.Thread(
            target=self._supervise,
            name=self.worker_id,
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout_s))
        if self.enabled:
            self._telemetry.state = (
                "stopped" if self._thread is None or not self._thread.is_alive() else "stop_timeout"
            )
            self._telemetry.stopped_ts_ms = now_ms()
            _replace_status(self._telemetry)

    def run_once(self, *, params: TraceParams | None = None) -> dict:
        recovered_count = 0
        with Session(self.engine) as session:
            recovered_count = len(
                recover_stale_frontier_leases(
                    session,
                    lease_timeout_ms=self.lease_timeout_ms,
                )
            )
            case_ids = list(
                session.exec(
                    select(FrontierItem.case_id)
                    .where(
                        or_(
                            FrontierItem.state == "queued",
                            (
                                (FrontierItem.state == "deferred")
                                & (FrontierItem.deferral_reason == "provider_backoff")
                            ),
                        )
                    )
                    .distinct()
                    .order_by(FrontierItem.case_id.asc())
                    .limit(self.max_cases_per_cycle)
                ).all()
            )

        expanded = 0
        deferred = 0
        released = 0
        failures = 0
        last_error_kind: str | None = None
        for case_id in case_ids:
            try:
                with Session(self.engine) as session:
                    result = run_live_tron_frontier_cycle(
                        session,
                        case_id=int(case_id),
                        worker_id=self.worker_id,
                        limit=self.batch_size,
                        params=params,
                    )
                expanded += len(result.expanded)
                deferred += len(result.deferred)
                released += len(result.released_retries)
                failures += len(result.failures)
                if result.failures:
                    last_error_kind = str(result.failures[-1].get("error_kind") or "worker_item_error")
            except Exception as exc:
                failures += 1
                last_error_kind = exc.__class__.__name__

        with Session(self.engine) as session:
            state_counts = {
                state: len(
                    session.exec(
                        select(FrontierItem.id).where(FrontierItem.state == state)
                    ).all()
                )
                for state in ("queued", "leased", "deferred")
            }

        self._telemetry.last_cycle_ts_ms = now_ms()
        self._telemetry.cycles += 1
        self._telemetry.expanded_items += expanded
        self._telemetry.deferred_items += deferred
        self._telemetry.released_retries += released
        self._telemetry.recovered_leases += recovered_count
        self._telemetry.failures += failures
        self._telemetry.queued = state_counts["queued"]
        self._telemetry.leased = state_counts["leased"]
        self._telemetry.deferred = state_counts["deferred"]
        self._telemetry.last_error_kind = last_error_kind
        _replace_status(self._telemetry)
        return frontier_worker_status()

    def _supervise(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self._telemetry.failures += 1
                self._telemetry.last_error_kind = exc.__class__.__name__
                _replace_status(self._telemetry)
            self._stop_event.wait(self.poll_interval_s)
