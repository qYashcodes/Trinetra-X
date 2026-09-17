from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Case, SourceCoverage
from app.services.accounting import (
    AccountingError,
    ValueOutcome,
    conservation_report,
    persist_conservation_report,
)
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


def make_case(session: Session) -> Case:
    ts = now_ms()
    case = Case(
        ack_no="NCRP/2026/TEST/ACCOUNTING",
        category="investment fraud",
        jurisdiction="Test Cyber Cell",
        filed_ts_ms=ts,
        amount_reported_base=100_000,
        asset_symbol="USDT",
        asset_decimals=6,
        chain_family="TRON",
        chain_network="mainnet",
        reported_address="TSeed",
        payment_txid="d" * 64,
        payment_ts_ms=ts,
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def test_snapshot_value_outcomes_assign_seed_once_per_asset() -> None:
    report = conservation_report(
        {"TRON:USDT": 100_000},
        [
            ValueOutcome("TRON:USDT", 3_000, "deferred", "branch-001"),
            ValueOutcome("TRON:USDT", 97_000, "unresolved", "branch-remainder"),
        ],
    )

    assert report["TRON:USDT"]["seed"] == 100_000
    assert report["TRON:USDT"]["deferred"] == 3_000
    assert report["TRON:USDT"]["unresolved"] == 97_000
    assert report["TRON:USDT"]["assigned"] == 100_000


def test_accounting_rejects_double_counted_parent_and_child() -> None:
    with pytest.raises(AccountingError, match="not conserved"):
        conservation_report(
            {"TRON:USDT": 100},
            [
                ValueOutcome("TRON:USDT", 100, "unresolved", "parent"),
                ValueOutcome("TRON:USDT", 40, "custody", "child"),
            ],
        )


def test_accounting_rejects_duplicate_evidence_reference() -> None:
    with pytest.raises(AccountingError, match="assigned twice"):
        conservation_report(
            {"TRON:USDT": 100},
            [
                ValueOutcome("TRON:USDT", 50, "deferred", "edge-1"),
                ValueOutcome("TRON:USDT", 50, "unresolved", "edge-1"),
            ],
        )


def test_accounting_keeps_assets_separate() -> None:
    report = conservation_report(
        {"TRON:USDT": 100, "BTC:sat": 10},
        [
            ValueOutcome("TRON:USDT", 100, "custody", "usdt-credit"),
            ValueOutcome("BTC:sat", 10, "unresolved", "btc-utxo"),
        ],
    )

    assert report["TRON:USDT"]["assigned"] == 100
    assert report["BTC:sat"]["assigned"] == 10

    with pytest.raises(AccountingError, match="no seed basis"):
        conservation_report(
            {"TRON:USDT": 100},
            [ValueOutcome("BTC:sat", 100, "custody", "mixed-asset")],
        )


def test_conservation_totals_persist_as_source_coverage(db: Session) -> None:
    case = make_case(db)
    report = conservation_report(
        {"TRON:USDT": 100_000},
        [
            ValueOutcome("TRON:USDT", 60_000, "deferred", "victim-one:0"),
            ValueOutcome("TRON:USDT", 40_000, "stationary", "victim-two:0"),
        ],
    )
    rows = persist_conservation_report(
        db,
        case_id=case.id,
        snapshot_id=None,
        chain_family="TRON",
        chain_network="mainnet",
        report=report,
    )

    assert len(rows) == 1
    assert isinstance(rows[0], SourceCoverage)
    assert rows[0].provider == "trace-accounting"
    assert rows[0].completeness == "balanced"
    assert rows[0].query_range["asset_identifier"] == "TRON:USDT"
    assert rows[0].observed_watermark["totals"]["assigned"] == 100_000
