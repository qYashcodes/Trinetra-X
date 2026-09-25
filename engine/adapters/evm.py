from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from app.engine_bridge import EVM_CHAINS, ChainRef, explorer_url
from app.services.evidence_store import capture_provider_payload, mark_provider_schema
from app.services.provider_budget import reserve_provider_request
from engine.contracts import NormalizedTransferRecord

DEFAULT_ETHERSCAN_BASE_URL = "https://api.etherscan.io/v2/api"


class EvmAdapterUnavailableError(RuntimeError):
    pass


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
class EtherscanConfig:
    base_url: str
    api_keys: tuple[str, ...]
    timeout_s: float = 10.0
    page_size: int = 200
    max_pages: int = 50


_key_index = 0


def configured() -> bool:
    return bool((os.getenv("ETHERSCAN_API_KEY") or "").strip())


def config_from_env() -> EtherscanConfig:
    raw_keys = (os.getenv("ETHERSCAN_API_KEY") or "").strip()
    if not raw_keys:
        raise ProviderConfigurationError("ETHERSCAN_API_KEY is required for live EVM fetching.")
    keys = tuple(key.strip() for key in raw_keys.split(",") if key.strip())
    if not keys:
        raise ProviderConfigurationError("ETHERSCAN_API_KEY is empty.")
    return EtherscanConfig(
        base_url=(os.getenv("ETHERSCAN_BASE_URL") or DEFAULT_ETHERSCAN_BASE_URL).rstrip("/"),
        api_keys=keys,
        timeout_s=float(os.getenv("TRINETRA_PROVIDER_TIMEOUT_S", "10")),
        page_size=min(int(os.getenv("TRINETRA_EVM_PROVIDER_PAGE_SIZE", "200")), 1000),
        max_pages=int(os.getenv("TRINETRA_EVM_PROVIDER_MAX_PAGES", "50")),
    )


def configured_chains() -> list[ChainRef]:
    configured_ids = {
        int(item.strip())
        for item in (os.getenv("TRINETRA_EVM_CHAIN_IDS") or "1").split(",")
        if item.strip().isdigit()
    }
    chains = [chain for chain in EVM_CHAINS if chain.chain_id in configured_ids]
    return chains or [EVM_CHAINS[0]]


def adapter_status() -> dict[str, object]:
    return {
        "family": "EVM",
        "enabled": False,
        "configured_key": configured(),
        "required_gate": "TRINETRA_LIVE_EVM_TRACE_VERIFIED",
        "reason": "EVM token tracing is explicitly unsupported until adapter evidence gates pass.",
    }


def fetch_token_transfers(address: str, chain: ChainRef) -> list[dict]:
    return fetch_address_transactions(address, chain=chain, native=False)


def fetch_address_transactions(
    address: str,
    *,
    chain: ChainRef | None = None,
    asset_id: str | None = None,
    native: bool = True,
    token: bool = True,
    session: Any | None = None,
    config: EtherscanConfig | None = None,
) -> list[dict]:
    cfg = config or config_from_env()
    selected_chain = chain or EVM_CHAINS[0]
    rows: dict[str, dict] = {}
    if native:
        for row in _page_history(
            address,
            chain=selected_chain,
            action="txlist",
            session=session,
            config=cfg,
        ):
            normalized = _normalize_native_row(row, symbol=_native_symbol(selected_chain))
            if normalized and normalized.get("vout"):
                rows[str(normalized["txid"]).lower()] = normalized
    if token:
        contract_filter = _contract_from_asset_id(asset_id)
        for row in _page_history(
            address,
            chain=selected_chain,
            action="tokentx",
            filter_key={"contractaddress": contract_filter} if contract_filter else None,
            session=session,
            config=cfg,
        ):
            txid = row.get("hash")
            if not txid:
                continue
            item = _normalize_token_row(row)
            if not item:
                continue
            existing = rows.setdefault(
                str(txid).lower(),
                {
                    "txid": str(txid),
                    "status": dict(item["status"]),
                    "vin": [],
                    "vout": [],
                    "symbol": item.get("symbol"),
                    "decimals": item.get("decimals"),
                    "raw": row,
                },
            )
            for vin in item.get("vin") or []:
                if vin not in existing["vin"]:
                    existing["vin"].append(vin)
            existing["vout"].extend(item.get("vout") or [])
    result = [_dedupe_outputs(row, asset_id=asset_id) for row in rows.values()]
    result = [row for row in result if row.get("vout")]
    result.sort(
        key=lambda row: (
            int((row.get("status") or {}).get("block_height") or 0),
            int((row.get("status") or {}).get("transaction_index") or 0),
            int((row.get("status") or {}).get("block_time") or 0),
            str(row.get("txid") or ""),
        )
    )
    return result


def resolve_transaction(
    txid: str,
    *,
    chain: ChainRef | None = None,
    session: Any | None = None,
    config: EtherscanConfig | None = None,
) -> dict | None:
    selected_chain = chain or EVM_CHAINS[0]
    cfg = config or config_from_env()
    canonical_txid = _canonical_txid(txid)
    data = _api(
        module="proxy",
        action="eth_getTransactionByHash",
        txhash=canonical_txid,
        chain=selected_chain,
        session=session,
        config=cfg,
    )
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    if str(result.get("hash") or "").lower() != canonical_txid.lower():
        return None
    receipt_payload = _api(
        module="proxy",
        action="eth_getTransactionReceipt",
        txhash=canonical_txid,
        chain=selected_chain,
        session=session,
        config=cfg,
    )
    receipt = receipt_payload.get("result") or {}
    if receipt and not isinstance(receipt, dict):
        raise ProviderSchemaDriftError(
            "Etherscan transaction receipt result is not an object.",
            error_kind="schema_drift",
        )
    block_number_hex = result.get("blockNumber")
    block_time = _block_time(block_number_hex, chain=selected_chain, session=session, config=cfg)
    sender = _normalize_address(result.get("from"))
    recipient = _normalize_address(result.get("to"))
    native_value = _hex_int(result.get("value"), 0) or 0
    outputs = []
    if recipient and native_value > 0:
        outputs.append(
            {
                "scriptpubkey_address": recipient,
                "value": native_value,
                "token": _native_symbol(selected_chain),
                "decimals": 18,
                "asset_id": _native_symbol(selected_chain),
            }
        )
    token_transfers = _token_transfers_for_tx(
        sender or recipient,
        canonical_txid,
        chain=selected_chain,
        session=session,
        config=cfg,
    )
    for row in token_transfers:
        item = _normalize_token_row(row)
        if item:
            outputs.extend(item.get("vout") or [])
    confirmed = bool(block_number_hex) and str(receipt.get("status", "0x1")).lower() != "0x0"
    normalized = {
        "txid": canonical_txid,
        "status": {
            "confirmed": confirmed,
            "block_time": block_time,
            "block_height": _hex_int(block_number_hex),
            "transaction_index": _hex_int(result.get("transactionIndex")),
        },
        "vin": [{"prevout": {"scriptpubkey_address": sender}}] if sender else [],
        "vout": outputs,
        "symbol": _native_symbol(selected_chain),
        "decimals": 18,
        "raw": result,
    }
    asset_ids = {output.get("asset_id") for output in outputs if output.get("asset_id")}
    normalized["asset_id"] = next(iter(asset_ids)) if len(asset_ids) == 1 else None
    return normalized


def normalise(raw: dict, *, output_index: int = 0) -> NormalizedTransferRecord:
    outputs = raw.get("vout") or []
    inputs = raw.get("vin") or []
    if not outputs:
        raise ProviderResponseError("EVM transaction has no value-moving output.")
    if output_index >= len(outputs):
        raise ProviderResponseError("EVM output index is out of range.")
    output = outputs[output_index]
    status = raw.get("status") or {}
    source = ((inputs[0] or {}).get("prevout") or {}).get("scriptpubkey_address") if inputs else None
    destination = output.get("scriptpubkey_address")
    txid = raw.get("txid")
    block_time = status.get("block_time")
    amount = output.get("value")
    if not txid or not source or not destination or amount is None or block_time is None:
        raise ProviderResponseError("EVM transaction payload is missing required fields.")
    try:
        timestamp_ms = int(block_time) * 1000
        amount_base = int(amount)
        decimals = int(output.get("decimals", 18))
    except (TypeError, ValueError) as exc:
        raise ProviderSchemaDriftError(
            "EVM transaction payload has invalid integer fields.",
            error_kind="schema_drift",
        ) from exc
    return {
        "txid": str(txid),
        "ts_ms": timestamp_ms,
        "source": str(source),
        "destination": str(destination),
        "amount_base": amount_base,
        "token": output.get("token"),
        "decimals": decimals,
        "contract": _contract_from_asset_id(output.get("asset_id")),
        "block": status.get("block_height"),
        "event_index": status.get("transaction_index"),
    }


def address_url(address: str, chain: ChainRef | None = None) -> str:
    return explorer_url("address", address, chain or EVM_CHAINS[0])


def _api(
    *,
    chain: ChainRef,
    session: Any | None,
    config: EtherscanConfig,
    **params: Any,
) -> dict:
    global _key_index
    key = config.api_keys[_key_index % len(config.api_keys)]
    _key_index += 1
    query = {**params, "chainid": chain.chain_id or 1, "apikey": key}
    budget = reserve_provider_request("etherscan-v2")
    if not budget.allowed:
        raise ProviderResponseError(
            "Provider request budget is reserved for interactive tracing.",
            error_kind=str(budget.reason or "provider_budget"),
            retry_after_ms=budget.retry_after_ms,
        )
    http = session or requests.Session()
    safe_query = {name: value for name, value in query.items() if name != "apikey"}
    try:
        response = http.get(config.base_url, params=query, timeout=config.timeout_s)
    except requests.RequestException as exc:
        receipt = capture_provider_payload(
            provider="etherscan-v2",
            endpoint="/api",
            query=safe_query,
            status_code=None,
            payload={"response_available": False, "error_kind": exc.__class__.__name__},
        )
        mark_provider_schema(receipt, status="not_received")
        raise ProviderResponseError(
            "Etherscan request failed before a response was received.",
            error_kind="network_error",
        ) from exc
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        receipt = capture_provider_payload(
            provider="etherscan-v2",
            endpoint="/api",
            query=safe_query,
            status_code=int(response.status_code),
            payload={"non_json_response": True},
        )
        mark_provider_schema(receipt, status="schema_drift")
        raise ProviderSchemaDriftError(
            "Etherscan returned a non-JSON response.",
            error_kind="schema_drift",
        ) from exc
    receipt = capture_provider_payload(
        provider="etherscan-v2",
        endpoint="/api",
        query=safe_query,
        status_code=int(response.status_code),
        payload=payload,
    )
    try:
        if response.status_code in {401, 403}:
            raise ProviderConfigurationError("Etherscan rejected the configured API key.")
        if response.status_code == 429:
            raise ProviderResponseError("Etherscan rate limit reached.", error_kind="rate_limit")
        if response.status_code >= 500:
            raise ProviderResponseError(
                f"Etherscan returned HTTP {response.status_code}.",
                error_kind="provider_unavailable",
            )
        if response.status_code >= 400:
            raise ProviderResponseError(f"Etherscan returned HTTP {response.status_code}.")
        if not isinstance(payload, dict):
            raise ProviderSchemaDriftError(
                "Etherscan returned a non-object JSON payload.",
                error_kind="schema_drift",
            )
        _validate_response(payload)
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
    return payload


def _validate_response(payload: dict) -> None:
    if "result" not in payload:
        raise ProviderSchemaDriftError(
            "Etherscan payload is missing result.",
            error_kind="schema_drift",
        )
    result = payload.get("result")
    if result is not None and not isinstance(result, (dict, list, str)):
        raise ProviderSchemaDriftError(
            "Etherscan result has unsupported shape.",
            error_kind="schema_drift",
        )
    if str(payload.get("status") or "") == "0":
        message = str(payload.get("message") or "")
        result = str(payload.get("result") or "")
        detail = result or message
        if "No transactions found" in message or "No transactions found" in result:
            return
        if "NOTOK" in message.upper() or "Invalid API Key" in detail:
            raise ProviderResponseError(detail or "Etherscan returned NOTOK.")


def _page_history(
    address: str,
    *,
    chain: ChainRef,
    action: str,
    filter_key: dict[str, Any] | None = None,
    session: Any | None,
    config: EtherscanConfig,
) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, config.max_pages + 1):
        query = {
            "module": "account",
            "action": action,
            "address": address,
            "startblock": 0,
            "endblock": 999999999,
            "page": page,
            "offset": config.page_size,
            "sort": "asc",
        }
        if filter_key:
            query.update(filter_key)
        payload = _api(chain=chain, session=session, config=config, **query)
        result = payload.get("result") or []
        if isinstance(result, str) and "No transactions found" in result:
            return rows
        if not isinstance(result, list):
            raise ProviderSchemaDriftError(
                "Etherscan history result is not a list.",
                error_kind="schema_drift",
            )
        if not result:
            return rows
        if not all(isinstance(item, dict) for item in result):
            raise ProviderSchemaDriftError(
                "Etherscan history contains a non-object row.",
                error_kind="schema_drift",
            )
        rows.extend(result)
        if len(result) < config.page_size:
            return rows
    raise ProviderResponseError("Etherscan pagination exceeded the configured page budget.")


def _token_transfers_for_tx(
    address: str | None,
    txid: str,
    *,
    chain: ChainRef,
    session: Any | None,
    config: EtherscanConfig,
) -> list[dict]:
    if not address:
        return []
    found = []
    for page in range(1, min(config.max_pages, 5) + 1):
        payload = _api(
            module="account",
            action="tokentx",
            address=address,
            startblock=0,
            endblock=999999999,
            page=page,
            offset=config.page_size,
            sort="asc",
            chain=chain,
            session=session,
            config=config,
        )
        result = payload.get("result") or []
        if isinstance(result, str) and "No transactions found" in result:
            return found
        if not isinstance(result, list):
            raise ProviderSchemaDriftError(
                "Etherscan token transfer result is not a list.",
                error_kind="schema_drift",
            )
        for row in result:
            if isinstance(row, dict) and str(row.get("hash") or "").lower() == _canonical_txid(txid).lower():
                found.append(row)
        if len(result) < config.page_size:
            return found
    return found


def _block_time(
    block_number: object,
    *,
    chain: ChainRef,
    session: Any | None,
    config: EtherscanConfig,
) -> int | None:
    if not block_number:
        return None
    payload = _api(
        module="proxy",
        action="eth_getBlockByNumber",
        tag=block_number,
        boolean="true",
        chain=chain,
        session=session,
        config=config,
    )
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise ProviderSchemaDriftError(
            "Etherscan block result is not an object.",
            error_kind="schema_drift",
        )
    return _hex_int(result.get("timestamp"))


def _normalize_native_row(row: dict, *, symbol: str) -> dict | None:
    txid = row.get("hash")
    if not txid:
        return None
    sender = _normalize_address(row.get("from"))
    recipient = _normalize_address(row.get("to"))
    value = _safe_int(row.get("value"), 0) or 0
    outputs = []
    if recipient and value > 0:
        outputs.append(
            {
                "scriptpubkey_address": recipient,
                "value": value,
                "token": symbol,
                "decimals": 18,
                "asset_id": symbol,
            }
        )
    return {
        "txid": str(txid),
        "status": {
            "confirmed": True,
            "block_time": _safe_int(row.get("timeStamp")),
            "block_height": _safe_int(row.get("blockNumber")),
            "transaction_index": _safe_int(row.get("transactionIndex")),
        },
        "vin": [{"prevout": {"scriptpubkey_address": sender}}] if sender else [],
        "vout": outputs,
        "symbol": symbol,
        "decimals": 18,
        "asset_id": symbol if value > 0 else None,
        "raw": row,
    }


def _normalize_token_row(row: dict) -> dict | None:
    txid = row.get("hash")
    if not txid:
        return None
    sender = _normalize_address(row.get("from"))
    recipient = _normalize_address(row.get("to"))
    contract = _normalize_contract(row.get("contractAddress"))
    token_symbol = row.get("tokenSymbol") or "TOKEN"
    decimals = _safe_int(row.get("tokenDecimal"), 18) or 18
    value = _safe_int(row.get("value"), 0) or 0
    asset_id = _token_asset_id(contract)
    outputs = []
    if recipient:
        outputs.append(
            {
                "scriptpubkey_address": recipient,
                "value": value,
                "token": token_symbol,
                "decimals": decimals,
                "asset_id": asset_id,
            }
        )
    return {
        "txid": str(txid),
        "status": {
            "confirmed": True,
            "block_time": _safe_int(row.get("timeStamp")),
            "block_height": _safe_int(row.get("blockNumber")),
            "transaction_index": _safe_int(row.get("transactionIndex")),
        },
        "vin": [{"prevout": {"scriptpubkey_address": sender}}] if sender else [],
        "vout": outputs,
        "symbol": token_symbol,
        "decimals": decimals,
        "asset_id": asset_id,
        "raw": row,
    }


def _dedupe_outputs(tx: dict, *, asset_id: str | None) -> dict:
    unique_outputs = []
    seen = set()
    for output in tx.get("vout") or []:
        key = (
            output.get("scriptpubkey_address"),
            output.get("value"),
            output.get("asset_id"),
            output.get("token"),
            output.get("decimals"),
        )
        if key in seen:
            continue
        seen.add(key)
        if asset_id and output.get("asset_id") != asset_id:
            continue
        unique_outputs.append(output)
    result = dict(tx)
    result["vout"] = unique_outputs
    asset_ids = {output.get("asset_id") for output in unique_outputs if output.get("asset_id")}
    result["asset_id"] = next(iter(asset_ids)) if len(asset_ids) == 1 else None
    return result


def _safe_int(value: object, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _hex_int(value: object, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        if isinstance(value, int):
            return value
        text = str(value)
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return default


def _normalize_address(address: object) -> str | None:
    if not address:
        return None
    return str(address).strip()


def _normalize_contract(contract: object) -> str | None:
    if not contract:
        return None
    return str(contract).strip().lower()


def _token_asset_id(contract: str | None) -> str | None:
    normalized = _normalize_contract(contract)
    return f"ERC20:{normalized}" if normalized else None


def _contract_from_asset_id(asset_id: object) -> str | None:
    if asset_id is None:
        return None
    text = str(asset_id)
    if text.upper().startswith("ERC20:"):
        return text.split(":", 1)[1].strip().lower()
    return None


def _native_symbol(chain: ChainRef) -> str:
    if chain.chain_id == 56:
        return "BNB"
    if chain.chain_id == 137:
        return "MATIC"
    return "ETH"


def _canonical_txid(txid: str) -> str:
    text = str(txid).strip()
    return text if text.lower().startswith("0x") else f"0x{text}"
