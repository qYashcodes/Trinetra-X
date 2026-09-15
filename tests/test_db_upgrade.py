from __future__ import annotations

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from app.db import upgrade_sqlite_schema
from app.models import TraceSnapshot
from app.settings import ROOT_DIR


def test_sqlite_upgrade_adds_trace_snapshot_columns_to_existing_database() -> None:
    db_path = ROOT_DIR / "var" / "test-db-upgrade.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE tracesnapshot (
                id INTEGER NOT NULL PRIMARY KEY,
                case_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                result_json JSON NOT NULL,
                sha256 VARCHAR NOT NULL,
                chain_family VARCHAR NOT NULL,
                chain_network VARCHAR NOT NULL,
                asset_symbol VARCHAR NOT NULL,
                asset_decimals INTEGER NOT NULL,
                bridge_candidate JSON,
                case_link_evidence JSON NOT NULL,
                started_ts_ms INTEGER NOT NULL,
                closed_ts_ms INTEGER
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO tracesnapshot (
                id,
                case_id,
                version,
                result_json,
                sha256,
                chain_family,
                chain_network,
                asset_symbol,
                asset_decimals,
                bridge_candidate,
                case_link_evidence,
                started_ts_ms,
                closed_ts_ms
            )
            VALUES (
                1,
                1,
                1,
                '{"cache_identity":"legacy-cache"}',
                'abc',
                'TRON',
                'mainnet',
                'USDT',
                6,
                NULL,
                '[]',
                10,
                20
            )
            """
        )

    SQLModel.metadata.create_all(engine)
    with engine.connect() as connection:
        upgrade_sqlite_schema(connection)
        upgrade_sqlite_schema(connection)
        connection.commit()
        columns = {
            row["name"]
            for row in connection.exec_driver_sql("PRAGMA table_info(tracesnapshot)").mappings()
        }
        trace_indexes = _index_names(connection, "tracesnapshot")
        canonical_indexes = _index_names(connection, "canonicaltraceevent")
        frontier_indexes = _index_names(connection, "frontieritem")
        protocol_indexes = _index_names(connection, "operationbridgelink")
        custody_indexes = _index_names(connection, "custodyassertion")
        provider_response_indexes = _index_names(connection, "providerresponserecord")
        provider_ledger_indexes = _index_names(connection, "providerledgerrecord")
        provider_kyc_indexes = _index_names(connection, "providerkycrecordrow")
        provider_trade_indexes = _index_names(connection, "providertraderecordrow")
        provider_withdrawal_indexes = _index_names(connection, "providerwithdrawalrecordrow")
        provider_session_indexes = _index_names(connection, "providersessionrecordrow")

    assert {
        "snapshot_schema",
        "status",
        "cache_identity",
        "parent_snapshot_id",
        "superseded_by_id",
    } <= columns
    with Session(engine) as session:
        row = session.get(TraceSnapshot, 1)
        assert row is not None
        assert row.snapshot_schema == "trinetra.snapshot/2"
        assert row.status == "complete"
        assert row.cache_identity == "legacy-cache"
        assert row.parent_snapshot_id is None
        assert row.superseded_by_id is None

    assert "uq_tracesnapshot_case_cache_identity" in trace_indexes
    assert "uq_canonicaltraceevent_snapshot_evidence" in canonical_indexes
    assert "uq_frontieritem_snapshot_event" in frontier_indexes
    assert "uq_operationbridgelink_case_join_input" in protocol_indexes
    assert "uq_custodyassertion_provider_credit" in custody_indexes
    assert "uq_providerresponserecord_provider_response" in provider_response_indexes
    assert "uq_providerledgerrecord_response_entry" in provider_ledger_indexes
    assert "uq_providerkycrecordrow_response_account" in provider_kyc_indexes
    assert "uq_providertraderecordrow_response_trade" in provider_trade_indexes
    assert "uq_providerwithdrawalrecordrow_response_withdrawal" in provider_withdrawal_indexes
    assert "uq_providersessionrecordrow_response_session" in provider_session_indexes

    engine.dispose()
    if db_path.exists():
        db_path.unlink()


def _index_names(connection, table_name: str) -> set[str]:
    return {
        row["name"]
        for row in connection.exec_driver_sql(f"PRAGMA index_list({table_name})").mappings()
    }
