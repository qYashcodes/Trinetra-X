from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Connection
from sqlmodel import Session, SQLModel, create_engine

from app.settings import ROOT_DIR, settings


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return ROOT_DIR / url.removeprefix("sqlite:///")


db_path = _sqlite_path_from_url(settings.db_url)
if db_path is not None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as connection:
        upgrade_sqlite_schema(connection)
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
        connection.commit()


def get_session():
    with Session(engine) as session:
        yield session


def upgrade_sqlite_schema(connection: Connection) -> None:
    """Apply idempotent local SQLite upgrades for additive schema changes."""
    if connection.dialect.name != "sqlite":
        return
    _sqlite_add_missing_columns(
        connection,
        "tracesnapshot",
        {
            "snapshot_schema": "TEXT NOT NULL DEFAULT 'trinetra.snapshot/2'",
            "status": "TEXT NOT NULL DEFAULT 'complete'",
            "cache_identity": "TEXT",
            "parent_snapshot_id": "INTEGER",
            "superseded_by_id": "INTEGER",
        },
    )
    _sqlite_backfill_trace_cache_identity(connection)
    _sqlite_create_indexes(
        connection,
        [
            ("ix_tracesnapshot_status", "tracesnapshot", "status"),
            ("ix_tracesnapshot_cache_identity", "tracesnapshot", "cache_identity"),
            ("ix_tracesnapshot_parent_snapshot_id", "tracesnapshot", "parent_snapshot_id"),
            ("ix_tracesnapshot_superseded_by_id", "tracesnapshot", "superseded_by_id"),
        ],
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tracesnapshot_case_cache_identity "
        "ON tracesnapshot (case_id, cache_identity) WHERE cache_identity IS NOT NULL"
    )
    _sqlite_create_unique_indexes(connection)


def _sqlite_add_missing_columns(
    connection: Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    if not _sqlite_table_exists(connection, table_name):
        return
    existing = {
        str(row["name"])
        for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})").mappings()
    }
    for column_name, column_sql in columns.items():
        if column_name not in existing:
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"
            )


def _sqlite_create_indexes(
    connection: Connection,
    indexes: list[tuple[str, str, str]],
) -> None:
    for index_name, table_name, column_name in indexes:
        if _sqlite_table_exists(connection, table_name):
            connection.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name})"
            )


def _sqlite_backfill_trace_cache_identity(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "tracesnapshot"):
        return
    try:
        connection.exec_driver_sql(
            "UPDATE tracesnapshot "
            "SET cache_identity = json_extract(result_json, '$.cache_identity') "
            "WHERE cache_identity IS NULL"
        )
    except Exception:
        return


def _sqlite_create_unique_indexes(connection: Connection) -> None:
    specs = [
        (
            "canonicaltraceevent",
            "uq_canonicaltraceevent_snapshot_evidence",
            "snapshot_id, evidence_ref",
            "snapshot_id IS NOT NULL",
        ),
        (
            "frontieritem",
            "uq_frontieritem_snapshot_event",
            "snapshot_id, event_ref",
            "snapshot_id IS NOT NULL AND event_ref IS NOT NULL",
        ),
        (
            "operationbridgelink",
            "uq_operationbridgelink_case_join_input",
            "case_id, protocol, deployment, join_identifier, input_evidence_ref",
            None,
        ),
        (
            "custodyassertion",
            "uq_custodyassertion_provider_credit",
            "provider_key, exact_credit_ref",
            None,
        ),
        (
            "providerresponserecord",
            "uq_providerresponserecord_provider_response",
            "provider_key, response_ref",
            None,
        ),
        (
            "providerledgerrecord",
            "uq_providerledgerrecord_response_entry",
            "provider_response_id, entry_ref",
            None,
        ),
        (
            "providerkycrecordrow",
            "uq_providerkycrecordrow_response_account",
            "provider_response_id, account_reference",
            None,
        ),
        (
            "providertraderecordrow",
            "uq_providertraderecordrow_response_trade",
            "provider_response_id, trade_ref",
            None,
        ),
        (
            "providerwithdrawalrecordrow",
            "uq_providerwithdrawalrecordrow_response_withdrawal",
            "provider_response_id, withdrawal_ref",
            None,
        ),
        (
            "providersessionrecordrow",
            "uq_providersessionrecordrow_response_session",
            "provider_response_id, session_ref",
            None,
        ),
    ]
    for table_name, index_name, columns, predicate in specs:
        if not _sqlite_table_exists(connection, table_name):
            continue
        sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns})"
        if predicate:
            sql = f"{sql} WHERE {predicate}"
        connection.exec_driver_sql(sql)


def _sqlite_table_exists(connection: Connection, table_name: str) -> bool:
    row = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).first()
    return row is not None
