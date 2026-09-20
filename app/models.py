from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


class OfficerRole(str, Enum):
    io = "io"
    supervisor = "supervisor"
    admin = "admin"


class CaseStage(str, Enum):
    intake = "intake"
    trace_running = "trace_running"
    trace_failed = "trace_failed"
    trace_unsupported = "trace_unsupported"
    trace_incomplete = "trace_incomplete"
    stationary_observed = "stationary_observed"
    custody_candidate = "custody_candidate"
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


class OfficerProfile(SQLModel, table=True):
    """Stable officer identity used for assignment and escalation scope.

    Login sessions deliberately remain separate: a session is evidence of one
    authentication event, while this row is the durable directory entry.
    """

    id: int | None = Field(default=None, primary_key=True)
    pis: str = Field(index=True, unique=True)
    name: str
    rank: str
    unit: str
    role: OfficerRole = Field(index=True)
    active: bool = Field(default=True, index=True)
    created_ts_ms: int
    updated_ts_ms: int


class CaseAssignment(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("case_id", name="uq_caseassignment_case"),)

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    assigned_io_pis: str = Field(index=True)
    supervising_acp_pis: str = Field(index=True)
    created_ts_ms: int
    updated_ts_ms: int


class CaseWatcher(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("case_id", "officer_pis", name="uq_casewatcher_case_officer"),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    officer_pis: str = Field(index=True)
    reason: str
    created_ts_ms: int


class TraceSnapshot(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("case_id", "cache_identity", name="uq_tracesnapshot_case_cache_identity"),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    version: int = 1
    snapshot_schema: str = "trinetra.snapshot/2"
    status: str = Field(default="complete", index=True)
    cache_identity: str | None = Field(default=None, index=True)
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


class CanonicalTraceEvent(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "evidence_ref",
            name="uq_canonicaltraceevent_snapshot_evidence",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    chain_family: str = Field(index=True)
    chain_network: str = Field(index=True)
    block_hash: str | None = Field(default=None, index=True)
    block_height: int | None = Field(default=None, index=True)
    txid: str = Field(index=True)
    tx_index: int | None = None
    event_index: int | None = Field(default=None, index=True)
    output_index: int | None = Field(default=None, index=True)
    asset_identifier: str
    source_address: str | None = Field(default=None, index=True)
    destination_address: str | None = Field(default=None, index=True)
    amount_base: int
    success: bool = True
    finality: str = Field(default="unknown", index=True)
    evidence_ref: str
    raw_sha256: str
    observed_ts_ms: int | None = None
    created_ts_ms: int


class SourceCoverage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    provider: str = Field(index=True)
    chain_family: str = Field(index=True)
    chain_network: str = Field(index=True)
    query_range: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    pagination_cursor: str | None = None
    observed_watermark: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    retrieval_ts_ms: int
    completeness: str = Field(index=True)
    gaps: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    conflicts: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))


class AttributedLot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    seed_event_ref: str = Field(index=True)
    asset_identifier: str = Field(index=True)
    current_address: str = Field(index=True)
    remaining_amount_base: int
    arrival_cursor: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    allocation_policy: str
    allocation_version: str
    ancestry: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    rounding_state: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    state: str = Field(default="pending", index=True)
    created_ts_ms: int
    updated_ts_ms: int


class FrontierItem(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("snapshot_id", "event_ref", name="uq_frontieritem_snapshot_event"),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    lot_id: int | None = Field(default=None, index=True, foreign_key="attributedlot.id")
    event_ref: str | None = Field(default=None, index=True)
    priority_base: int
    priority_reason: str
    depth: int
    lease_version: int = 0
    state: str = Field(default="queued", index=True)
    deferral_reason: str | None = Field(default=None, index=True)
    resume_cursor: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_ts_ms: int
    updated_ts_ms: int


class OperationBridgeLink(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "protocol",
            "deployment",
            "join_identifier",
            "input_evidence_ref",
            name="uq_operationbridgelink_case_join_input",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    protocol: str = Field(index=True)
    deployment: str = Field(index=True)
    version: str = Field(index=True)
    input_evidence_ref: str
    output_evidence_ref: str | None = Field(default=None, index=True)
    join_identifier: str = Field(index=True)
    input_asset: str
    output_asset: str
    input_amount_base: int
    output_amount_base: int | None = None
    fee_amount_base: int | None = None
    recipient: str | None = Field(default=None, index=True)
    position_ref: str | None = Field(default=None, index=True)
    execution_state: str = Field(index=True)
    finality: str = Field(index=True)
    evidence_kind: str = Field(index=True)
    proof: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_ts_ms: int


class CustodyAssertion(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "provider_key",
            "exact_credit_ref",
            name="uq_custodyassertion_provider_credit",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    provider_key: str = Field(index=True)
    chain_family: str = Field(index=True)
    chain_network: str = Field(index=True)
    deposit_address: str = Field(index=True)
    exact_credit_ref: str
    account_reference: str | None = Field(default=None, index=True)
    service_role: str
    valid_from_ms: int | None = None
    valid_to_ms: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    dispute_status: str = Field(default="undisputed", index=True)
    current_balance_base: int | None = None
    recoverable_amount_base: int | None = None
    created_ts_ms: int


class ProviderResponseRecord(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "provider_key",
            "response_ref",
            name="uq_providerresponserecord_provider_response",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    custody_assertion_id: int = Field(index=True, foreign_key="custodyassertion.id")
    provider_key: str = Field(index=True)
    response_ref: str = Field(index=True)
    signed_by: str
    received_ts_ms: int
    response_sha256: str = Field(index=True)
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_ts_ms: int


class ProviderLedgerRecord(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "provider_response_id",
            "entry_ref",
            name="uq_providerledgerrecord_response_entry",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    provider_response_id: int = Field(index=True, foreign_key="providerresponserecord.id")
    custody_assertion_id: int = Field(index=True, foreign_key="custodyassertion.id")
    entry_ref: str = Field(index=True)
    account_reference: str = Field(index=True)
    asset_identifier: str = Field(index=True)
    amount_base: int
    direction: str = Field(index=True)
    occurred_ts_ms: int
    chain_credit_ref: str | None = Field(default=None, index=True)
    created_ts_ms: int


class ProviderKycRecordRow(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "provider_response_id",
            "account_reference",
            name="uq_providerkycrecordrow_response_account",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    provider_response_id: int = Field(index=True, foreign_key="providerresponserecord.id")
    custody_assertion_id: int = Field(index=True, foreign_key="custodyassertion.id")
    account_reference: str = Field(index=True)
    beneficiary_ref: str = Field(index=True)
    verification_status: str = Field(index=True)
    fields: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_ts_ms: int


class ProviderTradeRecordRow(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "provider_response_id",
            "trade_ref",
            name="uq_providertraderecordrow_response_trade",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    provider_response_id: int = Field(index=True, foreign_key="providerresponserecord.id")
    custody_assertion_id: int = Field(index=True, foreign_key="custodyassertion.id")
    trade_ref: str = Field(index=True)
    account_reference: str = Field(index=True)
    base_asset: str
    quote_asset: str
    base_amount: int
    quote_amount: int
    executed_ts_ms: int
    created_ts_ms: int


class ProviderWithdrawalRecordRow(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "provider_response_id",
            "withdrawal_ref",
            name="uq_providerwithdrawalrecordrow_response_withdrawal",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    provider_response_id: int = Field(index=True, foreign_key="providerresponserecord.id")
    custody_assertion_id: int = Field(index=True, foreign_key="custodyassertion.id")
    withdrawal_ref: str = Field(index=True)
    account_reference: str = Field(index=True)
    asset_identifier: str = Field(index=True)
    amount_base: int
    destination: str = Field(index=True)
    requested_ts_ms: int
    txid: str | None = Field(default=None, index=True)
    created_ts_ms: int


class ProviderSessionRecordRow(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "provider_response_id",
            "session_ref",
            name="uq_providersessionrecordrow_response_session",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    provider_response_id: int = Field(index=True, foreign_key="providerresponserecord.id")
    custody_assertion_id: int = Field(index=True, foreign_key="custodyassertion.id")
    session_ref: str = Field(index=True)
    account_reference: str = Field(index=True)
    started_ts_ms: int
    ip_country: str | None = Field(default=None, index=True)
    device_ref: str | None = Field(default=None, index=True)
    created_ts_ms: int


class CustodyAction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    custody_assertion_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="custodyassertion.id",
    )
    target_kind: str
    target_ref: str
    amount_base: int | None = None
    asset_identifier: str | None = None
    jurisdiction: str | None = None
    authority: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    expiry_ts_ms: int | None = None
    status: str = Field(default="draft", index=True)
    requested_by: str | None = None
    reviewed_by: str | None = None
    receipts: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    verified_outcome: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_ts_ms: int
    updated_ts_ms: int


class WalletWatch(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "chain_family",
            "chain_network",
            "asset_identifier",
            "address",
            name="uq_walletwatch_case_asset_address",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    chain_family: str = Field(index=True)
    chain_network: str = Field(index=True)
    asset_identifier: str = Field(index=True)
    asset_symbol: str
    asset_decimals: int
    address: str = Field(index=True)
    source: str = Field(index=True)
    pinned: bool = Field(default=False, index=True)
    officer_priority: int = 0
    status: str = Field(default="active", index=True)
    capacity_reason: str | None = None
    tier: str = Field(default="standard", index=True)
    attributed_value_base: int
    last_movement_ts_ms: int | None = Field(default=None, index=True)
    last_checked_ts_ms: int | None = None
    next_poll_ts_ms: int | None = Field(default=None, index=True)
    poll_cursor: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    last_error_kind: str | None = Field(default=None, index=True)
    last_error_ts_ms: int | None = None
    created_by_pis: str | None = Field(default=None, index=True)
    created_ts_ms: int
    updated_ts_ms: int


class WatchAlert(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("watch_id", "event_ref", name="uq_watchalert_watch_event"),
    )

    id: int | None = Field(default=None, primary_key=True)
    watch_id: int = Field(index=True, foreign_key="walletwatch.id")
    case_id: int = Field(index=True, foreign_key="case.id")
    snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    event_ref: str = Field(index=True)
    trigger_kind: str = Field(index=True)
    trigger_reasons: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    priority: str = Field(index=True)
    state: str = Field(default="unread", index=True)
    source_address: str
    destination_address: str
    amount_base: int
    asset_identifier: str
    txid: str = Field(index=True)
    event_index: int | None = Field(default=None, index=True)
    block_height: int | None = Field(default=None, index=True)
    observed_ts_ms: int
    retrieval_ts_ms: int | None = None
    provider_request_ref: str | None = None
    raw_sha256: str | None = Field(default=None, index=True)
    observation_only: bool = True
    node_ref: str | None = Field(default=None, index=True)
    assigned_unit: str
    assigned_to_pis: str | None = Field(default=None, index=True)
    read_by_pis: str | None = None
    read_ts_ms: int | None = None
    acknowledged_by_pis: str | None = None
    acknowledged_ts_ms: int | None = None
    snoozed_until_ms: int | None = Field(default=None, index=True)
    snooze_return_state: str | None = None
    dismissed_by_pis: str | None = None
    dismissed_ts_ms: int | None = None
    dismissal_reason: str | None = None
    resolved_by_pis: str | None = None
    resolved_ts_ms: int | None = None
    created_ts_ms: int
    updated_ts_ms: int


class WatchAlertEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    alert_id: int = Field(index=True, foreign_key="watchalert.id")
    actor_pis: str = Field(index=True)
    action: str = Field(index=True)
    from_state: str
    to_state: str
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_ts_ms: int


class WatchGraphExtension(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("case_id", "event_ref", name="uq_watchgraphextension_case_event"),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    watch_id: int = Field(index=True, foreign_key="walletwatch.id")
    alert_id: int = Field(index=True, foreign_key="watchalert.id")
    source_snapshot_id: int | None = Field(default=None, index=True, foreign_key="tracesnapshot.id")
    new_snapshot_id: int = Field(index=True, foreign_key="tracesnapshot.id")
    event_ref: str = Field(index=True)
    node_ref: str = Field(index=True)
    event_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    new_since_last_view: bool = Field(default=True, index=True)
    acknowledged_by_pis: str | None = None
    acknowledged_ts_ms: int | None = None
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
    tracker_status: str = Field(default="drafted", index=True)
    tracker_sub_outcome: str | None = None
    tracker_last_note: str | None = None
    tracker_updated_ts_ms: int | None = None
    dispatched_ts_ms: int | None = Field(default=None, index=True)
    created_by_pis: str
    created_ts_ms: int


class NoticeDraft(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("finding_id", name="uq_noticedraft_finding"),)

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    finding_id: int = Field(index=True, foreign_key="finding.id")
    notice_id: int | None = Field(default=None, index=True, foreign_key="notice.id")
    stage: str = Field(default="parameters", index=True)
    notice_type: str = Field(default="freeze", index=True)
    parameters: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    annex_enabled: bool = False
    generated: bool = False
    dirty: bool = False
    active_version_no: int | None = None
    attested_by_pis: str | None = Field(default=None, index=True)
    attested_ts_ms: int | None = None
    attested_version_no: int | None = None
    created_by_pis: str = Field(index=True)
    created_ts_ms: int
    updated_ts_ms: int


class NoticeVersion(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("draft_id", "version_no", name="uq_noticeversion_draft_version"),
    )

    id: int | None = Field(default=None, primary_key=True)
    draft_id: int = Field(index=True, foreign_key="noticedraft.id")
    version_no: int = Field(index=True)
    parent_version_id: int | None = Field(default=None, foreign_key="noticeversion.id")
    tag: str = Field(default="draft", index=True)
    content: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    rendered_html: str
    content_sha256: str = Field(index=True)
    change_summary: str = "Initial generated draft"
    acp_remarks: str | None = None
    caused_by_remarks: str | None = None
    immutable: bool = False
    countersigned_by_pis: str | None = Field(default=None, index=True)
    countersigned_ts_ms: int | None = None
    created_by_pis: str = Field(index=True)
    created_ts_ms: int


class NoticeAttachment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    draft_id: int = Field(index=True, foreign_key="noticedraft.id")
    version_id: int | None = Field(default=None, index=True, foreign_key="noticeversion.id")
    slot: str = Field(index=True)
    source: str = Field(index=True)
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str = Field(index=True)
    storage_ref: str
    provenance: str | None = None
    simulated: bool = False
    superseded_by_id: int | None = Field(default=None, foreign_key="noticeattachment.id")
    created_by_pis: str = Field(index=True)
    created_ts_ms: int


class NoticeVerificationState(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "draft_id",
            "officer_session_id",
            name="uq_noticeverification_draft_session",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    draft_id: int = Field(index=True, foreign_key="noticedraft.id")
    officer_session_id: int = Field(index=True, foreign_key="officersession.id")
    failure_count: int = 0
    locked_until_ts_ms: int | None = None
    last_method: str | None = None
    verified_ts_ms: int | None = None
    updated_ts_ms: int


class NoticeTrackerEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    notice_id: int = Field(index=True, foreign_key="notice.id")
    actor_pis: str
    from_status: str | None = None
    to_status: str = Field(index=True)
    sub_outcome: str | None = None
    note: str | None = None
    created_ts_ms: int = Field(index=True)


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


class DispatchRecord(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("notice_id", name="uq_dispatchrecord_notice"),)

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    notice_id: int = Field(index=True, foreign_key="notice.id")
    notice_version_id: int | None = Field(default=None, index=True, foreign_key="noticeversion.id")
    notice_type: str = Field(default="freeze", index=True)
    vasp_label: str
    stage: str = Field(default="drafted", index=True)
    acknowledgement_state: str = Field(default="awaiting", index=True)
    dispatched_ts_ms: int | None = Field(default=None, index=True)
    acknowledged_ts_ms: int | None = None
    closed_ts_ms: int | None = None
    sla_window_minutes: int = 1_440
    sla_due_ts_ms: int | None = Field(default=None, index=True)
    sla_frozen_remaining_ms: int | None = None
    sla_override_source: str | None = None
    sla_breach_audit_event_id: str | None = Field(default=None, index=True)
    assigned_io_pis: str = Field(index=True)
    supervising_acp_pis: str = Field(index=True)
    current_owner_pis: str = Field(index=True)
    escalation_level: int = 0
    created_ts_ms: int
    updated_ts_ms: int


class CaseEscalationAssignment(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "dispatch_record_id",
            "level",
            name="uq_caseescalation_dispatch_level",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_id: int = Field(index=True, foreign_key="case.id")
    dispatch_record_id: int = Field(index=True, foreign_key="dispatchrecord.id")
    level: int = Field(index=True)
    previous_owner_pis: str | None = Field(default=None, index=True)
    new_owner_pis: str = Field(index=True)
    reason: str
    assigned_by_pis: str = Field(index=True)
    created_ts_ms: int


class VaspSlaOverride(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    vasp_key: str = Field(index=True, unique=True)
    acknowledgement_minutes: int = 1_440
    source: str = "fixture_configuration"
    active: bool = True
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


class OfficerSession(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    token_sha256: str = Field(index=True, unique=True)
    officer_pis: str = Field(index=True)
    officer_name: str
    officer_rank: str
    officer_unit: str
    role: OfficerRole = Field(index=True)
    authentication_kind: str = Field(default="prototype_demonstration", index=True)
    device_label: str
    user_agent_sha256: str
    created_ts_ms: int
    last_active_ts_ms: int = Field(index=True)
    expires_ts_ms: int = Field(index=True)
    ended_ts_ms: int | None = Field(default=None, index=True)
    end_reason: str | None = Field(default=None, index=True)
    revoked: bool = Field(default=False, index=True)
    cases_touched: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    traces_run: int = 0
    artifacts_exported: int = 0


class AuditOutbox(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(index=True, unique=True)
    payload: dict[str, Any] = Field(sa_column=Column(JSON))
    created_ts_ms: int = Field(index=True)
    flushed_ts_ms: int | None = Field(default=None, index=True)
    attempts: int = 0
    last_error: str | None = None
