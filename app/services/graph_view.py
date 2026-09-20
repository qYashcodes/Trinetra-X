from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any


GRAPH_VIEW_SCHEMA = "trinetra.omega_graph/1"
BalanceLookup = Callable[
    [set[str], dict[str, Any], dict[str, Any]],
    Mapping[str, Any],
]


def omega_graph_payload(
    snapshot: dict[str, Any] | None,
    *,
    case: Any | None = None,
    balance_lookup: BalanceLookup | None = None,
) -> dict[str, Any]:
    """Map a TRINETRA trace snapshot into the Omega static SVG tree contract."""
    result = dict(snapshot or {})
    asset = _asset(result, case)
    case_data = dict(result.get("case") or {})
    seed_match = dict(result.get("seed_match") or {})
    hops = [dict(item) for item in result.get("hops") or []]
    parked = [dict(item) for item in result.get("parked") or []]
    terminal = dict(result.get("terminal") or {})
    root_address = (
        case_data.get("reported_address")
        or getattr(case, "reported_address", None)
        or seed_match.get("address")
        or (hops[0].get("source_address") if hops else None)
        or (hops[0].get("address") if hops else None)
        or terminal.get("deposit_address")
        or terminal.get("current_address")
        or "Reported address"
    )
    balances = _wallet_balance_map(
        result,
        asset,
        _graph_addresses(str(root_address), hops, parked, terminal),
        balance_lookup=balance_lookup,
    )
    seed_amount = _first_present(
        seed_match.get("amount_base"),
        case_data.get("amount_reported_base"),
        getattr(case, "amount_reported_base", None),
        terminal.get("amount_credited_base"),
        0,
    )
    root = _node(
        str(root_address),
        role="seed",
        amount_base=seed_amount,
        asset=asset,
        balance=_balance_for(balances, str(root_address), asset),
        explain_target="seed",
        metadata={
            "title": "Reported payment anchor",
            "trace_role": "reported recipient",
            "txid": seed_match.get("txid") or case_data.get("payment_txid") or getattr(case, "payment_txid", None),
            "ts_ms": _first_present(
                seed_match.get("ts_ms"),
                case_data.get("payment_ts"),
                case_data.get("payment_ts_ms"),
                getattr(case, "payment_ts_ms", None),
            ),
        },
    )
    root["received_transactions"] = [
        {
            "txid": root["metadata"].get("txid"),
            "value": seed_amount,
            "value_display": _display_amount(seed_amount, asset["decimals"]),
            "token": asset["symbol"],
            "decimals": asset["decimals"],
            "ts_ms": root["metadata"].get("ts_ms"),
        }
    ]

    nodes_by_address: dict[str, dict[str, Any]] = {_address_key(root["address"]): root}
    nodes_by_hop: dict[int, dict[str, Any]] = {0: root}
    current = root
    previous_address = str(root_address)
    for index, hop in enumerate(hops):
        destination = str(hop.get("address") or hop.get("destination") or hop.get("destination_address") or "")
        if not destination:
            continue
        confirmed_amount, attributed_amount = _transfer_amounts(hop)
        amount_metadata = _amount_metadata(
            confirmed_amount,
            attributed_amount,
            asset=asset,
        )
        if index == 0 and _address_key(destination) == _address_key(previous_address):
            current["metadata"].update(
                {
                    "title": f"Hop {hop.get('hop', index)}",
                    "frontier_state": hop.get("frontier_state"),
                    "event_ref": hop.get("event_ref"),
                    "txid": _first_txid(hop) or current["metadata"].get("txid"),
                    **amount_metadata,
                }
            )
            nodes_by_address[_address_key(destination)] = current
            nodes_by_hop[int(hop.get("hop", index))] = current
            continue
        child = _node(
            destination,
            role="hop",
            amount_base=confirmed_amount,
            asset=asset,
            balance=_balance_for(balances, destination, asset),
            explain_target=f"hop:{index}",
            metadata={
                "title": f"Hop {hop.get('hop', index + 1)}",
                "frontier_state": hop.get("frontier_state"),
                "event_ref": hop.get("event_ref"),
                "txid": _first_txid(hop),
                "ts_ms": hop.get("ts_ms"),
                "source_address": hop.get("source_address") or previous_address,
                "class": hop.get("class") or hop.get("candidate"),
                "note": hop.get("note"),
                **amount_metadata,
            },
        )
        edge = _edge(
            recipient=destination,
            amount_base=confirmed_amount,
            asset=asset,
            txid=_first_txid(hop),
            output_index=_first_present(hop.get("event_index"), hop.get("output_index"), index),
            explain_target=f"hop:{index}",
            metadata={
                "kind": "hop",
                "frontier_state": hop.get("frontier_state"),
                "event_ref": hop.get("event_ref"),
                "ts_ms": hop.get("ts_ms"),
                **amount_metadata,
            },
            branch_reasons=_branch_reasons(hop),
            child=child,
        )
        current.setdefault("children", []).append(edge)
        current = child
        previous_address = destination
        nodes_by_address[_address_key(destination)] = child
        nodes_by_hop[int(hop.get("hop", index + 1))] = child

    for index, branch in enumerate(parked):
        destination = str(branch.get("address") or branch.get("destination") or branch.get("destination_address") or "")
        if not destination:
            continue
        source = branch.get("source_address")
        parent = nodes_by_address.get(_address_key(str(source))) if source else None
        if parent is None and branch.get("from_hop") is not None:
            try:
                parent = nodes_by_hop.get(int(branch["from_hop"]))
            except (TypeError, ValueError):
                parent = None
        if parent is None:
            parent = current if current is not root else root
        confirmed_amount, attributed_amount = _transfer_amounts(branch)
        amount_metadata = _amount_metadata(
            confirmed_amount,
            attributed_amount,
            asset=asset,
        )
        child = _node(
            destination,
            role="deferred",
            amount_base=confirmed_amount,
            asset=asset,
            balance=_balance_for(balances, destination, asset),
            explain_target=f"parked:{index}",
            metadata={
                "title": f"Parked branch {branch.get('branch_id') or index + 1}",
                "frontier_state": branch.get("frontier_state") or "deferred",
                "event_ref": branch.get("event_ref") or branch.get("branch_id"),
                "txid": _first_txid(branch),
                "ts_ms": branch.get("ts_ms"),
                "source_address": source,
                "reason": branch.get("deferral_reason") or branch.get("reason"),
                **amount_metadata,
            },
        )
        parent.setdefault("children", []).append(
            _edge(
                recipient=destination,
                amount_base=confirmed_amount,
                asset=asset,
                txid=_first_txid(branch),
                output_index=_first_present(branch.get("event_index"), branch.get("output_index"), index),
                explain_target=f"parked:{index}",
                metadata={
                    "kind": "parked",
                    "frontier_state": branch.get("frontier_state") or "deferred",
                    "event_ref": branch.get("event_ref") or branch.get("branch_id"),
                    "ts_ms": branch.get("ts_ms"),
                    **amount_metadata,
                },
                branch_reasons=_branch_reasons(branch),
                child=child,
            )
        )
        nodes_by_address[_address_key(destination)] = child

    _attach_terminal(root, current, nodes_by_address, terminal, asset, balances)
    return {
        "schema": GRAPH_VIEW_SCHEMA,
        "title": "Transaction flow",
        "subtitle": "SVG graph adapted from Akshat's Omega graph for TRINETRA sealed snapshots.",
        "asset": asset,
        "mode": str((result.get("engine") or {}).get("mode") or "fixture"),
        "tree": root,
    }


def _attach_terminal(
    root: dict[str, Any],
    current: dict[str, Any],
    nodes_by_address: dict[str, dict[str, Any]],
    terminal: dict[str, Any],
    asset: dict[str, Any],
    balances: Mapping[str, dict[str, Any]],
) -> None:
    hot_wallet = terminal.get("hot_wallet")
    deposit_address = terminal.get("deposit_address")
    terminal_address = hot_wallet or terminal.get("current_address")
    if not terminal_address:
        return
    if deposit_address and _address_key(str(terminal_address)) == _address_key(str(deposit_address)):
        return
    parent = nodes_by_address.get(_address_key(str(deposit_address))) if deposit_address else None
    parent = parent or current or root
    amount = _first_present(terminal.get("amount_credited_base"), parent.get("amount"), 0)
    child = _node(
        str(terminal_address),
        role="terminal",
        amount_base=amount,
        asset=asset,
        balance=_balance_for(balances, str(terminal_address), asset),
        explain_target="terminal",
        metadata={
            "title": "Terminal account object",
            "terminal_kind": terminal.get("kind"),
            "custodian_key": terminal.get("custodian_key"),
            "note": terminal.get("note"),
            "deposit_address": deposit_address,
            "trace_role": "terminal metadata only",
        },
    )
    parent.setdefault("children", []).append(
        _edge(
            recipient=str(terminal_address),
            amount_base=amount,
            asset=asset,
            txid=terminal.get("txid"),
            output_index="terminal",
            explain_target="terminal",
            metadata={
                "kind": "terminal",
                "terminal_kind": terminal.get("kind"),
                "custodian_key": terminal.get("custodian_key"),
            },
            branch_reasons=[
                "Terminal metadata is displayed separately from the deposit address.",
                "A service label is not account identity or person identity.",
            ],
            child=child,
        )
    )


def _asset(snapshot: dict[str, Any], case: Any | None) -> dict[str, Any]:
    data = dict(snapshot.get("asset") or {})
    return {
        "symbol": str(data.get("symbol") or getattr(case, "asset_symbol", None) or "USDT"),
        "decimals": int(data.get("decimals") or getattr(case, "asset_decimals", None) or 6),
        "contract": data.get("contract") or data.get("contract_address"),
    }


def _transfer_amounts(item: dict[str, Any]) -> tuple[Any, Any]:
    attributed = _first_present(item.get("value_base"), item.get("attributed_amount_base"), 0)
    confirmed = _first_present(item.get("observed_amount_base"), item.get("amount_base"), attributed)
    return confirmed, attributed


def _amount_metadata(confirmed: Any, attributed: Any, *, asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "amount_semantics": "confirmed_transfer_with_attributed_share",
        "observed_amount_base": confirmed,
        "observed_amount_display": _display_amount(confirmed, asset["decimals"]),
        "attributed_amount_base": attributed,
        "attributed_amount_display": _display_amount(attributed, asset["decimals"]),
    }


def _node(
    address: str,
    *,
    role: str,
    amount_base: Any,
    asset: dict[str, Any],
    balance: dict[str, Any],
    explain_target: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "address": address,
        "role": role,
        "amount": amount_base,
        "amount_display": _display_amount(amount_base, asset["decimals"]),
        "token": asset["symbol"],
        "decimals": asset["decimals"],
        "balance": balance,
        "metadata": metadata,
        "explain_target": explain_target,
        "children": [],
    }


def _edge(
    *,
    recipient: str,
    amount_base: Any,
    asset: dict[str, Any],
    txid: str | None,
    output_index: Any,
    explain_target: str,
    metadata: dict[str, Any],
    branch_reasons: list[str],
    child: dict[str, Any],
) -> dict[str, Any]:
    return {
        "recipient": recipient,
        "amount": amount_base,
        "amount_display": _display_amount(amount_base, asset["decimals"]),
        "token": asset["symbol"],
        "decimals": asset["decimals"],
        "sent_txid": txid,
        "output_index": output_index,
        "branch_reasons": branch_reasons,
        "metadata": metadata,
        "explain_target": explain_target,
        "child": child,
    }


def _branch_reasons(item: dict[str, Any]) -> list[str]:
    reasons = [
        item.get("deferral_reason"),
        item.get("reason"),
        item.get("frontier_state"),
        (item.get("scheduling") or {}).get("decision") if isinstance(item.get("scheduling"), dict) else None,
    ]
    return [str(reason).replace("_", " ") for reason in reasons if reason]


def _display_amount(value: Any, decimals: int) -> float:
    try:
        base = Decimal(str(value if value is not None else 0))
        return float(base / (Decimal(10) ** int(decimals)))
    except (InvalidOperation, ValueError, TypeError):
        return 0.0


def _graph_addresses(
    root_address: str,
    hops: list[dict[str, Any]],
    parked: list[dict[str, Any]],
    terminal: dict[str, Any],
) -> set[str]:
    addresses = {root_address}
    for item in [*hops, *parked]:
        destination = item.get("address") or item.get("destination") or item.get("destination_address")
        if destination is not None:
            addresses.add(str(destination))
        source = item.get("source_address")
        if source is not None:
            addresses.add(str(source))
    for key in ("deposit_address", "hot_wallet", "current_address"):
        value = terminal.get(key)
        if value is not None:
            addresses.add(str(value))
    return {address for address in addresses if address.strip()}


def _wallet_balance_map(
    result: dict[str, Any],
    asset: dict[str, Any],
    addresses: set[str],
    *,
    balance_lookup: BalanceLookup | None,
) -> dict[str, dict[str, Any]]:
    if balance_lookup is not None:
        return _normalise_balance_map(balance_lookup(addresses, asset, result), asset)
    mode = str((result.get("engine") or {}).get("mode") or "fixture").lower()
    chain = dict(result.get("chain") or {})
    family = str(chain.get("family") or result.get("chain_family") or "TRON").upper()
    if mode != "live" or family != "TRON" or str(asset.get("symbol") or "").upper() != "USDT":
        return {}
    try:
        from engine.adapters import tron
    except ImportError:
        return {}
    if not tron.configured():
        return {}
    balances: dict[str, dict[str, Any]] = {}
    contract = str(asset.get("contract") or tron.TRON_MAINNET_USDT)
    for address in sorted(addresses):
        if not tron.matches(address):
            continue
        try:
            record = tron.fetch_trc20_balance(address, contract_address=contract)
        except (tron.ProviderConfigurationError, tron.ProviderResponseError) as exc:
            balances[_address_key(address)] = _unavailable_balance(
                asset,
                reason=getattr(exc, "error_kind", exc.__class__.__name__),
            )
            continue
        balances[_address_key(address)] = _available_balance(
            record.get("amount_base"),
            asset,
            source="trongrid",
            retrieval_ts_ms=record.get("retrieval_ts_ms"),
            raw_sha256=record.get("raw_sha256"),
            provider_request_ref=record.get("provider_request_ref"),
        )
    return balances


def _normalise_balance_map(
    values: Mapping[str, Any],
    asset: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    balances: dict[str, dict[str, Any]] = {}
    for address, value in values.items():
        if isinstance(value, dict) and value.get("available") is False:
            balances[_address_key(str(address))] = _unavailable_balance(
                asset,
                reason=str(value.get("reason") or "provider_unavailable"),
            )
            continue
        if isinstance(value, dict):
            amount = _first_present(
                value.get("amount_base"),
                value.get("value"),
                value.get("balance_base"),
                value.get("balance"),
            )
            balances[_address_key(str(address))] = _available_balance(
                amount,
                asset,
                source=str(value.get("source") or "test"),
                retrieval_ts_ms=value.get("retrieval_ts_ms"),
                raw_sha256=value.get("raw_sha256"),
                provider_request_ref=value.get("provider_request_ref"),
            )
        else:
            balances[_address_key(str(address))] = _available_balance(
                value,
                asset,
                source="test",
                retrieval_ts_ms=None,
                raw_sha256=None,
                provider_request_ref=None,
            )
    return balances


def _balance_for(
    balances: Mapping[str, dict[str, Any]],
    address: str,
    asset: dict[str, Any],
) -> dict[str, Any]:
    return dict(balances.get(_address_key(address)) or _unavailable_balance(asset))


def _available_balance(
    amount_base: Any,
    asset: dict[str, Any],
    *,
    source: str,
    retrieval_ts_ms: Any,
    raw_sha256: Any,
    provider_request_ref: Any,
) -> dict[str, Any]:
    amount = _integer_or_zero(amount_base)
    balance = {
        "available": True,
        "value": amount,
        "display": _display_amount(amount, asset["decimals"]),
        "symbol": asset["symbol"],
        "decimals": asset["decimals"],
        "source": source,
    }
    if retrieval_ts_ms is not None:
        balance["retrieval_ts_ms"] = retrieval_ts_ms
    if raw_sha256 is not None:
        balance["raw_sha256"] = raw_sha256
    if provider_request_ref is not None:
        balance["provider_request_ref"] = provider_request_ref
    return balance


def _unavailable_balance(asset: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    balance = {
        "available": False,
        "symbol": asset["symbol"],
        "decimals": asset["decimals"],
    }
    if reason:
        balance["reason"] = reason
    return balance


def _integer_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_txid(item: dict[str, Any]) -> str | None:
    txids = item.get("txids")
    if isinstance(txids, list) and txids:
        return str(txids[0])
    if item.get("txid") is not None:
        return str(item.get("txid"))
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _address_key(value: str) -> str:
    return value.strip().lower()
