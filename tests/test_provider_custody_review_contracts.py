from __future__ import annotations

from collections.abc import Iterator

import pytest
import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    Case,
    CustodyAssertion,
    ProviderKycRecordRow,
    ProviderLedgerRecord,
    ProviderResponseRecord,
    ProviderSessionRecordRow,
    ProviderTradeRecordRow,
    ProviderWithdrawalRecordRow,
)
from app.services.custody import (
    CertifiedProviderResponse,
    CustodyContractError,
    CustodyImport,
    ProviderKycRecord,
    ProviderLedgerEntry,
    ProviderSessionRecord,
    ProviderTradeRecord,
    ProviderWithdrawalRecord,
    action_has_confirmed_restraint,
    advance_action_status,
    create_action_draft,
    import_certified_provider_response,
    import_custody_assertion,
)
from app.services.review import (
    EvidenceClaim,
    EvidenceIndicator,
    exposure_only,
    participation_unknown,
    privacy_boundary,
    review_evidence_claims,
    service_role_indicator,
    validate_claim_references,
)
from app.services.time import now_ms
from engine.adapters.tron import (
    ProviderConfigurationError,
    ProviderResponseError,
    TronGridConfig,
    fetch_trc20_transfers,
    normalise,
    resolve_usdt_transfer,
    verify_seed_transfer,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, headers: dict, timeout: float) -> FakeResponse:
        self.calls.append(
            {"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout}
        )
        if not self.responses:
            raise AssertionError("Unexpected extra HTTP call")
        return self.responses.pop(0)


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
        ack_no="NCRP/2026/TEST/CONTRACT",
        category="investment fraud",
        jurisdiction="Test Cyber Cell",
        filed_ts_ms=ts,
        amount_reported_base=1_000,
        asset_symbol="USDT",
        asset_decimals=6,
        chain_family="TRON",
        chain_network="mainnet",
        reported_address="TNq7CqVEANFoJjvekpNLtTYuTnTtA6x7Ti",
        payment_txid="a" * 64,
        payment_ts_ms=ts,
        created_ts_ms=ts,
        updated_ts_ms=ts,
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def test_trongrid_pagination_uses_fingerprint_and_api_key() -> None:
    fake = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "transaction_id": "a",
                            "block_timestamp": 1,
                            "from": "A",
                            "to": "B",
                            "value": "5",
                        }
                    ],
                    "meta": {"fingerprint": "next-page"},
                },
            ),
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "transaction_id": "b",
                            "block_timestamp": 2,
                            "from": "B",
                            "to": "C",
                            "value": "4",
                        }
                    ],
                    "meta": {},
                },
            ),
        ]
    )
    config = TronGridConfig(
        base_url="https://api.trongrid.io",
        api_key="key",
        page_limit=100,
        max_pages=3,
    )

    rows = fetch_trc20_transfers(
        "TAddress",
        min_timestamp=10,
        max_timestamp=20,
        contract_address="TR7",
        session=fake,
        config=config,
    )

    assert [row["transaction_id"] for row in rows] == ["a", "b"]
    assert fake.calls[0]["url"].endswith("/v1/accounts/TAddress/transactions/trc20")
    assert fake.calls[0]["headers"]["TRON-PRO-API-KEY"] == "key"
    assert fake.calls[1]["params"]["fingerprint"] == "next-page"


def test_trongrid_rejects_repeated_pagination_cursor() -> None:
    fake = FakeSession(
        [
            FakeResponse(200, {"success": True, "data": [], "meta": {"fingerprint": "loop"}}),
            FakeResponse(200, {"success": True, "data": [], "meta": {"fingerprint": "loop"}}),
        ]
    )
    config = TronGridConfig(base_url="https://api.trongrid.io", api_key="key", max_pages=3)

    with pytest.raises(ProviderResponseError):
        fetch_trc20_transfers("TAddress", session=fake, config=config)


def test_trongrid_requires_key_for_default_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRONGRID_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError):
        fetch_trc20_transfers("TAddress")


def test_trongrid_socket_failure_is_provider_response_error() -> None:
    class FailingSession:
        def get(self, *_args, **_kwargs):
            raise requests.ConnectionError("socket blocked")

    config = TronGridConfig(base_url="https://api.trongrid.io", api_key="key")

    with pytest.raises(ProviderResponseError, match="request failed"):
        fetch_trc20_transfers("TAddress", session=FailingSession(), config=config)


def test_seed_transfer_verifies_exact_recipient_amount_and_contract() -> None:
    fake = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "event_name": "Transfer",
                            "contract_address": "TR7",
                            "result": {"to": "TRecipient", "value": "100"},
                        }
                    ],
                },
            )
        ]
    )
    event = verify_seed_transfer(
        txid="abc",
        recipient="TRecipient",
        amount_base=100,
        contract_address="TR7",
        session=fake,
        config=TronGridConfig(base_url="https://api.trongrid.io", api_key="key"),
    )

    assert event["event_name"] == "Transfer"


def test_resolve_usdt_transfer_from_confirmed_event_normalizes_hex_addresses() -> None:
    fake = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "event_name": "Transfer",
                            "contract_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                            "block_number": 84308492,
                            "block_timestamp": 1783595430000,
                            "event_index": 0,
                            "transaction_id": "b3" * 32,
                            "result": {
                                "from": "0x579e933f76f64d5383f3856b1ead4903c7d08541",
                                "to": "0x96e74e1cd5edecfc7f90372b024e092d2b2eb644",
                                "value": "149900000",
                            },
                        }
                    ],
                },
            )
        ]
    )

    transfer = resolve_usdt_transfer(
        "b3" * 32,
        session=fake,
        config=TronGridConfig(base_url="https://api.trongrid.io", api_key="key"),
    )

    assert transfer["source"] == "THxVkkBoYUdYCZgtqgeRZHReUhShRBUzwM"
    assert transfer["destination"] == "TPj7TCJ9rxdd243yQ3tc7iJzqcEYtupB4v"
    assert transfer["amount_base"] == 149_900_000
    assert transfer["ts_ms"] == 1_783_595_430_000
    assert transfer["block"] == 84_308_492
    assert transfer["event_index"] == 0


def test_resolve_usdt_transfer_rejects_non_usdt_transaction() -> None:
    fake = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "event_name": "Transfer",
                            "contract_address": "TNotUsdtContract",
                            "block_timestamp": 1783595430000,
                            "result": {
                                "from": "TSource",
                                "to": "TDestination",
                                "value": "149900000",
                            },
                        }
                    ],
                },
            )
        ]
    )

    with pytest.raises(ProviderResponseError, match="No confirmed TRON USDT"):
        resolve_usdt_transfer(
            "b3" * 32,
            session=fake,
            config=TronGridConfig(base_url="https://api.trongrid.io", api_key="key"),
        )


def test_tron_normalise_requires_core_fields() -> None:
    row = normalise(
        {
            "transaction_id": "tx",
            "block_timestamp": 123,
            "from": "A",
            "to": "B",
            "value": "42",
            "token_info": {"symbol": "USDT", "decimals": 6, "address": "TR7"},
        }
    )
    assert row["amount_base"] == 42
    assert row["contract"] == "TR7"
    with pytest.raises(ProviderResponseError):
        normalise({"transaction_id": "missing-fields"})


def test_tron_normalise_preserves_explicit_zero_values() -> None:
    row = normalise(
        {
            "transaction_id": "tx-zero",
            "transaction_hash": "fallback-tx",
            "block_timestamp": 0,
            "block_ts": 123,
            "from": "A",
            "to": "B",
            "value": 0,
            "amount": "42",
            "event_index": 0,
            "log_index": 7,
            "token_info": {"symbol": "USDT", "decimals": 6, "address": "TR7"},
        }
    )

    assert row["txid"] == "tx-zero"
    assert row["ts_ms"] == 0
    assert row["amount_base"] == 0
    assert row["event_index"] == 0


def test_custody_import_separates_credit_balance_and_recoverable_amount(db: Session) -> None:
    case = make_case(db)
    assertion = import_custody_assertion(
        db,
        case_id=case.id,
        snapshot_id=None,
        payload=CustodyImport(
            provider_key="coinsphere",
            chain_family="TRON",
            chain_network="mainnet",
            deposit_address="TDeposit",
            exact_credit_ref="tx:event:0",
            account_reference="acct-123",
            service_role="exchange_deposit_account",
            current_balance_base=700,
            recoverable_amount_base=500,
            provenance={"source": "fixture-provider-record"},
        ),
    )

    assert assertion.account_reference == "acct-123"
    assert assertion.exact_credit_ref == "tx:event:0"
    assert assertion.current_balance_base == 700
    assert assertion.recoverable_amount_base == 500
    assert assertion.provenance["schema"] == "trinetra.custody_import/1"


def test_custody_import_rejects_recoverable_amount_above_balance(db: Session) -> None:
    case = make_case(db)
    with pytest.raises(CustodyContractError):
        import_custody_assertion(
            db,
            case_id=case.id,
            snapshot_id=None,
            payload=CustodyImport(
                provider_key="coinsphere",
                chain_family="TRON",
                chain_network="mainnet",
                deposit_address="TDeposit",
                exact_credit_ref="tx:event:0",
                service_role="exchange_deposit_account",
                current_balance_base=10,
                recoverable_amount_base=11,
            ),
        )


def test_certified_provider_response_imports_ledger_kyc_trades_withdrawals_and_sessions(
    db: Session,
) -> None:
    case = make_case(db)
    response = CertifiedProviderResponse(
        response_ref="cert-1",
        signed_by="coinsphere-compliance",
        received_ts_ms=now_ms(),
        deposit_assignment=CustodyImport(
            provider_key="coinsphere",
            chain_family="TRON",
            chain_network="mainnet",
            deposit_address="TDeposit",
            exact_credit_ref="tx:event:0",
            account_reference="acct-123",
            service_role="exchange_deposit_account",
            current_balance_base=700,
            recoverable_amount_base=500,
            provenance={"credit_ts_ms": now_ms(), "hot_wallet": "THotWallet"},
        ),
        ledger_entries=[
            ProviderLedgerEntry(
                entry_ref="ledger-credit-1",
                account_reference="acct-123",
                asset_identifier="TRON:USDT",
                amount_base=700,
                direction="credit",
                occurred_ts_ms=now_ms(),
                chain_credit_ref="tx:event:0",
            )
        ],
        kyc=ProviderKycRecord(
            account_reference="acct-123",
            beneficiary_ref="beneficiary-1",
            verification_status="verified",
            fields={"name_ref": "sealed-fixture"},
        ),
        trades=[
            ProviderTradeRecord(
                trade_ref="trade-1",
                account_reference="acct-123",
                base_asset="TRON:USDT",
                quote_asset="INR",
                base_amount=100,
                quote_amount=8_800,
                executed_ts_ms=now_ms(),
            )
        ],
        withdrawals=[
            ProviderWithdrawalRecord(
                withdrawal_ref="withdrawal-1",
                account_reference="acct-123",
                asset_identifier="TRON:USDT",
                amount_base=100,
                destination="TExternal",
                requested_ts_ms=now_ms(),
                txid="b" * 64,
            )
        ],
        sessions=[
            ProviderSessionRecord(
                session_ref="session-1",
                account_reference="acct-123",
                started_ts_ms=now_ms(),
                ip_country="IN",
            )
        ],
    )

    assertion = import_certified_provider_response(
        db,
        case_id=case.id,
        snapshot_id=None,
        response=response,
    )

    assert assertion.account_reference == "acct-123"
    assert assertion.provenance["certified_response"]["response_ref"] == "cert-1"
    assert assertion.provenance["ledger_refs"] == ["ledger-credit-1"]
    assert assertion.provenance["kyc"]["beneficiary_ref"] == "beneficiary-1"
    assert assertion.provenance["withdrawal_refs"] == ["withdrawal-1"]
    assert assertion.provenance["session_refs"] == ["session-1"]
    response_row = db.exec(select(ProviderResponseRecord)).one()
    assert response_row.response_ref == "cert-1"
    assert response_row.custody_assertion_id == assertion.id
    assert response_row.raw_payload["deposit_assignment"]["deposit_address"] == "TDeposit"
    ledger = db.exec(select(ProviderLedgerRecord)).one()
    assert ledger.entry_ref == "ledger-credit-1"
    assert ledger.chain_credit_ref == "tx:event:0"
    assert db.exec(select(ProviderKycRecordRow)).one().beneficiary_ref == "beneficiary-1"
    assert db.exec(select(ProviderTradeRecordRow)).one().trade_ref == "trade-1"
    assert db.exec(select(ProviderWithdrawalRecordRow)).one().withdrawal_ref == "withdrawal-1"
    assert db.exec(select(ProviderSessionRecordRow)).one().session_ref == "session-1"

    repeated = import_certified_provider_response(
        db,
        case_id=case.id,
        snapshot_id=None,
        response=response,
    )
    assert repeated.id == assertion.id
    assert len(db.exec(select(ProviderResponseRecord)).all()) == 1
    assert len(db.exec(select(ProviderLedgerRecord)).all()) == 1


def test_duplicate_provider_response_is_idempotent_but_conflicts_are_rejected(db: Session) -> None:
    case = make_case(db)
    payload = CustodyImport(
        provider_key="coinsphere",
        chain_family="TRON",
        chain_network="mainnet",
        deposit_address="TDeposit",
        exact_credit_ref="tx:event:0",
        account_reference="acct-123",
        service_role="exchange_deposit_account",
        current_balance_base=700,
        recoverable_amount_base=500,
    )

    first = import_custody_assertion(db, case_id=case.id, snapshot_id=None, payload=payload)
    second = import_custody_assertion(db, case_id=case.id, snapshot_id=None, payload=payload)

    assert first.id == second.id

    with pytest.raises(CustodyContractError, match="Duplicate provider response"):
        import_custody_assertion(
            db,
            case_id=case.id,
            snapshot_id=None,
            payload=CustodyImport(
                provider_key="coinsphere",
                chain_family="TRON",
                chain_network="mainnet",
                deposit_address="TDifferent",
                exact_credit_ref="tx:event:0",
                account_reference="acct-123",
                service_role="exchange_deposit_account",
                current_balance_base=700,
                recoverable_amount_base=500,
            ),
        )

    with pytest.raises(CustodyContractError, match="Duplicate provider response"):
        import_custody_assertion(
            db,
            case_id=case.id,
            snapshot_id=None,
            payload=CustodyImport(
                provider_key="coinsphere",
                chain_family="TRON",
                chain_network="nile",
                deposit_address="TDeposit",
                exact_credit_ref="tx:event:0",
                account_reference="acct-123",
                service_role="exchange_deposit_account",
                current_balance_base=700,
                recoverable_amount_base=500,
            ),
        )


def test_custody_assertion_database_rejects_duplicate_provider_credit(db: Session) -> None:
    case = make_case(db)
    assertion = CustodyAssertion(
        case_id=case.id,
        snapshot_id=None,
        provider_key="coinsphere",
        chain_family="TRON",
        chain_network="mainnet",
        deposit_address="TDeposit",
        exact_credit_ref="tx:event:0",
        account_reference="acct-123",
        service_role="exchange_deposit_account",
        current_balance_base=100,
        recoverable_amount_base=100,
        created_ts_ms=now_ms(),
    )
    duplicate = CustodyAssertion(
        case_id=case.id,
        snapshot_id=None,
        provider_key="coinsphere",
        chain_family="TRON",
        chain_network="mainnet",
        deposit_address="TDeposit",
        exact_credit_ref="tx:event:0",
        account_reference="acct-123",
        service_role="exchange_deposit_account",
        current_balance_base=100,
        recoverable_amount_base=100,
        created_ts_ms=now_ms(),
    )

    db.add(assertion)
    db.commit()
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_reused_deposit_address_keeps_exact_credits_separate(db: Session) -> None:
    case = make_case(db)
    first = import_custody_assertion(
        db,
        case_id=case.id,
        snapshot_id=None,
        payload=CustodyImport(
            provider_key="coinsphere",
            chain_family="TRON",
            chain_network="mainnet",
            deposit_address="TDeposit",
            exact_credit_ref="tx:event:0",
            account_reference="acct-123",
            service_role="exchange_deposit_account",
            current_balance_base=700,
            recoverable_amount_base=500,
        ),
    )
    second = import_custody_assertion(
        db,
        case_id=case.id,
        snapshot_id=None,
        payload=CustodyImport(
            provider_key="coinsphere",
            chain_family="TRON",
            chain_network="mainnet",
            deposit_address="TDeposit",
            exact_credit_ref="tx:event:1",
            account_reference="acct-123",
            service_role="exchange_deposit_account",
            current_balance_base=900,
            recoverable_amount_base=700,
        ),
    )

    assert first.id != second.id
    assert first.deposit_address == second.deposit_address
    assert first.exact_credit_ref != second.exact_credit_ref


def test_shared_hot_wallet_keeps_distinct_deposit_assignments_separate(db: Session) -> None:
    case = make_case(db)
    first = import_custody_assertion(
        db,
        case_id=case.id,
        snapshot_id=None,
        payload=CustodyImport(
            provider_key="coinsphere",
            chain_family="TRON",
            chain_network="mainnet",
            deposit_address="TDepositOne",
            exact_credit_ref="tx:event:0",
            account_reference="acct-123",
            service_role="exchange_deposit_account",
            current_balance_base=700,
            recoverable_amount_base=500,
            provenance={"hot_wallet": "TSharedHotWallet"},
        ),
    )
    second = import_custody_assertion(
        db,
        case_id=case.id,
        snapshot_id=None,
        payload=CustodyImport(
            provider_key="coinsphere",
            chain_family="TRON",
            chain_network="mainnet",
            deposit_address="TDepositTwo",
            exact_credit_ref="tx:event:1",
            account_reference="acct-456",
            service_role="exchange_deposit_account",
            current_balance_base=900,
            recoverable_amount_base=700,
            provenance={"hot_wallet": "TSharedHotWallet"},
        ),
    )

    assert first.id != second.id
    assert first.deposit_address != second.deposit_address
    assert first.provenance["hot_wallet"] == second.provenance["hot_wallet"]


def test_deposit_address_cannot_be_collapsed_with_hot_wallet_or_expired_label(
    db: Session,
) -> None:
    case = make_case(db)
    with pytest.raises(CustodyContractError, match="separate objects"):
        import_custody_assertion(
            db,
            case_id=case.id,
            snapshot_id=None,
            payload=CustodyImport(
                provider_key="coinsphere",
                chain_family="TRON",
                chain_network="mainnet",
                deposit_address="TSame",
                exact_credit_ref="tx:event:0",
                service_role="exchange_deposit_account",
                provenance={"hot_wallet": "TSame"},
            ),
        )

    with pytest.raises(CustodyContractError, match="expired"):
        import_custody_assertion(
            db,
            case_id=case.id,
            snapshot_id=None,
            payload=CustodyImport(
                provider_key="coinsphere",
                chain_family="TRON",
                chain_network="mainnet",
                deposit_address="TDeposit",
                exact_credit_ref="tx:event:0",
                service_role="exchange_deposit_account",
                valid_to_ms=1,
            ),
        )


def test_action_submission_and_dispatch_do_not_imply_confirmed_restraint(db: Session) -> None:
    case = make_case(db)
    action = create_action_draft(
        db,
        case_id=case.id,
        custody_assertion_id=None,
        target_kind="provider_account",
        target_ref="acct-123",
        requested_by="74821",
        amount_base=500,
        asset_identifier="TRON:USDT",
        jurisdiction="IN",
        authority="BNSS-94-test-authority",
    )
    action = advance_action_status(db, action, to_status="reviewed", actor="61207")
    action = advance_action_status(
        db,
        action,
        to_status="submitted",
        actor="74821",
        receipt={"channel": "fixture"},
    )
    action = advance_action_status(
        db,
        action,
        to_status="acknowledged",
        actor="provider",
        receipt={"ref": "ack"},
    )

    assert action.status == "acknowledged"
    assert action_has_confirmed_restraint(action) is False

    with pytest.raises(CustodyContractError):
        advance_action_status(db, action, to_status="confirmed_hold", actor="provider")

    action = advance_action_status(
        db,
        action,
        to_status="confirmed_hold",
        actor="provider",
        verified_outcome={"provider_ref": "hold-1", "amount_base": 500},
    )
    assert action_has_confirmed_restraint(action) is True


def test_action_workflow_accepts_plan_named_review_confirmed_and_released_states(
    db: Session,
) -> None:
    case = make_case(db)
    action = create_action_draft(
        db,
        case_id=case.id,
        custody_assertion_id=None,
        target_kind="provider_account",
        target_ref="acct-123",
        requested_by="74821",
        amount_base=500,
        asset_identifier="TRON:USDT",
        jurisdiction="IN",
        authority="BNSS-94-test-authority",
    )

    action = advance_action_status(db, action, to_status="review", actor="61207")
    action = advance_action_status(db, action, to_status="submitted", actor="74821")
    action = advance_action_status(db, action, to_status="acknowledged", actor="provider")

    assert action_has_confirmed_restraint(action) is False

    action = advance_action_status(
        db,
        action,
        to_status="confirmed",
        actor="provider",
        verified_outcome={"provider_ref": "hold-2", "amount_base": 500},
    )
    assert action_has_confirmed_restraint(action) is True

    action = advance_action_status(db, action, to_status="released", actor="provider")
    assert action.status == "released"
    assert action_has_confirmed_restraint(action) is False


def test_action_request_cannot_exceed_depleted_recoverable_amount(db: Session) -> None:
    case = make_case(db)
    assertion = import_custody_assertion(
        db,
        case_id=case.id,
        snapshot_id=None,
        payload=CustodyImport(
            provider_key="coinsphere",
            chain_family="TRON",
            chain_network="mainnet",
            deposit_address="TDeposit",
            exact_credit_ref="tx:event:0",
            account_reference="acct-123",
            service_role="exchange_deposit_account",
            current_balance_base=0,
            recoverable_amount_base=0,
        ),
    )

    with pytest.raises(CustodyContractError, match="exceeds recoverable"):
        create_action_draft(
            db,
            case_id=case.id,
            custody_assertion_id=assertion.id,
            target_kind="provider_account",
            target_ref="acct-123",
            requested_by="74821",
            amount_base=1,
            asset_identifier="TRON:USDT",
            jurisdiction="IN",
            authority="BNSS-94-test-authority",
        )


def test_confirmed_action_outcome_requires_reference_and_amount_bounds(db: Session) -> None:
    case = make_case(db)
    assertion = import_custody_assertion(
        db,
        case_id=case.id,
        snapshot_id=None,
        payload=CustodyImport(
            provider_key="coinsphere",
            chain_family="TRON",
            chain_network="mainnet",
            deposit_address="TDeposit",
            exact_credit_ref="tx:event:0",
            account_reference="acct-123",
            service_role="exchange_deposit_account",
            current_balance_base=400,
            recoverable_amount_base=300,
        ),
    )
    action = create_action_draft(
        db,
        case_id=case.id,
        custody_assertion_id=assertion.id,
        target_kind="provider_account",
        target_ref="acct-123",
        requested_by="74821",
        amount_base=250,
        asset_identifier="TRON:USDT",
        jurisdiction="IN",
        authority="BNSS-94-test-authority",
    )
    action = advance_action_status(db, action, to_status="review", actor="61207")
    action = advance_action_status(db, action, to_status="submitted", actor="74821")
    action = advance_action_status(db, action, to_status="acknowledged", actor="provider")

    with pytest.raises(CustodyContractError, match="provider or evidence reference"):
        advance_action_status(
            db,
            action,
            to_status="confirmed",
            actor="provider",
            verified_outcome={"amount_base": 200},
        )

    with pytest.raises(CustodyContractError, match="requested action amount"):
        advance_action_status(
            db,
            action,
            to_status="confirmed",
            actor="provider",
            verified_outcome={"provider_ref": "hold-too-large", "amount_base": 251},
        )

    action.amount_base = 350
    db.add(action)
    db.commit()
    db.refresh(action)
    with pytest.raises(CustodyContractError, match="recoverable custody amount"):
        advance_action_status(
            db,
            action,
            to_status="confirmed",
            actor="provider",
            verified_outcome={"provider_ref": "hold-unrecoverable", "amount_base": 301},
        )

    action.amount_base = 250
    db.add(action)
    db.commit()
    db.refresh(action)
    action = advance_action_status(
        db,
        action,
        to_status="confirmed",
        actor="provider",
        verified_outcome={"provider_ref": "hold-ok", "amount_base": 250},
    )
    assert action_has_confirmed_restraint(action) is True


def test_action_requires_authority_before_review(db: Session) -> None:
    case = make_case(db)
    action = create_action_draft(
        db,
        case_id=case.id,
        custody_assertion_id=None,
        target_kind="provider_account",
        target_ref="acct-123",
        requested_by="74821",
    )
    with pytest.raises(CustodyContractError):
        advance_action_status(db, action, to_status="reviewed", actor="61207")


def test_review_indicators_keep_exposure_service_and_participation_separate() -> None:
    indicators = [
        exposure_only("TRecipient", amount_base=1_000, evidence_id="edge-1"),
        service_role_indicator("coinsphere", evidence_id="provider-1", role="custodian"),
    ]

    assert participation_unknown(indicators) is True
    assert {item.evidence_class for item in indicators} == {"fund_exposure", "service_role"}
    assert "does not establish" in indicators[0].benign_explanation


def test_privacy_boundary_and_claim_validation() -> None:
    boundary = privacy_boundary(
        "monero",
        ingress_ref="swap-1",
        note="Known exchange withdrawal to Monero; outgoing destinations are not public.",
    )

    assert boundary.evidence_class == "privacy_boundary"
    assert "without inventing" in boundary.benign_explanation
    errors = validate_claim_references(
        [
            {"evidence_id": "swap-1", "text": "Private interval boundary recorded."},
            {"evidence_id": "missing", "text": "unsupported"},
            {"evidence_id": "swap-1", "text": "contains prohibited " + "guil" + "ty" + " term"},
        ],
        {"swap-1"},
    )
    assert "unknown evidence_id" in errors[0]
    assert "prohibited guilt language" in errors[1]


def test_evidence_claim_review_prevents_overstated_probability_and_participation() -> None:
    indicators = [
        exposure_only("TRecipient", amount_base=1_000, evidence_id="edge-1"),
        service_role_indicator("coinsphere", evidence_id="provider-1", role="custodian"),
        privacy_boundary("monero", ingress_ref="privacy-1", note="Private transfer boundary."),
        EvidenceIndicator(
            evidence_class="correlation_candidate",
            label="candidate bridge exit",
            evidence_id="candidate-1",
            reason="Time and amount are similar, but no protocol join is verified.",
        ),
    ]
    reviews = review_evidence_claims(
        [
            EvidenceClaim("fund_exposure", "edge-1", "Value exposure observed."),
            EvidenceClaim("service_role", "edge-1", "Custody role inferred from exposure."),
            EvidenceClaim(
                "participation",
                "provider-1",
                "Participation inferred from service role.",
            ),
            EvidenceClaim("privacy_boundary", "privacy-1", "Boundary recorded."),
            EvidenceClaim("correlation_candidate", "candidate-1", "Candidate bridge exit."),
            EvidenceClaim("probability", "edge-1", "Calibrated probability asserted."),
        ],
        indicators,
    )

    statuses = [(review.claim.claim_class, review.status) for review in reviews]

    assert statuses == [
        ("fund_exposure", "supported"),
        ("service_role", "unsupported"),
        ("participation", "unsupported"),
        ("privacy_boundary", "supported"),
        ("correlation_candidate", "supported"),
        ("probability", "prohibited"),
    ]
