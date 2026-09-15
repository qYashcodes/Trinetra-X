from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine_bridge import TraceParams
from app.models import AttributedLot, CanonicalTraceEvent, Case, FrontierItem, SourceCoverage
from app.services.allocation import (
    AllocationError,
    FrontierCandidate,
    allocate_proportional,
    schedule_candidates,
)
from app.services.frontier import (
    ObservedOutgoing,
    complete_frontier_item,
    lease_frontier_batch,
    next_frontier_batch,
    persist_frontier_observations,
    release_frontier_item,
)
from app.services.live_tron_resume import expand_live_tron_frontier_item
from app.services.live_tron_resume import release_due_frontier_retries
from app.services.live_tron_resume import run_live_tron_frontier_cycle
from app.services.time import now_ms


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_case(session: Session, ack_no: str = "NCRP/2026/TEST/FRONTIER") -> Case:
    ts = now_ms()
    case = Case(
        ack_no=ack_no,
        category="investment fraud",
        jurisdiction="Test Cyber Cell",
        filed_ts_ms=ts,
        amount_reported_base=100_000,
        asset_symbol="USDT",
        asset_decimals=6,
        chain_family="TRON",
        chain_network="mainnet",
        reported_address="TSeed",
        payment_txid="c" * 64,
        payment_ts_ms=ts,
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def test_proportional_allocation_uses_integer_base_units() -> None:
    step = allocate_proportional(
        balance_base=1_000,
        attributed_base=100,
        outgoing_base=400,
    )

    assert step.outgoing_attributed_base == 40
    assert step.remaining_balance_base == 600
    assert step.remaining_attributed_base == 60
    assert step.residual_numerator == 0


def test_proportional_allocation_carries_rounding_residual() -> None:
    first = allocate_proportional(balance_base=3, attributed_base=1, outgoing_base=1)
    second = allocate_proportional(
        balance_base=first.remaining_balance_base,
        attributed_base=first.remaining_attributed_base,
        outgoing_base=1,
        residual_numerator=first.residual_numerator,
    )

    assert first.outgoing_attributed_base == 0
    assert first.residual_numerator == 1
    assert second.outgoing_attributed_base == 1
    assert second.remaining_attributed_base == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"balance_base": 0, "attributed_base": 0, "outgoing_base": 0},
        {"balance_base": 100, "attributed_base": 101, "outgoing_base": 1},
        {"balance_base": 100, "attributed_base": 10, "outgoing_base": 101},
        {"balance_base": 100, "attributed_base": -1, "outgoing_base": 1},
    ],
)
def test_allocation_rejects_inconsistent_evidence(kwargs: dict) -> None:
    with pytest.raises(AllocationError):
        allocate_proportional(**kwargs)


def test_100_way_split_remains_represented_but_deferred_under_floor() -> None:
    children = [
        FrontierCandidate(f"child-{index:03}", attributed_base=1_000, event_order=index)
        for index in range(100)
    ]
    scheduled, deferred = schedule_candidates(
        children,
        strategy="value_weighted",
        breadth_cap=3,
    )

    assert len(scheduled) == 3
    assert len(deferred) == 97
    assert {item.stable_id for item in scheduled + deferred} == {
        item.stable_id for item in children
    }
    assert sum(item.attributed_base for item in scheduled + deferred) == 100_000


def test_dominant_and_value_weighted_scheduling_are_distinct() -> None:
    children = [
        FrontierCandidate("A", attributed_base=60, event_order=0),
        FrontierCandidate("B", attributed_base=40, event_order=1),
        FrontierCandidate("D", attributed_base=50, event_order=2),
        FrontierCandidate("C", attributed_base=10, event_order=3),
    ]

    dominant, parked = schedule_candidates(
        children,
        strategy="dominant_fund_flow",
        breadth_cap=3,
    )
    value_weighted, deferred = schedule_candidates(
        children,
        strategy="value_weighted",
        breadth_cap=3,
    )

    assert [item.stable_id for item in dominant] == ["A"]
    assert [item.stable_id for item in parked] == ["D", "B", "C"]
    assert [item.stable_id for item in value_weighted] == ["A", "D", "B"]
    assert [item.stable_id for item in deferred] == ["C"]


def test_frontier_persists_every_observed_edge_with_deferral_reason(db: Session) -> None:
    case = make_case(db)
    result = persist_frontier_observations(
        db,
        case_id=case.id,
        snapshot_id=None,
        lot_id=None,
        observed_edges=[
            ObservedOutgoing("edge-queued", "TQueued", 100, 0, 1, observed_ts_ms=10),
            ObservedOutgoing("edge-budget", "TBudget", 90, 1, 1, observed_ts_ms=10),
            ObservedOutgoing("edge-floor", "TFloor", 9, 2, 1, observed_ts_ms=10),
            ObservedOutgoing("edge-depth", "TDepth", 80, 3, 6, observed_ts_ms=10),
            ObservedOutgoing("edge-time", "TTime", 70, 4, 1, observed_ts_ms=200),
            ObservedOutgoing("edge-cycle", "TCycle", 60, 5, 1, observed_ts_ms=10),
        ],
        strategy="value_weighted",
        breadth_cap=3,
        address_budget_remaining=1,
        value_floor_base=10,
        max_depth=5,
        visited_addresses={"TCycle"},
        time_window_end_ms=100,
    )

    rows = db.exec(select(FrontierItem)).all()
    reasons = {row.event_ref: row.deferral_reason for row in rows}
    states = {row.event_ref: row.state for row in rows}

    assert len(rows) == 6
    assert [row.event_ref for row in result.queued] == ["edge-queued"]
    assert states["edge-queued"] == "queued"
    assert reasons["edge-budget"] == "address_budget"
    assert reasons["edge-floor"] == "value_floor"
    assert reasons["edge-depth"] == "depth_budget"
    assert reasons["edge-time"] == "time_window"
    assert reasons["edge-cycle"] == "cycle_detected"


def test_frontier_resume_order_matches_value_weighted_scheduler_after_restart(db: Session) -> None:
    case = make_case(db, "NCRP/2026/TEST/RESUME")
    persist_frontier_observations(
        db,
        case_id=case.id,
        snapshot_id=None,
        lot_id=None,
        observed_edges=[
            ObservedOutgoing("edge-a", "TA", 60, 0, 1, resume_cursor={"page": "a"}),
            ObservedOutgoing("edge-b", "TB", 40, 1, 1, resume_cursor={"page": "b"}),
            ObservedOutgoing("edge-c", "TC", 50, 2, 1, resume_cursor={"page": "c"}),
        ],
        strategy="value_weighted",
        breadth_cap=3,
        address_budget_remaining=3,
        value_floor_base=0,
        max_depth=5,
    )

    db.expire_all()
    resumed = next_frontier_batch(db, case_id=case.id, limit=3)

    assert [row.event_ref for row in resumed] == ["edge-a", "edge-c", "edge-b"]
    assert [row.resume_cursor["page"] for row in resumed] == ["a", "c", "b"]


def test_frontier_keeps_repeated_arrivals_distinct_and_honors_exact_budgets(db: Session) -> None:
    case = make_case(db, "NCRP/2026/TEST/REPEATED")
    result = persist_frontier_observations(
        db,
        case_id=case.id,
        snapshot_id=None,
        lot_id=None,
        observed_edges=[
            ObservedOutgoing("victim-one:0", "TShared", 50, 0, 1),
            ObservedOutgoing("victim-two:0", "TShared", 50, 1, 1),
        ],
        strategy="value_weighted",
        breadth_cap=2,
        address_budget_remaining=2,
        value_floor_base=50,
        max_depth=1,
    )

    assert [row.event_ref for row in result.queued] == ["victim-one:0", "victim-two:0"]
    assert result.deferred == []


def test_two_victim_deposits_to_same_destination_remain_separate_lots(db: Session) -> None:
    case = make_case(db, "NCRP/2026/TEST/TWO-VICTIMS")
    result = persist_frontier_observations(
        db,
        case_id=case.id,
        snapshot_id=None,
        lot_id=None,
        observed_edges=[
            ObservedOutgoing(
                "victim-a:tx:0",
                "TSharedDeposit",
                700,
                0,
                1,
                resume_cursor={"seed_event_ref": "seed:victim-a", "txid": "a" * 64},
            ),
            ObservedOutgoing(
                "victim-b:tx:0",
                "TSharedDeposit",
                300,
                1,
                1,
                resume_cursor={"seed_event_ref": "seed:victim-b", "txid": "b" * 64},
            ),
        ],
        strategy="value_weighted",
        breadth_cap=2,
        address_budget_remaining=2,
        value_floor_base=0,
        max_depth=3,
    )

    rows = db.exec(select(FrontierItem).order_by(FrontierItem.priority_base.desc())).all()

    assert [row.event_ref for row in result.queued] == ["victim-a:tx:0", "victim-b:tx:0"]
    assert [row.priority_base for row in rows] == [700, 300]
    assert {row.resume_cursor["seed_event_ref"] for row in rows} == {
        "seed:victim-a",
        "seed:victim-b",
    }
    assert {row.resume_cursor["destination_address"] for row in rows} == {"TSharedDeposit"}


def test_frontier_resume_after_release_matches_fresh_value_weighted_order(db: Session) -> None:
    case = make_case(db, "NCRP/2026/TEST/RESUME-EQUIV")
    edges = [
        ObservedOutgoing("edge-low", "TLow", 10, 0, 1, resume_cursor={"page": "low"}),
        ObservedOutgoing("edge-high", "THigh", 90, 1, 1, resume_cursor={"page": "high"}),
        ObservedOutgoing("edge-mid", "TMid", 50, 2, 1, resume_cursor={"page": "mid"}),
    ]
    persist_frontier_observations(
        db,
        case_id=case.id,
        snapshot_id=None,
        lot_id=None,
        observed_edges=edges,
        strategy="value_weighted",
        breadth_cap=3,
        address_budget_remaining=3,
        value_floor_base=0,
        max_depth=5,
    )
    expected_order = [
        item.stable_id
        for item in schedule_candidates(
            [
                FrontierCandidate(edge.event_ref, edge.attributed_base, edge.event_order)
                for edge in edges
            ],
            strategy="value_weighted",
            breadth_cap=3,
        )[0]
    ]

    leased = lease_frontier_batch(db, case_id=case.id, worker_id="resume-worker", limit=1)[0]
    assert leased.event_ref == expected_order[0]
    release_frontier_item(db, leased)
    db.expire_all()
    resumed = next_frontier_batch(db, case_id=case.id, limit=3)

    assert [row.event_ref for row in resumed] == expected_order
    assert [row.resume_cursor["page"] for row in resumed] == ["high", "mid", "low"]


def test_frontier_lease_complete_and_release_preserve_resume_cursor(db: Session) -> None:
    case = make_case(db, "NCRP/2026/TEST/LEASE")
    persist_frontier_observations(
        db,
        case_id=case.id,
        snapshot_id=None,
        lot_id=None,
        observed_edges=[
            ObservedOutgoing("edge-a", "TA", 60, 0, 1, resume_cursor={"page": "a"}),
            ObservedOutgoing("edge-b", "TB", 40, 1, 1, resume_cursor={"page": "b"}),
        ],
        strategy="value_weighted",
        breadth_cap=2,
        address_budget_remaining=2,
        value_floor_base=0,
        max_depth=5,
    )

    leased = lease_frontier_batch(db, case_id=case.id, worker_id="worker-1", limit=2)

    assert [row.event_ref for row in leased] == ["edge-a", "edge-b"]
    assert [row.lease_version for row in leased] == [1, 1]
    assert {row.resume_cursor["leased_by"] for row in leased} == {"worker-1"}
    assert next_frontier_batch(db, case_id=case.id, limit=2) == []

    completed = complete_frontier_item(db, leased[0], outcome_ref="snapshot:1:hop:next")
    released = release_frontier_item(db, leased[1], deferral_reason="time_window")

    assert completed.state == "completed"
    assert completed.resume_cursor["page"] == "a"
    assert completed.resume_cursor["outcome_ref"] == "snapshot:1:hop:next"
    assert released.state == "deferred"
    assert released.deferral_reason == "time_window"


def test_live_tron_frontier_expansion_persists_resume_children_and_stationary_value(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.adapters import tron

    case = make_case(db, "NCRP/2026/TEST/LIVE-RESUME")
    ts = now_ms()
    parent_lot = AttributedLot(
        case_id=case.id,
        snapshot_id=None,
        seed_event_ref="seed:parent",
        asset_identifier=f"TRON:mainnet:USDT:{tron.TRON_MAINNET_USDT}",
        current_address="TParent",
        remaining_amount_base=1_000,
        arrival_cursor={"txid": "1" * 64, "event_index": 0},
        allocation_policy="value_weighted",
        allocation_version="trinetra.allocation/1",
        ancestry=[{"evidence_ref": "seed:parent", "kind": "seed"}],
        rounding_state={"residual_numerator": 0},
        state="queued",
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    db.add(parent_lot)
    db.commit()
    db.refresh(parent_lot)
    parent_frontier = FrontierItem(
        case_id=case.id,
        snapshot_id=None,
        lot_id=parent_lot.id,
        event_ref="frontier:parent",
        priority_base=1_000,
        priority_reason="value_weighted",
        depth=1,
        state="queued",
        deferral_reason=None,
        resume_cursor={"address": "TParent", "ts_ms": 0, "observed_ts_ms": ts + 999},
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    db.add(parent_frontier)
    db.commit()

    def fake_fetch(address: str, **kwargs) -> list[dict]:
        assert address == "TParent"
        assert kwargs["contract_address"] == tron.TRON_MAINNET_USDT
        assert kwargs["min_timestamp"] == 0
        return [
            {
                "transaction_id": "a" * 64,
                "block_timestamp": ts + 1,
                "from": "TOther",
                "to": "TParent",
                "value": "999",
                "event_index": 1,
                "token_info": {"symbol": "USDT", "decimals": 6, "address": tron.TRON_MAINNET_USDT},
            },
            {
                "transaction_id": "b" * 64,
                "block_timestamp": ts + 2,
                "from": "TParent",
                "to": "TQueued",
                "value": "400",
                "event_index": 7,
                "token_info": {"symbol": "USDT", "decimals": 6, "address": tron.TRON_MAINNET_USDT},
            },
            {
                "transaction_id": "c" * 64,
                "block_timestamp": ts + 3,
                "from": "TParent",
                "to": "TDeferred",
                "value": "100",
                "event_index": 8,
                "token_info": {"symbol": "USDT", "decimals": 6, "address": tron.TRON_MAINNET_USDT},
            },
        ]

    monkeypatch.setattr(tron, "fetch_trc20_transfers", fake_fetch)
    leased = lease_frontier_batch(db, case_id=case.id, worker_id="worker-live", limit=1)[0]

    result = expand_live_tron_frontier_item(
        db,
        leased,
        params=TraceParams(
            strategy="value_weighted",
            breadth_cap=1,
            value_floor_share=Decimal("0.20"),
        ),
    )

    canonical = db.exec(
        select(CanonicalTraceEvent).order_by(CanonicalTraceEvent.event_index)
    ).all()
    assert [row.destination_address for row in canonical] == ["TQueued", "TDeferred"]
    assert [row.amount_base for row in canonical] == [400, 100]
    assert [row.event_index for row in canonical] == [7, 8]
    assert all(row.finality == "provider_confirmed" for row in canonical)

    db.refresh(parent_lot)
    db.refresh(leased)
    assert parent_lot.remaining_amount_base == 500
    assert parent_lot.state == "stationary_observed"
    assert leased.state == "completed"
    assert leased.resume_cursor["observed_outgoing"] == 2
    assert result.stationary_amount_base == 500

    child_lots = [
        row for row in db.exec(select(AttributedLot)).all() if row.id != parent_lot.id
    ]
    assert {row.current_address: row.state for row in child_lots} == {
        "TQueued": "queued",
        "TDeferred": "deferred",
    }
    children = [
        row for row in db.exec(select(FrontierItem)).all() if row.event_ref != "frontier:parent"
    ]
    assert [(row.state, row.deferral_reason, row.resume_cursor["event_index"]) for row in children] == [
        ("queued", None, 7),
        ("deferred", "value_floor", 8),
    ]
    coverage = db.exec(select(SourceCoverage).where(SourceCoverage.provider == "live-tron-frontier")).one()
    assert coverage.completeness == "frontier_expanded"
    assert coverage.observed_watermark["queued"] == 1
    assert coverage.observed_watermark["deferred"] == 1
    assert coverage.observed_watermark["stationary_amount_base"] == 500


def test_live_tron_frontier_expansion_defers_ancestor_cycles(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.adapters import tron

    case = make_case(db, "NCRP/2026/TEST/LIVE-CYCLE")
    ts = now_ms()
    parent_lot = AttributedLot(
        case_id=case.id,
        snapshot_id=None,
        seed_event_ref="seed:cycle",
        asset_identifier=f"TRON:mainnet:USDT:{tron.TRON_MAINNET_USDT}",
        current_address="TParent",
        remaining_amount_base=1_000,
        arrival_cursor={"txid": "e" * 64, "event_index": 0},
        allocation_policy="value_weighted",
        allocation_version="trinetra.allocation/1",
        ancestry=[
            {
                "evidence_ref": "seed:cycle",
                "kind": "seed",
                "destination_address": "TSeed",
            }
        ],
        rounding_state={"residual_numerator": 0},
        state="queued",
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    db.add(parent_lot)
    db.commit()
    db.refresh(parent_lot)
    parent_frontier = FrontierItem(
        case_id=case.id,
        snapshot_id=None,
        lot_id=parent_lot.id,
        event_ref="frontier:cycle",
        priority_base=1_000,
        priority_reason="value_weighted",
        depth=1,
        state="queued",
        deferral_reason=None,
        resume_cursor={"address": "TParent", "ts_ms": ts},
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    db.add(parent_frontier)
    db.commit()

    def fake_fetch(address: str, **_kwargs) -> list[dict]:
        assert address == "TParent"
        return [
            {
                "transaction_id": "e" * 64,
                "block_timestamp": ts + 1,
                "from": "TParent",
                "to": "TSeed",
                "value": "300",
                "event_index": 1,
                "token_info": {"symbol": "USDT", "decimals": 6, "address": tron.TRON_MAINNET_USDT},
            },
            {
                "transaction_id": "f" * 64,
                "block_timestamp": ts + 2,
                "from": "TParent",
                "to": "TNext",
                "value": "200",
                "event_index": 2,
                "token_info": {"symbol": "USDT", "decimals": 6, "address": tron.TRON_MAINNET_USDT},
            },
        ]

    monkeypatch.setattr(tron, "fetch_trc20_transfers", fake_fetch)
    leased = lease_frontier_batch(db, case_id=case.id, worker_id="cycle-worker", limit=1)[0]

    result = expand_live_tron_frontier_item(
        db,
        leased,
        params=TraceParams(
            strategy="value_weighted",
            breadth_cap=3,
            value_floor_share=Decimal("0"),
        ),
    )

    children = sorted(result.child_frontier, key=lambda row: row.resume_cursor["event_index"])
    assert [(row.resume_cursor["address"], row.state, row.deferral_reason) for row in children] == [
        ("TSeed", "deferred", "cycle_detected"),
        ("TNext", "queued", None),
    ]
    child_lots = {
        row.current_address: row
        for row in db.exec(select(AttributedLot)).all()
        if row.id != parent_lot.id
    }
    assert child_lots["TNext"].ancestry[-1]["source_address"] == "TParent"
    assert child_lots["TNext"].ancestry[-1]["destination_address"] == "TNext"
    coverage = db.exec(
        select(SourceCoverage).where(SourceCoverage.completeness == "frontier_expanded")
    ).one()
    assert coverage.observed_watermark["queued"] == 1
    assert coverage.observed_watermark["deferred"] == 1


def test_live_tron_worker_cycle_defers_provider_errors_and_releases_due_retry(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.adapters import tron

    case = make_case(db, "NCRP/2026/TEST/LIVE-WORKER")
    ts = now_ms()
    parent_lot = AttributedLot(
        case_id=case.id,
        snapshot_id=None,
        seed_event_ref="seed:worker",
        asset_identifier=f"TRON:mainnet:USDT:{tron.TRON_MAINNET_USDT}",
        current_address="TWorkerParent",
        remaining_amount_base=1_000,
        arrival_cursor={"txid": "d" * 64, "event_index": 0},
        allocation_policy="value_weighted",
        allocation_version="trinetra.allocation/1",
        ancestry=[{"evidence_ref": "seed:worker", "kind": "seed"}],
        rounding_state={"residual_numerator": 0},
        state="queued",
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    db.add(parent_lot)
    db.commit()
    db.refresh(parent_lot)
    frontier = FrontierItem(
        case_id=case.id,
        snapshot_id=None,
        lot_id=parent_lot.id,
        event_ref="frontier:worker",
        priority_base=1_000,
        priority_reason="value_weighted",
        depth=1,
        state="queued",
        deferral_reason=None,
        resume_cursor={"address": "TWorkerParent", "ts_ms": ts},
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    db.add(frontier)
    db.commit()
    db.refresh(frontier)

    def fail_fetch(*_args, **_kwargs) -> list[dict]:
        raise tron.ProviderResponseError("rate limit reached")

    monkeypatch.setattr(tron, "fetch_trc20_transfers", fail_fetch)
    failed = run_live_tron_frontier_cycle(
        db,
        case_id=case.id,
        worker_id="worker-cycle",
        limit=1,
        backoff_ms=10,
        max_attempts=2,
    )

    assert len(failed.expanded) == 0
    assert len(failed.deferred) == 1
    assert failed.failures[0]["error_kind"] == "ProviderResponseError"
    db.refresh(frontier)
    cursor = dict(frontier.resume_cursor)
    assert frontier.state == "deferred"
    assert frontier.deferral_reason == "provider_backoff"
    assert cursor["retry_attempts"] == 1
    backoff_coverage = db.exec(
        select(SourceCoverage).where(SourceCoverage.completeness == "provider_backoff")
    ).one()
    assert backoff_coverage.query_range["kind"] == "provider_backoff"
    assert backoff_coverage.query_range["event_ref"] == "frontier:worker"
    assert backoff_coverage.query_range["address"] == "TWorkerParent"
    assert backoff_coverage.observed_watermark["error_kind"] == "ProviderResponseError"
    assert backoff_coverage.observed_watermark["retry_attempts"] == 1
    assert backoff_coverage.observed_watermark["next_retry_ts_ms"] == cursor["next_retry_ts_ms"]
    assert backoff_coverage.observed_watermark["retry_exhausted"] is False
    assert backoff_coverage.gaps == [
        {"reason": "provider_backoff", "event_ref": "frontier:worker"}
    ]
    assert release_due_frontier_retries(
        db,
        case_id=case.id,
        now_ts_ms=cursor["next_retry_ts_ms"] - 1,
    ) == []

    released = release_due_frontier_retries(
        db,
        case_id=case.id,
        now_ts_ms=cursor["next_retry_ts_ms"],
    )
    assert [row.event_ref for row in released] == ["frontier:worker"]
    db.refresh(frontier)
    assert frontier.state == "queued"
    assert frontier.deferral_reason is None

    monkeypatch.setattr(tron, "fetch_trc20_transfers", lambda *_args, **_kwargs: [])
    succeeded = run_live_tron_frontier_cycle(
        db,
        case_id=case.id,
        worker_id="worker-cycle",
        limit=1,
    )

    assert len(succeeded.expanded) == 1
    assert succeeded.failures == []
    db.refresh(frontier)
    db.refresh(parent_lot)
    assert frontier.state == "completed"
    assert parent_lot.state == "stationary_observed"
    assert parent_lot.remaining_amount_base == 1_000
    coverage = db.exec(
        select(SourceCoverage).where(SourceCoverage.completeness == "frontier_expanded")
    ).one()
    assert coverage.observed_watermark["observed_outgoing"] == 0
    assert coverage.observed_watermark["stationary_amount_base"] == 1_000
