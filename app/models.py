from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


class OfficerRole(str, Enum):
    io = "io"
    supervisor = "supervisor"
    admin = "admin"


class CaseStage(str, Enum):
    intake = "intake"
    trace_running = "trace_running"
    custody_found = "custody_found"
    notice_draft = "notice_draft"
    awaiting_countersignature = "awaiting_countersignature"
    countersigned = "countersigned"
    notice_out = "notice_out"
    restraint_confirmed = "restraint_confirmed"
    closed_no_custody = "closed_no_custody"


class Case(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ack_no: str = Field(index=True, unique=True)
    category: str
    jurisdiction: str
    filed_ts_ms: int
    amount_reported_base: int
    asset_symbol: str
    asset_decimals: int = 6
    chain_family: str
    chain_network: str
    reported_address: str = Field(index=True)
    payment_txid: str | None = Field(default=None, index=True)
    payment_ts_ms: int | None = None
    complainant_contact_redacted: str | None = None
    stage: CaseStage = Field(default=CaseStage.intake)
    created_ts_ms: int
    updated_ts_ms: int


class TraceSnapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    version: int = 1
    snapshot_schema: str = "trinetra.snapshot/2"
    status: str = Field(default="complete", index=True)
    parent_snapshot_id: int | None = Field(default=None, foreign_key="tracesnapshot.id")
    result_json: dict[str, Any] = Field(sa_column=Column(JSON))
    sha256: str = Field(index=True)
    chain_family: str
    chain_network: str
    asset_symbol: str
    asset_decimals: int
    bridge_candidate: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    case_link_evidence: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    superseded_by_id: int | None = Field(default=None, foreign_key="tracesnapshot.id")
    started_ts_ms: int
    closed_ts_ms: int | None = None


class TraceEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    snapshot_id: int = Field(index=True, foreign_key="tracesnapshot.id")
    seq: int = Field(index=True)
    event_type: str
    data: dict[str, Any] = Field(sa_column=Column(JSON))
    created_ts_ms: int


class Finding(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int = Field(index=True, foreign_key="tracesnapshot.id")
    terminal_kind: str
    custodian_key: str | None = None
    deposit_address: str | None = Field(default=None, index=True)
    amount_credited_base: int | None = None
    review_checks: dict[str, bool] = Field(default_factory=dict, sa_column=Column(JSON))
    created_ts_ms: int


class Notice(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    finding_id: int = Field(index=True, foreign_key="finding.id")
    notice_no: str = Field(index=True, unique=True)
    status: str = Field(default="draft", index=True)
    deadline_hours: int = 24
    pdf_sha256: str | None = None
    countersigned_by_pis: str | None = None
    countersigned_ts_ms: int | None = None
    created_by_pis: str
    created_ts_ms: int


class Dispatch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    notice_id: int = Field(index=True, foreign_key="notice.id")
    channel: str
    target: str
    status: str = Field(default="queued")
    attempts: int = 0
    last_error: str | None = None
    created_ts_ms: int
    updated_ts_ms: int


class VaspResponse(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    notice_id: int = Field(index=True, foreign_key="notice.id")
    received_ts_ms: int
    reference: str
    kyc_disclosed: bool
    amount_restrained_base: int
    account_reference_hint: str | None = None
    created_ts_ms: int


class WebAuthnCredential(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    officer_pis: str = Field(index=True)
    credential_id: str = Field(index=True, unique=True)
    public_key: str
    sign_count: int = 0
    created_ts_ms: int


class OidcIdentity(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    officer_pis: str = Field(index=True)
    provider: str
    subject: str = Field(index=True)
    trusted_claims: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_ts_ms: int
