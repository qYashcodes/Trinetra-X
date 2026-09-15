from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Case, OperationBridgeLink
from app.services.protocols import (
    ProtocolEvidenceError,
    cctp_verified_link,
    consumes_attribution,
    correlation_candidate_link,
    record_protocol_link,
    swap_operation_link,
    unknown_operation_boundary,
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
        ack_no="NCRP/2026/TEST/PROTOCOL",
        category="investment fraud",
        jurisdiction="Test Cyber Cell",
        filed_ts_ms=ts,
        amount_reported_base=1_000,
        asset_symbol="USDC",
        asset_decimals=6,
        chain_family="EVM",
        chain_network="ethereum",
        reported_address="0x0000000000000000000000000000000000000000",
        payment_txid="b" * 64,
        payment_ts_ms=ts,
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def test_cctp_verified_link_records_burn_attestation_and_destination_mint(db: Session) -> None:
    case = make_case(db)
    link = cctp_verified_link(
        source_domain=0,
        destination_domain=6,
        nonce="nonce-1",
        burn_txid="burn-tx",
        message_ref="message-1",
        attestation_ref="attestation-1",
        destination_txid="mint-tx",
        mint_recipient="0xrecipient",
        burn_token="ethereum:USDC",
        mint_token="base:USDC",
        amount_base=100_000_000,
        fee_amount_base=1_000,
        finality="finalized",
    )
    row = record_protocol_link(db, case_id=case.id, snapshot_id=None, link=link)

    assert isinstance(row, OperationBridgeLink)
    assert row.protocol == "circle_cctp"
    assert row.join_identifier == "0:6:nonce-1"
    assert row.output_amount_base == 99_999_000
    assert row.recipient == "0xrecipient"
    assert row.proof["attestation_ref"] == "attestation-1"
    assert consumes_attribution(link) is True


def test_protocol_link_recording_is_idempotent_and_rejects_conflicting_duplicate(
    db: Session,
) -> None:
    case = make_case(db)
    link = cctp_verified_link(
        source_domain=0,
        destination_domain=6,
        nonce="nonce-duplicate",
        burn_txid="burn-tx",
        message_ref="message-1",
        attestation_ref="attestation-1",
        destination_txid="mint-tx",
        mint_recipient="0xrecipient",
        burn_token="ethereum:USDC",
        mint_token="base:USDC",
        amount_base=100_000_000,
        fee_amount_base=1_000,
        finality="finalized",
    )

    first = record_protocol_link(db, case_id=case.id, snapshot_id=None, link=link)
    second = record_protocol_link(db, case_id=case.id, snapshot_id=None, link=link)

    assert first.id == second.id

    conflicting = cctp_verified_link(
        source_domain=0,
        destination_domain=6,
        nonce="nonce-duplicate",
        burn_txid="burn-tx",
        message_ref="message-1",
        attestation_ref="attestation-1",
        destination_txid="different-mint-tx",
        mint_recipient="0xrecipient",
        burn_token="ethereum:USDC",
        mint_token="base:USDC",
        amount_base=100_000_000,
        fee_amount_base=1_000,
        finality="finalized",
    )
    with pytest.raises(ProtocolEvidenceError, match="conflicts"):
        record_protocol_link(db, case_id=case.id, snapshot_id=None, link=conflicting)


def test_cctp_requires_distinct_domains_and_positive_amounts() -> None:
    with pytest.raises(ProtocolEvidenceError):
        cctp_verified_link(
            source_domain=1,
            destination_domain=1,
            nonce="nonce-1",
            burn_txid="burn-tx",
            message_ref="message-1",
            attestation_ref="attestation-1",
            destination_txid="mint-tx",
            mint_recipient="0xrecipient",
            burn_token="ethereum:USDC",
            mint_token="base:USDC",
            amount_base=100,
            fee_amount_base=0,
            finality="finalized",
        )
    with pytest.raises(ProtocolEvidenceError, match="fee cannot exceed"):
        cctp_verified_link(
            source_domain=1,
            destination_domain=2,
            nonce="nonce-2",
            burn_txid="burn-tx",
            message_ref="message-1",
            attestation_ref="attestation-1",
            destination_txid="mint-tx",
            mint_recipient="0xrecipient",
            burn_token="ethereum:USDC",
            mint_token="base:USDC",
            amount_base=100,
            fee_amount_base=101,
            finality="finalized",
        )


def test_correlation_candidate_does_not_consume_attribution() -> None:
    link = correlation_candidate_link(
        protocol="unknown_bridge",
        deployment="unknown",
        input_evidence_ref="source-transfer",
        candidate_ref="candidate-transfer",
        input_asset="TRON:USDT",
        output_asset="ethereum:USDT",
        input_amount_base=50_000_000,
        features={"time_delta_s": 60, "amount_delta_base": 1_000},
    )

    assert link.evidence_kind == "correlation"
    assert link.execution_state == "pending"
    assert consumes_attribution(link) is False
    assert "not a cryptographic join" in link.proof["limitation"]


def test_swap_operation_continues_from_output_recipient_not_router() -> None:
    link = swap_operation_link(
        protocol="uniswap",
        deployment="ethereum-mainnet",
        version="v4",
        router="0xrouter",
        pool_manager="0xpoolmanager",
        input_evidence_ref="transfer-in",
        output_evidence_ref="transfer-out",
        input_asset="ethereum:USDC",
        output_asset="ethereum:ETH",
        input_amount_base=100_000_000,
        output_amount_base=30_000_000_000_000_000,
        recipient="0xbeneficiary",
        finality="finalized",
        flash_accounting=True,
    )

    assert link.protocol == "uniswap"
    assert link.recipient == "0xbeneficiary"
    assert link.proof["router"] == "0xrouter"
    assert link.proof["pool_manager"] == "0xpoolmanager"
    assert link.proof["flash_accounting"] is True
    assert consumes_attribution(link) is True


def test_unknown_operation_is_boundary_not_successful_generic_transfer() -> None:
    link = unknown_operation_boundary(
        input_evidence_ref="tx:log:7",
        asset="ethereum:USDC",
        amount_base=1_000,
        reason="Unsupported hook semantics",
    )

    assert link.protocol == "unknown_operation"
    assert link.evidence_kind == "boundary"
    assert link.execution_state == "unknown_boundary"
    assert link.output_evidence_ref is None
    assert consumes_attribution(link) is False
