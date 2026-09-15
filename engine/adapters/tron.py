from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import requests

from app.engine_bridge import ChainRef, explorer_url
from app.services.evidence_store import (
    capture_provider_bytes,
    capture_provider_payload,
    mark_provider_schema,
)
from app.services.provider_budget import reserve_provider_request
from app.services.time import now_ms

DEFAULT_TRONGRID_BASE_URL = "https://api.trongrid.io"
TRON_MAINNET_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRON_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_kind: str = "provider_response",
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.retry_after_ms = retry_after_ms


class ProviderSchemaDriftError(ProviderResponseError):
    pass


@dataclass(frozen=True)
class TronGridConfig:
    base_url: str
    api_key: str
    timeout_s: float = 10.0
    page_limit: int = 200
    max_pages: int = 20


def configured() -> bool:
    return bool((os.getenv("TRONGRID_API_KEY") or "").strip())


def config_from_env() -> TronGridConfig:
    api_key = (os.getenv("TRONGRID_API_KEY") or "").strip()
    if not api_key:
        raise ProviderConfigurationError("TRONGRID_API_KEY is required for live TRON fetching.")
    return TronGridConfig(
        base_url=(os.getenv("TRONGRID_BASE_URL") or DEFAULT_TRONGRID_BASE_URL).rstrip("/"),
        api_key=api_key,
        timeout_s=float(os.getenv("TRINETRA_PROVIDER_TIMEOUT_S", "10")),
        page_limit=min(int(os.getenv("TRINETRA_PROVIDER_PAGE_LIMIT", "200")), 200),
        max_pages=int(os.getenv("TRINETRA_PROVIDER_MAX_PAGES", "20")),
    )


def matches(address: str) -> bool:
    return address.startswith("T") and 26 <= len(address) <= 41


def fetch(address: str, *_args, **kwargs) -> list[dict]:
    return fetch_trc20_transfers(address, **kwargs)


def fetch_trc20_transfers(
    address: str,
    *,
    min_timestamp: int | None = None,
    max_timestamp: int | None = None,
    contract_address: str | None = None,
    order_by: str = "block_timestamp,asc",
    only_to: bool = False,
    session: Any | None = None,
    config: TronGridConfig | None = None,
) -> list[dict]:
    cfg = config or config_from_env()
    http = session or requests.Session()
    params: dict[str, Any] = {
        "only_confirmed": "true",
        "limit": cfg.page_limit,
        "order_by": order_by,
    }
    if min_timestamp is not None:
        params["min_timestamp"] = int(min_timestamp)
    if max_timestamp is not None:
        params["max_timestamp"] = int(max_timestamp)
    if contract_address:
        params["contract_address"] = contract_address
    if only_to:
        params["only_to"] = "true"
    return _paginate(http, cfg, f"/v1/accounts/{address}/transactions/trc20", params)


def fetch_unconfirmed_trc20_transfers(
    address: str,
    *,
    min_timestamp: int | None = None,
    max_timestamp: int | None = None,
    contract_address: str | None = None,
    session: Any | None = None,
    config: TronGridConfig | None = None,
) -> list[dict]:
    cfg = config or config_from_env()
    http = session or requests.Session()
    params: dict[str, Any] = {
        "only_unconfirmed": "true",
        "limit": cfg.page_limit,
        "order_by": "block_timestamp,asc",
    }
    if min_timestamp is not None:
        params["min_timestamp"] = int(min_timestamp)
    if max_timestamp is not None:
        params["max_timestamp"] = int(max_timestamp)
    if contract_address:
        params["contract_address"] = contract_address
    return _paginate(http, cfg, f"/v1/accounts/{address}/transactions/trc20", params)


def fetch_transaction_events(
    txid: str,
    *,
    session: Any | None = None,
    config: TronGridConfig | None = None,
) -> list[dict]:
    cfg = config or config_from_env()
    http = session or requests.Session()
    response = _get_json(
        http,
        cfg,
        f"/v1/transactions/{txid}/events",
        {"only_confirmed": "true"},
    )
    return _data_list(response)


def verify_seed_transfer(
    *,
    txid: str,
    recipient: str,
    amount_base: int,
    contract_address: str = TRON_MAINNET_USDT,
    session: Any | None = None,
    config: TronGridConfig | None = None,
) -> dict:
    events = fetch_transaction_events(txid, session=session, config=config)
    matches = [
        event
        for event in events
        if str(event.get("event_name") or event.get("event") or "").lower() == "transfer"
        and _event_contract(event) == contract_address
        and _event_recipient(event) == recipient
        and _event_amount(event) == amount_base
    ]
    if not matches:
        raise ProviderResponseError(
            "No matching TRC-20 Transfer event found for the supplied seed."
        )
    return matches[0]


def _paginate(http: Any, cfg: TronGridConfig, path: str, params: dict[str, Any]) -> list[dict]:
    rows: list[dict] = []
    seen_fingerprints: set[str] = set()
    current = dict(params)
    for _page in range(cfg.max_pages):
        response = _get_json(http, cfg, path, current)
        rows.extend(_data_list(response))
        fingerprint = ((response.get("meta") or {}).get("fingerprint") or "").strip()
        if not fingerprint:
            return rows
        if fingerprint in seen_fingerprints:
            raise ProviderResponseError("TronGrid pagination cursor repeated.")
        seen_fingerprints.add(fingerprint)
        current["fingerprint"] = fingerprint
    raise ProviderResponseError("TronGrid pagination exceeded the configured page budget.")


def _get_json(http: Any, cfg: TronGridConfig, path: str, params: dict[str, Any]) -> dict:
    url = f"{cfg.base_url}{path}"
    budget = reserve_provider_request("trongrid-v1")
    if not budget.allowed:
        raise ProviderResponseError(
            "Provider request budget is reserved for interactive tracing.",
            error_kind=str(budget.reason or "provider_budget"),
            retry_after_ms=budget.retry_after_ms,
        )
    try:
        response = http.get(
            url,
            params=params,
            headers={"TRON-PRO-API-KEY": cfg.api_key},
            timeout=cfg.timeout_s,
        )
    except requests.RequestException as exc:
        receipt = capture_provider_payload(
            provider="trongrid-v1",
            endpoint=path,
            query=params,
            status_code=None,
            payload={
                "response_available": False,
                "error_kind": exc.__class__.__name__,
            },
        )
        mark_provider_schema(receipt, status="not_received")
        raise ProviderResponseError(
            "TronGrid request failed before a response was received.",
            error_kind="network_error",
        ) from exc

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        body = bytes(getattr(response, "content", b""))
        receipt = capture_provider_bytes(
            provider="trongrid-v1",
            endpoint=path,
            query=params,
            status_code=int(response.status_code),
            body=body,
        )
        conflicts = [{"kind": "non_json_response"}]
        mark_provider_schema(receipt, status="schema_drift", conflicts=conflicts)
        raise ProviderSchemaDriftError(
            "TronGrid returned a non-JSON response.",
            error_kind="schema_drift",
        ) from exc

    receipt = capture_provider_payload(
        provider="trongrid-v1",
        endpoint=path,
        query=params,
        status_code=int(response.status_code),
        payload=payload,
    )
    try:
        if response.status_code in {403, 429} and _looks_rate_limited(payload):
            raise ProviderResponseError(
                "TronGrid rate limit reached.",
                error_kind="rate_limit",
                retry_after_ms=_retry_after_ms(response),
            )
        if response.status_code in {401, 403}:
            raise ProviderConfigurationError("TronGrid rejected the configured API key.")
        if response.status_code == 429:
            raise ProviderResponseError(
                "TronGrid rate limit reached.",
                error_kind="rate_limit",
                retry_after_ms=_retry_after_ms(response),
            )
        if response.status_code >= 500:
            raise ProviderResponseError(
                f"TronGrid returned HTTP {response.status_code}.",
                error_kind="provider_unavailable",
                retry_after_ms=_retry_after_ms(response),
            )
        if response.status_code >= 400:
            raise ProviderResponseError(f"TronGrid returned HTTP {response.status_code}.")
        if not isinstance(payload, dict):
            raise ProviderSchemaDriftError(
                "TronGrid returned a non-object JSON payload.",
                error_kind="schema_drift",
            )
        if payload.get("success") is False:
            raise ProviderResponseError("TronGrid returned success=false.")
        _validate_response_schema(path, payload)
    except ProviderSchemaDriftError as exc:
        mark_provider_schema(
            receipt,
            status="schema_drift",
            conflicts=[{"kind": "schema_drift", "detail": str(exc)}],
        )
        raise
    except (ProviderConfigurationError, ProviderResponseError):
        mark_provider_schema(receipt, status="provider_error")
        raise
    mark_provider_schema(receipt, status="valid")
    normalized_payload = dict(payload)
    normalized_payload["_trinetra_retrieval_ts_ms"] = (
        int(receipt["retrieval_ts_ms"]) if receipt is not None else now_ms()
    )
    if receipt is not None:
        normalized_payload["_trinetra_raw_sha256"] = receipt.get("raw_sha256")
        normalized_payload["_trinetra_provider_request_ref"] = receipt.get("request_ref")
    return normalized_payload


def _data_list(payload: dict) -> list[dict]:
    data = payload.get("data")
    if data is None:
        return []
    if not isinstance(data, list):
        raise ProviderResponseError("TronGrid data field is not a list.")
    if not all(isinstance(item, dict) for item in data):
        raise ProviderResponseError("TronGrid data field contains a non-object item.")
    retrieval_ts_ms = payload.get("_trinetra_retrieval_ts_ms")
    raw_sha256 = payload.get("_trinetra_raw_sha256")
    provider_request_ref = payload.get("_trinetra_provider_request_ref")
    if retrieval_ts_ms is None and raw_sha256 is None and provider_request_ref is None:
        return data
    return [
        dict(
            item,
            **(
                {"_retrieval_ts_ms": retrieval_ts_ms}
                if retrieval_ts_ms is not None
                else {}
            ),
            **({"_raw_sha256": raw_sha256} if raw_sha256 is not None else {}),
            **(
                {"_provider_request_ref": provider_request_ref}
                if provider_request_ref is not None
                else {}
            ),
        )
        for item in data
    ]


def _validate_response_schema(path: str, payload: dict) -> None:
    data = payload.get("data")
    if data is not None and not isinstance(data, list):
        raise ProviderSchemaDriftError(
            "TronGrid data field is not a list.",
            error_kind="schema_drift",
        )
    if isinstance(data, list) and not all(isinstance(item, dict) for item in data):
        raise ProviderSchemaDriftError(
            "TronGrid data field contains a non-object item.",
            error_kind="schema_drift",
        )
    meta = payload.get("meta")
    if meta is not None and not isinstance(meta, dict):
        raise ProviderSchemaDriftError(
            "TronGrid meta field is not an object.",
            error_kind="schema_drift",
        )
    if isinstance(meta, dict):
        fingerprint = meta.get("fingerprint")
        if fingerprint is not None and not isinstance(fingerprint, str):
            raise ProviderSchemaDriftError(
                "TronGrid pagination fingerprint is not a string.",
                error_kind="schema_drift",
            )
    if path.endswith("/transactions/trc20"):
        for row in data or []:
            missing = [
                key
                for key in ("transaction_id", "block_timestamp", "from", "to", "value")
                if key not in row or row[key] is None
            ]
            if missing:
                raise ProviderSchemaDriftError(
                    "TronGrid TRC-20 row is missing required fields: " + ", ".join(missing),
                    error_kind="schema_drift",
                )
            token_info = row.get("token_info")
            if token_info is not None and not isinstance(token_info, dict):
                raise ProviderSchemaDriftError(
                    "TronGrid token_info field is not an object.",
                    error_kind="schema_drift",
                )
            try:
                int(row["block_timestamp"])
                int(row["value"])
                if isinstance(token_info, dict) and token_info.get("decimals") is not None:
                    int(token_info["decimals"])
            except (TypeError, ValueError) as exc:
                raise ProviderSchemaDriftError(
                    "TronGrid TRC-20 row has invalid integer fields.",
                    error_kind="schema_drift",
                ) from exc


def _looks_rate_limited(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("statusCode") == 429:
        return True
    message = str(payload.get("error") or payload.get("Error") or "").lower()
    return "frequency limit" in message or "rate limit" in message


def _retry_after_ms(response: Any) -> int | None:
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = int(str(value).strip())
    except ValueError:
        return None
    return max(0, seconds) * 1000


def normalise(raw: dict) -> dict:
    token_info = raw.get("token_info")
    if token_info is None:
        token_info = {}
    if not isinstance(token_info, dict):
        raise ProviderSchemaDriftError(
            "TRON transfer payload token_info is not an object.",
            error_kind="schema_drift",
        )
    txid = _first_present(raw, "transaction_id", "transaction_hash", "tx_id")
    timestamp = _first_present(raw, "block_timestamp", "block_ts")
    source = _first_present(raw, "from", "from_address")
    destination = _first_present(raw, "to", "to_address")
    amount = _first_present(raw, "value", "amount")
    if not txid or timestamp is None or not source or not destination or amount is None:
        raise ProviderResponseError("TRON transfer payload is missing required fields.")
    try:
        timestamp_value = int(timestamp)
        amount_value = int(amount)
        decimals_value = int(token_info.get("decimals", 6))
    except (TypeError, ValueError) as exc:
        raise ProviderSchemaDriftError(
            "TRON transfer payload has invalid integer fields.",
            error_kind="schema_drift",
        ) from exc
    normalized = {
        "txid": str(txid),
        "ts_ms": timestamp_value,
        "source": str(source),
        "destination": str(destination),
        "amount_base": amount_value,
        "token": token_info.get("symbol", "USDT"),
        "decimals": decimals_value,
        "contract": token_info.get("address") or raw.get("contract_address"),
        "block": _first_present(raw, "block", "block_number"),
        "event_index": _first_present(raw, "event_index", "log_index"),
    }
    if raw.get("_retrieval_ts_ms") is not None:
        normalized["retrieval_ts_ms"] = int(raw["_retrieval_ts_ms"])
    if raw.get("_raw_sha256") is not None:
        normalized["raw_sha256"] = str(raw["_raw_sha256"])
    if raw.get("_provider_request_ref") is not None:
        normalized["provider_request_ref"] = str(raw["_provider_request_ref"])
    return normalized


def address_url(address: str) -> str:
    return explorer_url("address", address, ChainRef("TRON", "mainnet"))


def _event_contract(event: dict) -> str | None:
    value = (
        event.get("contract_address")
        or event.get("contract")
        or (event.get("contract_info") or {}).get("address")
    )
    return _canonical_tron_address(value)


def _event_recipient(event: dict) -> str | None:
    result = event.get("result") or {}
    value = event.get("to") or event.get("to_address") or result.get("to")
    return _canonical_tron_address(value)


def _event_amount(event: dict) -> int | None:
    result = event.get("result") or {}
    value = _first_present(event, "value", "amount")
    if value is None:
        value = _first_present(result, "value")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderSchemaDriftError(
            "TronGrid event amount is not an integer.",
            error_kind="schema_drift",
        ) from exc


def _first_present(payload: dict, *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _canonical_tron_address(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("T"):
        return text
    hex_text = text[2:] if text.lower().startswith("0x") else text
    if len(hex_text) == 40 and all(char in "0123456789abcdefABCDEF" for char in hex_text):
        hex_text = "41" + hex_text
    if len(hex_text) != 42 or not hex_text.lower().startswith("41"):
        return text
    if not all(char in "0123456789abcdefABCDEF" for char in hex_text):
        return text
    payload = bytes.fromhex(hex_text)
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    encoded = payload + checksum
    number = int.from_bytes(encoded, "big")
    characters = ""
    while number:
        number, remainder = divmod(number, 58)
        characters = TRON_BASE58_ALPHABET[remainder] + characters
    leading_zeroes = len(encoded) - len(encoded.lstrip(b"\x00"))
    return "1" * leading_zeroes + (characters or "1")
