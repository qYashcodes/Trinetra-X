from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    CustodyAction,
    CustodyAssertion,
    ProviderKycRecordRow,
    ProviderLedgerRecord,
    ProviderResponseRecord,
    ProviderSessionRecordRow,
    ProviderTradeRecordRow,
    ProviderWithdrawalRecordRow,
)
from app.services.hash import sha256_json
from app.services.time import now_ms

ActionStatus = Literal[
    "draft",
    "review",
    "reviewed",
    "submitted",
    "acknowledged",
    "confirmed",
    "confirmed_hold",
    "rejected",
    "expired",
    "released",
    "seizure_control",
    "restoration",
]

ALLOWED_ACTION_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"review", "reviewed", "expired"},
    "review": {"submitted", "expired"},
    "reviewed": {"submitted", "expired"},
    "submitted": {"acknowledged", "rejected", "expired"},
    "acknowledged": {"confirmed", "confirmed_hold", "rejected", "expired"},
    "confirmed": {"released", "seizure_control"},
    "confirmed_hold": {"released", "seizure_control"},
    "seizure_control": {"restoration", "released"},
    "rejected": set(),
    "expired": set(),
    "released": set(),
    "restoration": set(),
}

CONFIRMED_RESTRAINT_STATUSES = {"confirmed", "confirmed_hold", "seizure_control"}


class CustodyContractError(ValueError):
    pass


@dataclass(frozen=True)
class CustodyImport:
    provider_key: str
    chain_family: str
    chain_network: str
    deposit_address: str
    exact_credit_ref: str
    service_role: str
    account_reference: str | None = None
    current_balance_base: int | None = None
    recoverable_amount_base: int | None = None
    valid_from_ms: int | None = None
    valid_to_ms: int | None = None
    dispute_status: str = "undisputed"
    provenance: dict | None = None


@dataclass(frozen=True)
class ProviderLedgerEntry:
    entry_ref: str
    account_reference: str
    asset_identifier: str
    amount_base: int
    direction: Literal["credit", "debit"]
    occurred_ts_ms: int
    chain_credit_ref: str | None = None


@dataclass(frozen=True)
class ProviderKycRecord:
    account_reference: str
    beneficiary_ref: str
    verification_status: str
    fields: dict


@dataclass(frozen=True)
class ProviderTradeRecord:
    trade_ref: str
    account_reference: str
    base_asset: str
    quote_asset: str
    base_amount: int
    quote_amount: int
    executed_ts_ms: int


@dataclass(frozen=True)
class ProviderWithdrawalRecord:
    withdrawal_ref: str
    account_reference: str
    asset_identifier: str
    amount_base: int
    destination: str
    requested_ts_ms: int
    txid: str | None = None


@dataclass(frozen=True)
class ProviderSessionRecord:
    session_ref: str
    account_reference: str
    started_ts_ms: int
    ip_country: str | None = None
    device_ref: str | None = None


@dataclass(frozen=True)
class CertifiedProviderResponse:
    response_ref: str
    signed_by: str
    received_ts_ms: int
    deposit_assignment: CustodyImport
    ledger_entries: list[ProviderLedgerEntry]
    kyc: ProviderKycRecord | None = None
    trades: list[ProviderTradeRecord] | None = None
    withdrawals: list[ProviderWithdrawalRecord] | None = None
    sessions: list[ProviderSessionRecord] | None = None


def import_custody_assertion(
    session: Session,
    *,
    case_id: int,
    snapshot_id: int | None,
    payload: CustodyImport,
) -> CustodyAssertion:
    _validate_custody_import(payload)
    existing = _existing_assertion(session, payload)
    if existing:
        _validate_duplicate_assertion(existing, payload)
        return existing
    now = now_ms()
    provenance = dict(payload.provenance or {})
    provenance.setdefault("schema", "trinetra.custody_import/1")
    provenance.setdefault(
        "import_sha256",
        sha256_json(
            {
                "provider_key": payload.provider_key,
                "deposit_address": payload.deposit_address,
                "exact_credit_ref": payload.exact_credit_ref,
                "account_reference": payload.account_reference,
            }
        ),
    )
    assertion = CustodyAssertion(
        case_id=case_id,
        snapshot_id=snapshot_id,
        provider_key=payload.provider_key,
        chain_family=payload.chain_family,
        chain_network=payload.chain_network,
        deposit_address=payload.deposit_address,
        exact_credit_ref=payload.exact_credit_ref,
        account_reference=payload.account_reference,
        service_role=payload.service_role,
        valid_from_ms=payload.valid_from_ms,
        valid_to_ms=payload.valid_to_ms,
        provenance=provenance,
        dispute_status=payload.dispute_status,
        current_balance_base=payload.current_balance_base,
        recoverable_amount_base=payload.recoverable_amount_base,
        created_ts_ms=now,
    )
    session.add(assertion)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _existing_assertion(session, payload)
        if existing:
            _validate_duplicate_assertion(existing, payload)
            return existing
        raise
    session.refresh(assertion)
    return assertion


def import_certified_provider_response(
    session: Session,
    *,
    case_id: int,
    snapshot_id: int | None,
    response: CertifiedProviderResponse,
) -> CustodyAssertion:
    _validate_certified_response(response)
    payload = response.deposit_assignment
    provenance = dict(payload.provenance or {})
    provenance.update(
        {
            "certified_response": {
                "response_ref": response.response_ref,
                "signed_by": response.signed_by,
                "received_ts_ms": response.received_ts_ms,
            },
            "ledger_refs": [entry.entry_ref for entry in response.ledger_entries],
            "trade_refs": [trade.trade_ref for trade in response.trades or []],
            "withdrawal_refs": [
                withdrawal.withdrawal_ref for withdrawal in response.withdrawals or []
            ],
            "session_refs": [session_row.session_ref for session_row in response.sessions or []],
            "kyc": {
                "beneficiary_ref": response.kyc.beneficiary_ref,
                "verification_status": response.kyc.verification_status,
            }
            if response.kyc
            else None,
        }
    )
    certified_payload = CustodyImport(
        provider_key=payload.provider_key,
        chain_family=payload.chain_family,
        chain_network=payload.chain_network,
        deposit_address=payload.deposit_address,
        exact_credit_ref=payload.exact_credit_ref,
        service_role=payload.service_role,
        account_reference=payload.account_reference,
        current_balance_base=payload.current_balance_base,
        recoverable_amount_base=payload.recoverable_amount_base,
        valid_from_ms=payload.valid_from_ms,
        valid_to_ms=payload.valid_to_ms,
        dispute_status=payload.dispute_status,
        provenance=provenance,
    )
    assertion = import_custody_assertion(
        session,
        case_id=case_id,
        snapshot_id=snapshot_id,
        payload=certified_payload,
    )
    _persist_provider_response_records(
        session,
        case_id=case_id,
        snapshot_id=snapshot_id,
        assertion=assertion,
        response=response,
    )
    return assertion


def create_action_draft(
    session: Session,
    *,
    case_id: int,
    custody_assertion_id: int | None,
    target_kind: str,
    target_ref: str,
    requested_by: str,
    amount_base: int | None = None,
    asset_identifier: str | None = None,
    jurisdiction: str | None = None,
    authority: str | None = None,
    scope: dict | None = None,
    expiry_ts_ms: int | None = None,
) -> CustodyAction:
    if not target_kind or not target_ref:
        raise CustodyContractError("Action target kind and reference are required.")
    if amount_base is not None and amount_base < 0:
        raise CustodyContractError("Action amount cannot be negative.")
    _validate_action_against_assertion(
        session,
        custody_assertion_id=custody_assertion_id,
        amount_base=amount_base,
    )
    now = now_ms()
    action = CustodyAction(
        case_id=case_id,
        custody_assertion_id=custody_assertion_id,
        target_kind=target_kind,
        target_ref=target_ref,
        amount_base=amount_base,
        asset_identifier=asset_identifier,
        jurisdiction=jurisdiction,
        authority=authority,
        scope=dict(scope or {}),
        expiry_ts_ms=expiry_ts_ms,
        status="draft",
        requested_by=requested_by,
        created_ts_ms=now,
        updated_ts_ms=now,
    )
    session.add(action)
    session.commit()
    session.refresh(action)
    return action


def advance_action_status(
    session: Session,
    action: CustodyAction,
    *,
    to_status: ActionStatus,
    actor: str,
    receipt: dict | None = None,
    verified_outcome: dict | None = None,
) -> CustodyAction:
    if to_status not in ALLOWED_ACTION_TRANSITIONS.get(action.status, set()):
        raise CustodyContractError(f"Cannot move action from {action.status} to {to_status}.")
    if to_status in {"review", "reviewed", "submitted"} and not action.authority:
        raise CustodyContractError("Authority is required before review or submission.")
    if to_status in CONFIRMED_RESTRAINT_STATUSES and not verified_outcome:
        raise CustodyContractError("Confirmed action states require verified outcome evidence.")
    if to_status in CONFIRMED_RESTRAINT_STATUSES:
        _validate_verified_outcome(session, action, verified_outcome or {})

    receipts = list(action.receipts or [])
    if receipt:
        receipts.append({"actor": actor, "status": to_status, "ts_ms": now_ms(), **receipt})
    action.receipts = receipts
    if verified_outcome:
        action.verified_outcome = dict(verified_outcome)
    if to_status in {"review", "reviewed"}:
        action.reviewed_by = actor
    action.status = to_status
    action.updated_ts_ms = now_ms()
    session.add(action)
    session.commit()
    session.refresh(action)
    return action


def action_has_confirmed_restraint(action: CustodyAction) -> bool:
    return action.status in CONFIRMED_RESTRAINT_STATUSES and bool(action.verified_outcome)


def _validate_verified_outcome(
    session: Session,
    action: CustodyAction,
    verified_outcome: dict,
) -> None:
    if not any(
        str(verified_outcome.get(key) or "").strip()
        for key in ("provider_ref", "response_ref", "evidence_ref")
    ):
        raise CustodyContractError("Verified outcome requires provider or evidence reference.")
    outcome_amount = verified_outcome.get("amount_base")
    if outcome_amount is None:
        return
    try:
        amount_base = int(outcome_amount)
    except (TypeError, ValueError) as exc:
        raise CustodyContractError("Verified outcome amount must be an integer base-unit value.") from exc
    if amount_base < 0:
        raise CustodyContractError("Verified outcome amount cannot be negative.")
    if action.amount_base is not None and amount_base > action.amount_base:
        raise CustodyContractError("Verified outcome exceeds requested action amount.")
    if action.custody_assertion_id is None:
        return
    assertion = session.get(CustodyAssertion, action.custody_assertion_id)
    if assertion is None:
        raise CustodyContractError("Custody assertion is required for confirmed action outcome.")
    available = (
        assertion.recoverable_amount_base
        if assertion.recoverable_amount_base is not None
        else assertion.current_balance_base
    )
    if available is not None and amount_base > available:
        raise CustodyContractError("Verified outcome exceeds recoverable custody amount.")


def _persist_provider_response_records(
    session: Session,
    *,
    case_id: int,
    snapshot_id: int | None,
    assertion: CustodyAssertion,
    response: CertifiedProviderResponse,
) -> ProviderResponseRecord:
    if assertion.id is None:
        raise CustodyContractError("Custody assertion must be persisted before response records.")
    payload = _certified_response_payload(response)
    response_row = _upsert_provider_response_record(
        session,
        case_id=case_id,
        snapshot_id=snapshot_id,
        assertion=assertion,
        response=response,
        payload=payload,
    )
    _persist_ledger_rows(session, assertion=assertion, response_row=response_row, response=response)
    _persist_kyc_row(session, assertion=assertion, response_row=response_row, response=response)
    _persist_trade_rows(session, assertion=assertion, response_row=response_row, response=response)
    _persist_withdrawal_rows(
        session,
        assertion=assertion,
        response_row=response_row,
        response=response,
    )
    _persist_session_rows(
        session,
        assertion=assertion,
        response_row=response_row,
        response=response,
    )
    return response_row


def _upsert_provider_response_record(
    session: Session,
    *,
    case_id: int,
    snapshot_id: int | None,
    assertion: CustodyAssertion,
    response: CertifiedProviderResponse,
    payload: dict,
) -> ProviderResponseRecord:
    provider_key = response.deposit_assignment.provider_key
    response_sha256 = sha256_json(payload)
    existing = _existing_provider_response(session, provider_key, response.response_ref)
    if existing:
        _validate_provider_response_duplicate(existing, response, response_sha256)
        return existing
    now = now_ms()
    row = ProviderResponseRecord(
        case_id=case_id,
        snapshot_id=snapshot_id,
        custody_assertion_id=assertion.id,
        provider_key=provider_key,
        response_ref=response.response_ref,
        signed_by=response.signed_by,
        received_ts_ms=response.received_ts_ms,
        response_sha256=response_sha256,
        raw_payload=payload,
        created_ts_ms=now,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _existing_provider_response(session, provider_key, response.response_ref)
        if existing:
            _validate_provider_response_duplicate(existing, response, response_sha256)
            return existing
        raise
    session.refresh(row)
    return row


def _persist_ledger_rows(
    session: Session,
    *,
    assertion: CustodyAssertion,
    response_row: ProviderResponseRecord,
    response: CertifiedProviderResponse,
) -> None:
    for entry in response.ledger_entries:
        existing = session.exec(
            select(ProviderLedgerRecord)
            .where(ProviderLedgerRecord.provider_response_id == response_row.id)
            .where(ProviderLedgerRecord.entry_ref == entry.entry_ref)
        ).first()
        if existing:
            _compare_row(
                existing,
                {
                    "custody_assertion_id": assertion.id,
                    "account_reference": entry.account_reference,
                    "asset_identifier": entry.asset_identifier,
                    "amount_base": entry.amount_base,
                    "direction": entry.direction,
                    "occurred_ts_ms": entry.occurred_ts_ms,
                    "chain_credit_ref": entry.chain_credit_ref,
                },
                "Duplicate ledger entry conflicts with existing provider response.",
            )
            continue
        session.add(
            ProviderLedgerRecord(
                provider_response_id=response_row.id,
                custody_assertion_id=assertion.id,
                entry_ref=entry.entry_ref,
                account_reference=entry.account_reference,
                asset_identifier=entry.asset_identifier,
                amount_base=entry.amount_base,
                direction=entry.direction,
                occurred_ts_ms=entry.occurred_ts_ms,
                chain_credit_ref=entry.chain_credit_ref,
                created_ts_ms=now_ms(),
            )
        )
    session.commit()


def _persist_kyc_row(
    session: Session,
    *,
    assertion: CustodyAssertion,
    response_row: ProviderResponseRecord,
    response: CertifiedProviderResponse,
) -> None:
    if response.kyc is None:
        return
    kyc = response.kyc
    existing = session.exec(
        select(ProviderKycRecordRow)
        .where(ProviderKycRecordRow.provider_response_id == response_row.id)
        .where(ProviderKycRecordRow.account_reference == kyc.account_reference)
    ).first()
    expected = {
        "custody_assertion_id": assertion.id,
        "beneficiary_ref": kyc.beneficiary_ref,
        "verification_status": kyc.verification_status,
        "fields": dict(kyc.fields),
    }
    if existing:
        _compare_row(existing, expected, "Duplicate KYC record conflicts with existing response.")
        return
    session.add(
        ProviderKycRecordRow(
            provider_response_id=response_row.id,
            custody_assertion_id=assertion.id,
            account_reference=kyc.account_reference,
            beneficiary_ref=kyc.beneficiary_ref,
            verification_status=kyc.verification_status,
            fields=dict(kyc.fields),
            created_ts_ms=now_ms(),
        )
    )
    session.commit()


def _persist_trade_rows(
    session: Session,
    *,
    assertion: CustodyAssertion,
    response_row: ProviderResponseRecord,
    response: CertifiedProviderResponse,
) -> None:
    for trade in response.trades or []:
        existing = session.exec(
            select(ProviderTradeRecordRow)
            .where(ProviderTradeRecordRow.provider_response_id == response_row.id)
            .where(ProviderTradeRecordRow.trade_ref == trade.trade_ref)
        ).first()
        expected = {
            "custody_assertion_id": assertion.id,
            "account_reference": trade.account_reference,
            "base_asset": trade.base_asset,
            "quote_asset": trade.quote_asset,
            "base_amount": trade.base_amount,
            "quote_amount": trade.quote_amount,
            "executed_ts_ms": trade.executed_ts_ms,
        }
        if existing:
            _compare_row(
                existing,
                expected,
                "Duplicate trade record conflicts with existing response.",
            )
            continue
        session.add(
            ProviderTradeRecordRow(
                provider_response_id=response_row.id,
                custody_assertion_id=assertion.id,
                trade_ref=trade.trade_ref,
                account_reference=trade.account_reference,
                base_asset=trade.base_asset,
                quote_asset=trade.quote_asset,
                base_amount=trade.base_amount,
                quote_amount=trade.quote_amount,
                executed_ts_ms=trade.executed_ts_ms,
                created_ts_ms=now_ms(),
            )
        )
    session.commit()


def _persist_withdrawal_rows(
    session: Session,
    *,
    assertion: CustodyAssertion,
    response_row: ProviderResponseRecord,
    response: CertifiedProviderResponse,
) -> None:
    for withdrawal in response.withdrawals or []:
        existing = session.exec(
            select(ProviderWithdrawalRecordRow)
            .where(ProviderWithdrawalRecordRow.provider_response_id == response_row.id)
            .where(ProviderWithdrawalRecordRow.withdrawal_ref == withdrawal.withdrawal_ref)
        ).first()
        expected = {
            "custody_assertion_id": assertion.id,
            "account_reference": withdrawal.account_reference,
            "asset_identifier": withdrawal.asset_identifier,
            "amount_base": withdrawal.amount_base,
            "destination": withdrawal.destination,
            "requested_ts_ms": withdrawal.requested_ts_ms,
            "txid": withdrawal.txid,
        }
        if existing:
            _compare_row(
                existing,
                expected,
                "Duplicate withdrawal record conflicts with existing response.",
            )
            continue
        session.add(
            ProviderWithdrawalRecordRow(
                provider_response_id=response_row.id,
                custody_assertion_id=assertion.id,
                withdrawal_ref=withdrawal.withdrawal_ref,
                account_reference=withdrawal.account_reference,
                asset_identifier=withdrawal.asset_identifier,
                amount_base=withdrawal.amount_base,
                destination=withdrawal.destination,
                requested_ts_ms=withdrawal.requested_ts_ms,
                txid=withdrawal.txid,
                created_ts_ms=now_ms(),
            )
        )
    session.commit()


def _persist_session_rows(
    session: Session,
    *,
    assertion: CustodyAssertion,
    response_row: ProviderResponseRecord,
    response: CertifiedProviderResponse,
) -> None:
    for provider_session in response.sessions or []:
        existing = session.exec(
            select(ProviderSessionRecordRow)
            .where(ProviderSessionRecordRow.provider_response_id == response_row.id)
            .where(ProviderSessionRecordRow.session_ref == provider_session.session_ref)
        ).first()
        expected = {
            "custody_assertion_id": assertion.id,
            "account_reference": provider_session.account_reference,
            "started_ts_ms": provider_session.started_ts_ms,
            "ip_country": provider_session.ip_country,
            "device_ref": provider_session.device_ref,
        }
        if existing:
            _compare_row(
                existing,
                expected,
                "Duplicate session record conflicts with existing response.",
            )
            continue
        session.add(
            ProviderSessionRecordRow(
                provider_response_id=response_row.id,
                custody_assertion_id=assertion.id,
                session_ref=provider_session.session_ref,
                account_reference=provider_session.account_reference,
                started_ts_ms=provider_session.started_ts_ms,
                ip_country=provider_session.ip_country,
                device_ref=provider_session.device_ref,
                created_ts_ms=now_ms(),
            )
        )
    session.commit()


def _existing_provider_response(
    session: Session,
    provider_key: str,
    response_ref: str,
) -> ProviderResponseRecord | None:
    return session.exec(
        select(ProviderResponseRecord)
        .where(ProviderResponseRecord.provider_key == provider_key)
        .where(ProviderResponseRecord.response_ref == response_ref)
    ).first()


def _validate_provider_response_duplicate(
    existing: ProviderResponseRecord,
    response: CertifiedProviderResponse,
    response_sha256: str,
) -> None:
    expected = {
        "signed_by": response.signed_by,
        "received_ts_ms": response.received_ts_ms,
        "response_sha256": response_sha256,
    }
    _compare_row(existing, expected, "Duplicate certified response conflicts with existing row.")


def _certified_response_payload(response: CertifiedProviderResponse) -> dict:
    return {
        "response_ref": response.response_ref,
        "signed_by": response.signed_by,
        "received_ts_ms": response.received_ts_ms,
        "deposit_assignment": asdict(response.deposit_assignment),
        "ledger_entries": [asdict(entry) for entry in response.ledger_entries],
        "kyc": asdict(response.kyc) if response.kyc else None,
        "trades": [asdict(trade) for trade in response.trades or []],
        "withdrawals": [asdict(withdrawal) for withdrawal in response.withdrawals or []],
        "sessions": [asdict(session_row) for session_row in response.sessions or []],
    }


def _compare_row(row: object, expected: dict[str, object], message: str) -> None:
    for field_name, value in expected.items():
        if getattr(row, field_name) != value:
            raise CustodyContractError(message)


def _validate_custody_import(payload: CustodyImport) -> None:
    required = {
        "provider_key": payload.provider_key,
        "chain_family": payload.chain_family,
        "chain_network": payload.chain_network,
        "deposit_address": payload.deposit_address,
        "exact_credit_ref": payload.exact_credit_ref,
        "service_role": payload.service_role,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise CustodyContractError(f"Missing custody import field: {missing[0]}")
    if payload.current_balance_base is not None and payload.current_balance_base < 0:
        raise CustodyContractError("Current balance cannot be negative.")
    if payload.recoverable_amount_base is not None and payload.recoverable_amount_base < 0:
        raise CustodyContractError("Recoverable amount cannot be negative.")
    if (
        payload.current_balance_base is not None
        and payload.recoverable_amount_base is not None
        and payload.recoverable_amount_base > payload.current_balance_base
    ):
        raise CustodyContractError("Recoverable amount cannot exceed current balance.")
    if (
        payload.valid_from_ms
        and payload.valid_to_ms
        and payload.valid_to_ms < payload.valid_from_ms
    ):
        raise CustodyContractError("Custody assertion validity interval is inverted.")
    provenance = dict(payload.provenance or {})
    hot_wallet = provenance.get("hot_wallet")
    if hot_wallet and str(hot_wallet).lower() == payload.deposit_address.lower():
        raise CustodyContractError("Deposit address and hot wallet must remain separate objects.")
    credit_ts_ms = provenance.get("credit_ts_ms")
    if credit_ts_ms is not None:
        if payload.valid_from_ms is not None and credit_ts_ms < payload.valid_from_ms:
            raise CustodyContractError("Custody label was not valid at the credit timestamp.")
        if payload.valid_to_ms is not None and credit_ts_ms > payload.valid_to_ms:
            raise CustodyContractError("Custody label was expired at the credit timestamp.")
    elif payload.valid_to_ms is not None and payload.valid_to_ms < now_ms():
        raise CustodyContractError("Custody label is expired.")


def _validate_duplicate_assertion(existing: CustodyAssertion, payload: CustodyImport) -> None:
    comparisons = {
        "chain_family": payload.chain_family,
        "chain_network": payload.chain_network,
        "deposit_address": payload.deposit_address,
        "account_reference": payload.account_reference,
        "service_role": payload.service_role,
        "valid_from_ms": payload.valid_from_ms,
        "valid_to_ms": payload.valid_to_ms,
        "dispute_status": payload.dispute_status,
        "current_balance_base": payload.current_balance_base,
        "recoverable_amount_base": payload.recoverable_amount_base,
    }
    for field_name, value in comparisons.items():
        if getattr(existing, field_name) != value:
            raise CustodyContractError(
                "Duplicate provider response conflicts with existing assertion."
            )


def _existing_assertion(session: Session, payload: CustodyImport) -> CustodyAssertion | None:
    return session.exec(
        select(CustodyAssertion)
        .where(CustodyAssertion.provider_key == payload.provider_key)
        .where(CustodyAssertion.exact_credit_ref == payload.exact_credit_ref)
    ).first()


def _validate_action_against_assertion(
    session: Session,
    *,
    custody_assertion_id: int | None,
    amount_base: int | None,
) -> None:
    if custody_assertion_id is None or amount_base is None:
        return
    assertion = session.get(CustodyAssertion, custody_assertion_id)
    if assertion is None:
        raise CustodyContractError("Custody assertion is required for this action.")
    available = (
        assertion.recoverable_amount_base
        if assertion.recoverable_amount_base is not None
        else assertion.current_balance_base
    )
    if available is not None and amount_base > available:
        raise CustodyContractError("Action amount exceeds recoverable custody amount.")


def _validate_certified_response(response: CertifiedProviderResponse) -> None:
    required = {
        "response_ref": response.response_ref,
        "signed_by": response.signed_by,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise CustodyContractError(f"Missing certified provider response field: {missing[0]}")
    if response.received_ts_ms <= 0:
        raise CustodyContractError("Certified provider response timestamp must be positive.")
    if not response.ledger_entries:
        raise CustodyContractError("Certified provider response requires ledger entries.")
    _validate_custody_import(response.deposit_assignment)
    account_reference = response.deposit_assignment.account_reference
    matching_credit = False
    for entry in response.ledger_entries:
        _validate_ledger_entry(entry)
        if entry.account_reference != account_reference:
            raise CustodyContractError("Ledger entry account does not match deposit assignment.")
        if entry.chain_credit_ref == response.deposit_assignment.exact_credit_ref:
            matching_credit = True
    if not matching_credit:
        raise CustodyContractError("Ledger entries do not include the exact chain credit.")
    if response.kyc:
        _validate_kyc(response.kyc, account_reference)
    for trade in response.trades or []:
        _validate_trade(trade, account_reference)
    for withdrawal in response.withdrawals or []:
        _validate_withdrawal(withdrawal, account_reference)
    for session_row in response.sessions or []:
        _validate_session(session_row, account_reference)


def _validate_ledger_entry(entry: ProviderLedgerEntry) -> None:
    required = {
        "entry_ref": entry.entry_ref,
        "account_reference": entry.account_reference,
        "asset_identifier": entry.asset_identifier,
        "direction": entry.direction,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise CustodyContractError(f"Missing ledger field: {missing[0]}")
    if entry.amount_base < 0 or entry.occurred_ts_ms <= 0:
        raise CustodyContractError("Ledger amounts must be non-negative and timestamped.")


def _validate_kyc(kyc: ProviderKycRecord, account_reference: str | None) -> None:
    if kyc.account_reference != account_reference:
        raise CustodyContractError("KYC account does not match deposit assignment.")
    if not kyc.beneficiary_ref.strip() or not kyc.verification_status.strip():
        raise CustodyContractError("KYC beneficiary and verification fields are required.")


def _validate_trade(trade: ProviderTradeRecord, account_reference: str | None) -> None:
    if trade.account_reference != account_reference:
        raise CustodyContractError("Trade account does not match deposit assignment.")
    if trade.base_amount < 0 or trade.quote_amount < 0 or trade.executed_ts_ms <= 0:
        raise CustodyContractError("Trade records require non-negative amounts and timestamps.")


def _validate_withdrawal(
    withdrawal: ProviderWithdrawalRecord,
    account_reference: str | None,
) -> None:
    if withdrawal.account_reference != account_reference:
        raise CustodyContractError("Withdrawal account does not match deposit assignment.")
    if withdrawal.amount_base < 0 or withdrawal.requested_ts_ms <= 0:
        raise CustodyContractError(
            "Withdrawal records require non-negative amounts and timestamps."
        )
    if not withdrawal.destination.strip():
        raise CustodyContractError("Withdrawal destination is required.")


def _validate_session(session_row: ProviderSessionRecord, account_reference: str | None) -> None:
    if session_row.account_reference != account_reference:
        raise CustodyContractError("Session account does not match deposit assignment.")
    if not session_row.session_ref.strip() or session_row.started_ts_ms <= 0:
        raise CustodyContractError("Session records require a reference and timestamp.")
