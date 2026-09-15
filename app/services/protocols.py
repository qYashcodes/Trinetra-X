from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import OperationBridgeLink
from app.services.time import now_ms

EvidenceKind = Literal["verified", "correlation", "boundary"]
ExecutionState = Literal[
    "pending",
    "executed",
    "refunded",
    "expired",
    "partial",
    "unknown_boundary",
]


class ProtocolEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ProtocolLink:
    protocol: str
    deployment: str
    version: str
    input_evidence_ref: str
    output_evidence_ref: str | None
    join_identifier: str
    input_asset: str
    output_asset: str
    input_amount_base: int
    output_amount_base: int | None
    fee_amount_base: int | None
    recipient: str | None
    position_ref: str | None
    execution_state: ExecutionState
    finality: str
    evidence_kind: EvidenceKind
    proof: dict


def cctp_verified_link(
    *,
    source_domain: int,
    destination_domain: int,
    nonce: str,
    burn_txid: str,
    message_ref: str,
    attestation_ref: str,
    destination_txid: str,
    mint_recipient: str,
    burn_token: str,
    mint_token: str,
    amount_base: int,
    fee_amount_base: int,
    finality: str,
    deployment: str = "circle-cctp",
    version: str = "v2",
) -> ProtocolLink:
    required = {
        "nonce": nonce,
        "burn_txid": burn_txid,
        "message_ref": message_ref,
        "attestation_ref": attestation_ref,
        "destination_txid": destination_txid,
        "mint_recipient": mint_recipient,
        "burn_token": burn_token,
        "mint_token": mint_token,
    }
    _require_strings(required)
    if source_domain == destination_domain:
        raise ProtocolEvidenceError("CCTP source and destination domains must differ.")
    if amount_base <= 0 or fee_amount_base < 0:
        raise ProtocolEvidenceError("CCTP amounts must be positive and fees non-negative.")
    if fee_amount_base > amount_base:
        raise ProtocolEvidenceError("CCTP fee cannot exceed burned amount.")
    return ProtocolLink(
        protocol="circle_cctp",
        deployment=deployment,
        version=version,
        input_evidence_ref=burn_txid,
        output_evidence_ref=destination_txid,
        join_identifier=f"{source_domain}:{destination_domain}:{nonce}",
        input_asset=burn_token,
        output_asset=mint_token,
        input_amount_base=amount_base,
        output_amount_base=amount_base - fee_amount_base,
        fee_amount_base=fee_amount_base,
        recipient=mint_recipient,
        position_ref=None,
        execution_state="executed",
        finality=finality,
        evidence_kind="verified",
        proof={
            "source_domain": source_domain,
            "destination_domain": destination_domain,
            "nonce": nonce,
            "message_ref": message_ref,
            "attestation_ref": attestation_ref,
            "mint_recipient": mint_recipient,
        },
    )


def correlation_candidate_link(
    *,
    protocol: str,
    deployment: str,
    input_evidence_ref: str,
    candidate_ref: str,
    input_asset: str,
    output_asset: str,
    input_amount_base: int,
    features: dict,
) -> ProtocolLink:
    _require_strings(
        {
            "protocol": protocol,
            "deployment": deployment,
            "input_evidence_ref": input_evidence_ref,
            "candidate_ref": candidate_ref,
            "input_asset": input_asset,
            "output_asset": output_asset,
        }
    )
    if input_amount_base <= 0:
        raise ProtocolEvidenceError("Correlation candidate amount must be positive.")
    return ProtocolLink(
        protocol=protocol,
        deployment=deployment,
        version="unknown",
        input_evidence_ref=input_evidence_ref,
        output_evidence_ref=candidate_ref,
        join_identifier=f"candidate:{input_evidence_ref}:{candidate_ref}",
        input_asset=input_asset,
        output_asset=output_asset,
        input_amount_base=input_amount_base,
        output_amount_base=None,
        fee_amount_base=None,
        recipient=None,
        position_ref=None,
        execution_state="pending",
        finality="unverified",
        evidence_kind="correlation",
        proof={"features": dict(features), "limitation": "correlation is not a cryptographic join"},
    )


def swap_operation_link(
    *,
    protocol: str,
    deployment: str,
    version: str,
    router: str,
    pool_manager: str | None,
    input_evidence_ref: str,
    output_evidence_ref: str,
    input_asset: str,
    output_asset: str,
    input_amount_base: int,
    output_amount_base: int,
    recipient: str,
    finality: str,
    flash_accounting: bool = False,
) -> ProtocolLink:
    _require_strings(
        {
            "protocol": protocol,
            "deployment": deployment,
            "version": version,
            "router": router,
            "input_evidence_ref": input_evidence_ref,
            "output_evidence_ref": output_evidence_ref,
            "input_asset": input_asset,
            "output_asset": output_asset,
            "recipient": recipient,
        }
    )
    if input_amount_base <= 0 or output_amount_base <= 0:
        raise ProtocolEvidenceError("Swap amounts must be positive.")
    return ProtocolLink(
        protocol=protocol,
        deployment=deployment,
        version=version,
        input_evidence_ref=input_evidence_ref,
        output_evidence_ref=output_evidence_ref,
        join_identifier=f"swap:{input_evidence_ref}:{output_evidence_ref}",
        input_asset=input_asset,
        output_asset=output_asset,
        input_amount_base=input_amount_base,
        output_amount_base=output_amount_base,
        fee_amount_base=None,
        recipient=recipient,
        position_ref=None,
        execution_state="executed",
        finality=finality,
        evidence_kind="verified",
        proof={
            "router": router,
            "pool_manager": pool_manager,
            "flash_accounting": flash_accounting,
            "note": (
                "Trace continues from verified output recipient, not from router or pool inventory."
            ),
        },
    )


def unknown_operation_boundary(
    *,
    input_evidence_ref: str,
    asset: str,
    amount_base: int,
    reason: str,
) -> ProtocolLink:
    _require_strings({"input_evidence_ref": input_evidence_ref, "asset": asset, "reason": reason})
    if amount_base < 0:
        raise ProtocolEvidenceError("Boundary amount cannot be negative.")
    return ProtocolLink(
        protocol="unknown_operation",
        deployment="unknown",
        version="unknown",
        input_evidence_ref=input_evidence_ref,
        output_evidence_ref=None,
        join_identifier=f"boundary:{input_evidence_ref}",
        input_asset=asset,
        output_asset=asset,
        input_amount_base=amount_base,
        output_amount_base=None,
        fee_amount_base=None,
        recipient=None,
        position_ref=None,
        execution_state="unknown_boundary",
        finality="unresolved",
        evidence_kind="boundary",
        proof={"reason": reason},
    )


def record_protocol_link(
    session: Session,
    *,
    case_id: int,
    snapshot_id: int | None,
    link: ProtocolLink,
) -> OperationBridgeLink:
    existing = _existing_protocol_link(session, case_id=case_id, link=link)
    if existing:
        _validate_duplicate_protocol_link(existing, link)
        return existing
    row = OperationBridgeLink(
        case_id=case_id,
        snapshot_id=snapshot_id,
        protocol=link.protocol,
        deployment=link.deployment,
        version=link.version,
        input_evidence_ref=link.input_evidence_ref,
        output_evidence_ref=link.output_evidence_ref,
        join_identifier=link.join_identifier,
        input_asset=link.input_asset,
        output_asset=link.output_asset,
        input_amount_base=link.input_amount_base,
        output_amount_base=link.output_amount_base,
        fee_amount_base=link.fee_amount_base,
        recipient=link.recipient,
        position_ref=link.position_ref,
        execution_state=link.execution_state,
        finality=link.finality,
        evidence_kind=link.evidence_kind,
        proof=link.proof,
        created_ts_ms=now_ms(),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _existing_protocol_link(session, case_id=case_id, link=link)
        if existing:
            _validate_duplicate_protocol_link(existing, link)
            return existing
        raise
    session.refresh(row)
    return row


def consumes_attribution(link: ProtocolLink) -> bool:
    return link.evidence_kind == "verified" and link.execution_state == "executed"


def _existing_protocol_link(
    session: Session,
    *,
    case_id: int,
    link: ProtocolLink,
) -> OperationBridgeLink | None:
    return session.exec(
        select(OperationBridgeLink)
        .where(OperationBridgeLink.case_id == case_id)
        .where(OperationBridgeLink.protocol == link.protocol)
        .where(OperationBridgeLink.deployment == link.deployment)
        .where(OperationBridgeLink.join_identifier == link.join_identifier)
        .where(OperationBridgeLink.input_evidence_ref == link.input_evidence_ref)
    ).first()


def _validate_duplicate_protocol_link(row: OperationBridgeLink, link: ProtocolLink) -> None:
    comparisons = {
        "version": link.version,
        "output_evidence_ref": link.output_evidence_ref,
        "input_asset": link.input_asset,
        "output_asset": link.output_asset,
        "input_amount_base": link.input_amount_base,
        "output_amount_base": link.output_amount_base,
        "fee_amount_base": link.fee_amount_base,
        "recipient": link.recipient,
        "position_ref": link.position_ref,
        "execution_state": link.execution_state,
        "finality": link.finality,
        "evidence_kind": link.evidence_kind,
        "proof": link.proof,
    }
    for field_name, expected in comparisons.items():
        if getattr(row, field_name) != expected:
            raise ProtocolEvidenceError("Duplicate protocol evidence conflicts with existing row.")


def _require_strings(values: dict[str, object]) -> None:
    missing = [name for name, value in values.items() if not str(value or "").strip()]
    if missing:
        raise ProtocolEvidenceError(f"Missing protocol evidence field: {missing[0]}")
