from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from app.engine_bridge import AssetRef, ChainRef, TraceParams, TraceResult, TraceSeed
from app.services.time import now_ms


class EvmTraceUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvmResolution:
    chain: ChainRef
    address: str
    txid: str | None
    amount_base: int | None
    ts_ms: int | None
    asset: AssetRef
    provider: str


def resolve_transaction(txid: str) -> list[EvmResolution]:
    from app.services.feature_flags import feature_flags
    from engine.adapters import evm

    if not feature_flags()["live_evm_provider"]["enabled"]:
        return []

    matches: list[EvmResolution] = []
    for chain in evm.configured_chains():
        tx = evm.resolve_transaction(txid, chain=chain)
        if not tx:
            continue
        if not (tx.get("status") or {}).get("confirmed"):
            continue
        for output in tx.get("vout") or []:
            address = output.get("scriptpubkey_address")
            amount = output.get("value")
            block_time = (tx.get("status") or {}).get("block_time")
            if not address or amount is None or block_time is None:
                continue
            matches.append(
                EvmResolution(
                    chain=chain,
                    address=str(address),
                    txid=str(tx.get("txid") or txid),
                    amount_base=int(amount),
                    ts_ms=int(block_time) * 1000,
                    asset=_asset_ref_from_output(output),
                    provider="etherscan-v2",
                )
            )
    return matches


def run_trace(seed: TraceSeed, params: TraceParams, *, family: str, case: dict) -> TraceResult:
    from app.services.feature_flags import feature_flags

    flags = feature_flags()
    provider_flag = flags["live_evm_provider"]
    trace_flag = flags["live_evm_trace"]
    if not provider_flag["enabled"]:
        return _closed_result(
            seed,
            terminal={
                "kind": "unsupported_chain",
                "family": family,
                "note": provider_flag["blocked_reason"]
                or "EVM tracing remains unavailable until provider gates pass.",
            },
        )
    from engine.adapters import evm

    terminal = _validate_seed(seed, family)
    if terminal:
        return _closed_result(seed, terminal=terminal)

    chain = seed.chain or ChainRef("EVM", "ethereum", 1)
    asset_id = _asset_id(seed.asset)
    try:
        seed_tx = _resolve_seed_transaction(seed, chain=chain, asset_id=asset_id)
        if seed_tx is None:
            return _closed_result(
                seed,
                terminal={
                    "kind": "seed_mismatch",
                    "reason": "confirmed_transfer_not_found",
                    "family": family,
                    "note": "No confirmed EVM transfer matched the supplied seed fields.",
                },
            )
        seed_transfer = evm.normalise(seed_tx["tx"], output_index=seed_tx["output_index"])
        transfers = _fetch_transfers(seed.address, chain=chain, asset_id=asset_id)
    except (evm.ProviderConfigurationError, evm.ProviderResponseError) as exc:
        return _closed_result(
            seed,
            terminal={
                "kind": "provider_error",
                "reason": getattr(exc, "error_kind", "provider_configuration"),
                "family": family,
                "note": str(exc),
            },
            status="failed",
        )

    amount_base = int(seed.amount_base)
    payment_txid = str(seed_transfer["txid"])
    payment_ts_ms = int(seed_transfer["ts_ms"])
    try:
        trace_state = _bounded_trace_state(
            seed=seed,
            params=params,
            chain=chain,
            asset_id=asset_id,
            seed_transfers=transfers,
            payment_ts_ms=payment_ts_ms,
            amount_base=amount_base,
        )
    except (evm.ProviderConfigurationError, evm.ProviderResponseError) as exc:
        return _closed_result(
            seed,
            terminal={
                "kind": "provider_error",
                "reason": getattr(exc, "error_kind", "provider_configuration"),
                "family": family,
                "note": str(exc),
            },
            status="failed",
        )

    queued_frontier = trace_state["queued_frontier"]
    deferred_frontier = len(trace_state["parked_events"])
    stationary_amount_base = trace_state["stationary_amount_base"]
    terminal_kind = (
        "depth_exhausted"
        if queued_frontier or deferred_frontier
        else "stationary_funds"
        if stationary_amount_base > 0
        else "trail_dissipated"
    )
    terminal = {
        "kind": terminal_kind,
        "reason": "live_evm_seed_verified"
        if not trace_flag["enabled"]
        else "live_evm_trace_bounded",
        "family": family,
        "note": (
            "The EVM seed transfer was verified through the configured provider; "
            "custody attribution remains disabled."
        ),
        "verified_seed_txid": payment_txid,
        "verified_amount_base": amount_base,
        "provider": "etherscan-v2",
        "addresses_queried": trace_state["addresses_queried"],
        "outgoing_observed": trace_state["outgoing_observed"],
        "scheduled_frontier": queued_frontier,
        "deferred_frontier": deferred_frontier,
        "stationary_amount_base": stationary_amount_base,
        "trace_window_end_ms": payment_ts_ms + params.time_window_hours * 60 * 60 * 1000,
        "custody_evaluation": "disabled_without_provider_attribution",
    }
    closed_ts = now_ms()
    events = [
        {
            "type": "source",
            "data": {
                "name": "etherscan-v2",
                "status": "verified",
                "detail": "Credentialed EVM seed-event lookup and bounded multi-hop transfer projection.",
            },
        },
        {
            "type": "work",
            "data": {
                "message": "Verified supplied EVM seed transfer",
                "txid": payment_txid,
                "asset_id": asset_id or (seed.asset.symbol if seed.asset else "ETH"),
            },
        },
        *trace_state["work_events"],
        *trace_state["hop_events"],
        *trace_state["parked_events"],
        {
            "type": "stats",
            "data": {
                "addresses_visited": trace_state["addresses_queried"],
                "transfers_read": trace_state["transfers_read"],
                "retained_share_bp": _share_bp(stationary_amount_base, amount_base),
                "branches_parked": deferred_frontier,
                "frontier_queued": queued_frontier,
                "expanded_hops": trace_state["expanded_hops"],
            },
        },
        {"type": "terminal", "data": terminal},
        {"type": "done", "data": {"snapshot_id": None, "sha256": None, "closed_ts": closed_ts}},
    ]
    return TraceResult(
        status="incomplete",
        case_stage="trace_incomplete" if terminal_kind != "stationary_funds" else "stationary_observed",
        chain={
            "family": "EVM",
            "network": chain.network,
            "chain_id": chain.chain_id,
        },
        asset=_asset_json(seed.asset),
        terminal=terminal,
        events=events,
        started_ts_ms=payment_ts_ms,
        closed_ts_ms=closed_ts,
        creates_finding=False,
        seed_match={
            "matched": True,
            "txid": payment_txid,
            "ts_ms": payment_ts_ms,
            "amount_base": amount_base,
            "provider": "etherscan-v2",
            "resolution": "transaction_hash" if seed.payment_txid is not None else "address_tuple",
        },
    )


def _bounded_trace_state(
    *,
    seed: TraceSeed,
    params: TraceParams,
    chain: ChainRef,
    asset_id: str | None,
    seed_transfers: list[dict],
    payment_ts_ms: int,
    amount_base: int,
) -> dict:
    from engine.adapters import evm

    trace_window_end_ms = payment_ts_ms + params.time_window_hours * 60 * 60 * 1000
    work_events: list[dict] = []
    hop_events: list[dict] = []
    parked_events: list[dict] = []
    pending: list[dict] = []
    root_visited = {seed.address.lower()}
    hops_seen = 0
    addresses_queried = 1
    transfers_read = len(seed_transfers)
    stationary_total = 0
    observed_outgoing = 0

    root_outgoing = _outgoing_rows(
        seed_transfers,
        source_address=seed.address,
        min_ts_ms=payment_ts_ms,
        max_ts_ms=trace_window_end_ms,
    )
    root_edges, root_stationary = _attribute_edges(root_outgoing, amount_base=amount_base)
    stationary_total += root_stationary
    observed_outgoing += len(root_outgoing)
    root_scheduled, root_deferred = _schedule_edges(
        root_edges,
        params=params,
        amount_base=amount_base,
        next_depth=1,
        visited_addresses=root_visited,
        time_window_end_ms=trace_window_end_ms,
        address_budget_remaining=max(0, params.address_budget - addresses_queried),
    )
    pending.extend(
        _prepare_child_edges(root_scheduled, depth=1, visited_addresses=root_visited)
    )
    parked_events.extend(
        {"type": "parked", "data": _parked_event(edge)}
        for edge in _prepare_child_edges(root_deferred, depth=1, visited_addresses=root_visited)
    )

    while pending:
        pending = _rank_pending(pending, strategy=params.strategy)
        pending_view = [
            {
                "rank": index + 1,
                "event_ref": item.get("stable_id"),
                "destination": item.get("destination"),
                "attributed_base": _int_or_zero(item.get("attributed_base")),
                "depth": int(item.get("depth") or 0),
            }
            for index, item in enumerate(pending)
        ]
        edge = pending.pop(0)
        edge["global_pending_decision"] = {
            "strategy": params.strategy,
            "selected_rank": 1,
            "pending_count": len(pending_view),
            "ranking": pending_view,
        }
        depth = int(edge.get("depth") or 1)
        destination = str(edge.get("destination") or "")
        if not destination:
            edge["deferral_reason"] = "address_budget"
            parked_events.append({"type": "parked", "data": _parked_event(edge)})
            continue

        if depth >= params.max_depth:
            edge["frontier_state"] = "queued"
            hops_seen += 1
            hop_events.append(
                {
                    "type": "hop",
                    "data": _hop_event(
                        edge,
                        rank=hops_seen,
                        seed_address=str(edge.get("source") or ""),
                        seed_amount_base=amount_base,
                    ),
                }
            )
            continue

        if addresses_queried >= params.address_budget:
            edge["deferral_reason"] = "address_budget"
            parked_events.append({"type": "parked", "data": _parked_event(edge)})
            continue

        edge["frontier_state"] = "expanded"
        hops_seen += 1
        hop_events.append(
            {
                "type": "hop",
                "data": _hop_event(
                    edge,
                    rank=hops_seen,
                    seed_address=str(edge.get("source") or ""),
                    seed_amount_base=amount_base,
                ),
            }
        )
        edge_ts = edge.get("ts_ms")
        min_ts_ms = int(edge_ts) if edge_ts is not None else payment_ts_ms
        work_events.append(
            {
                "type": "work",
                "data": {
                    "message": "Expanding live EVM frontier address",
                    "address": destination,
                    "depth": depth,
                    "min_timestamp": min_ts_ms,
                },
            }
        )
        try:
            child_transfers = _fetch_transfers(destination, chain=chain, asset_id=asset_id)
        except evm.ProviderResponseError as exc:
            retry_after_ms = getattr(exc, "retry_after_ms", None)
            retry_delay_ms = max(
                60_000,
                int(retry_after_ms) if retry_after_ms is not None else 0,
            )
            edge["frontier_state"] = "deferred"
            edge["deferral_reason"] = "provider_backoff"
            edge["provider"] = "etherscan-v2"
            edge["retry_attempts"] = 1
            edge["provider_retry_after_ms"] = retry_after_ms
            edge["next_retry_ts_ms"] = now_ms() + retry_delay_ms
            hop_events[-1]["data"]["frontier_state"] = "deferred"
            hop_events[-1]["data"]["deferral_reason"] = "provider_backoff"
            hop_events[-1]["data"]["next_retry_ts_ms"] = edge["next_retry_ts_ms"]
            parked_events.append({"type": "parked", "data": _parked_event(edge)})
            continue
        transfers_read += len(child_transfers)
        addresses_queried += 1
        child_outgoing = _outgoing_rows(
            child_transfers,
            source_address=destination,
            min_ts_ms=min_ts_ms,
            max_ts_ms=trace_window_end_ms,
        )
        observed_outgoing += len(child_outgoing)
        child_amount_base = _int_or_zero(edge.get("attributed_base"))
        child_edges, child_stationary = _attribute_edges(
            child_outgoing,
            amount_base=child_amount_base,
        )
        stationary_total += child_stationary
        child_visited = set(edge.get("_visited_addresses") or root_visited)
        child_visited.add(destination.lower())
        child_scheduled, child_deferred = _schedule_edges(
            child_edges,
            params=params,
            amount_base=child_amount_base,
            next_depth=depth + 1,
            visited_addresses=child_visited,
            time_window_end_ms=trace_window_end_ms,
            address_budget_remaining=max(0, params.address_budget - addresses_queried),
        )
        pending.extend(
            _prepare_child_edges(
                child_scheduled,
                depth=depth + 1,
                visited_addresses=child_visited,
            )
        )
        parked_events.extend(
            {"type": "parked", "data": _parked_event(row)}
            for row in _prepare_child_edges(
                child_deferred,
                depth=depth + 1,
                visited_addresses=child_visited,
            )
        )

    queued_frontier = sum(
        1
        for event in hop_events
        if event["data"].get("frontier_state") == "queued"
    )
    expanded_hops = sum(
        1
        for event in hop_events
        if event["data"].get("frontier_state") == "expanded"
    )
    return {
        "work_events": work_events,
        "hop_events": hop_events,
        "parked_events": parked_events,
        "addresses_queried": addresses_queried,
        "transfers_read": transfers_read,
        "stationary_amount_base": stationary_total,
        "outgoing_observed": observed_outgoing,
        "queued_frontier": queued_frontier,
        "expanded_hops": expanded_hops,
    }


def _fetch_transfers(address: str, *, chain: ChainRef, asset_id: str | None) -> list[dict]:
    from engine.adapters import evm

    rows = evm.fetch_address_transactions(
        address,
        chain=chain,
        asset_id=asset_id,
        native=asset_id is None or not str(asset_id).upper().startswith("ERC20:"),
        token=asset_id is None or str(asset_id).upper().startswith("ERC20:"),
    )
    return [
        evm.normalise(row, output_index=index)
        for row in rows
        for index, _output in enumerate(row.get("vout") or [])
    ]


def _resolve_seed_transaction(seed: TraceSeed, *, chain: ChainRef, asset_id: str | None) -> dict | None:
    from engine.adapters import evm

    rows = []
    if seed.payment_txid:
        tx = evm.resolve_transaction(seed.payment_txid, chain=chain)
        rows = [tx] if tx else []
    else:
        rows = evm.fetch_address_transactions(
            seed.address,
            chain=chain,
            asset_id=asset_id,
            native=asset_id is None or not str(asset_id).upper().startswith("ERC20:"),
            token=asset_id is None or str(asset_id).upper().startswith("ERC20:"),
        )
    for row in rows:
        if not row:
            continue
        for index, _output in enumerate(row.get("vout") or []):
            transfer = evm.normalise(row, output_index=index)
            if (
                transfer["destination"].lower() == seed.address.lower()
                and int(transfer["amount_base"]) == int(seed.amount_base)
                and (
                    seed.payment_txid is None
                    or str(transfer["txid"]).lower() == _canonical_txid(seed.payment_txid).lower()
                )
                and (
                    seed.payment_ts_ms is None
                    or int(transfer["ts_ms"]) == int(seed.payment_ts_ms)
                )
            ):
                return {"tx": row, "output_index": index}
    return None


def _validate_seed(seed: TraceSeed, family: str) -> dict | None:
    if seed.chain and seed.chain.family != "EVM":
        return {
            "kind": "unsupported_chain",
            "family": family,
            "note": "Live EVM tracing requires an EVM chain seed.",
        }
    if seed.amount_base is None:
        return {
            "kind": "invalid_seed",
            "reason": "amount_required",
            "family": family,
            "note": "Live EVM seed verification requires the payment amount in base units.",
        }
    if seed.amount_base <= 0:
        return {
            "kind": "invalid_seed",
            "reason": "amount",
            "family": family,
            "supplied_amount_base": seed.amount_base,
            "note": "Seed amount must be a positive integer base-unit value.",
        }
    if seed.payment_ts_ms is None and not seed.payment_txid:
        return {
            "kind": "invalid_seed",
            "reason": "timestamp_required",
            "family": family,
            "note": "An EVM address seed requires the confirmed payment timestamp.",
        }
    if seed.payment_ts_ms is not None and seed.payment_ts_ms <= 0:
        return {
            "kind": "invalid_seed",
            "reason": "timestamp",
            "family": family,
            "supplied_payment_ts_ms": seed.payment_ts_ms,
            "note": "Seed timestamp must be a positive UTC epoch millisecond value.",
        }
    return None


def _closed_result(
    seed: TraceSeed,
    *,
    terminal: dict,
    status: str | None = None,
) -> TraceResult:
    closed_ts = now_ms()
    result_status = status or ("unsupported" if terminal.get("kind") == "unsupported_chain" else "failed")
    return TraceResult(
        status=result_status,
        case_stage="trace_unsupported" if result_status == "unsupported" else "trace_failed",
        chain={
            "family": "EVM",
            "network": seed.chain.network if seed.chain else "unknown",
            "chain_id": seed.chain.chain_id if seed.chain else None,
        },
        asset=_asset_json(seed.asset),
        terminal=terminal,
        events=[
            {
                "type": "source",
                "data": {
                    "name": "evm-engine",
                    "status": "closed",
                    "detail": "EVM trace closed without custody attribution.",
                },
            },
            {"type": "terminal", "data": terminal},
            {"type": "done", "data": {"snapshot_id": None, "sha256": None, "closed_ts": closed_ts}},
        ],
        started_ts_ms=seed.payment_ts_ms if seed.payment_ts_ms is not None else closed_ts,
        closed_ts_ms=closed_ts,
        creates_finding=False,
        seed_match={
            "matched": False,
            "reason": terminal.get("reason") or terminal.get("kind"),
            "txid": seed.payment_txid,
        },
    )


def _prepare_child_edges(
    edges: list[dict],
    *,
    depth: int,
    visited_addresses: set[str],
) -> list[dict]:
    prepared: list[dict] = []
    for edge in edges:
        item = dict(edge)
        item["depth"] = depth
        item["_visited_addresses"] = set(visited_addresses)
        prepared.append(item)
    return prepared


def _rank_pending(
    pending: list[dict],
    *,
    strategy: str,
) -> list[dict]:
    if strategy == "value_weighted":
        return sorted(
            pending,
            key=lambda edge: (
                -_int_or_zero(edge.get("attributed_base")),
                int(edge.get("depth") or 0),
                int(edge.get("event_order") or 0),
                str(edge.get("stable_id") or ""),
            ),
        )
    return sorted(
        pending,
        key=lambda edge: (
            int(edge.get("depth") or 0),
            -_int_or_zero(edge.get("attributed_base")),
            int(edge.get("event_order") or 0),
            str(edge.get("stable_id") or ""),
        ),
    )


def _outgoing_rows(
    transfers: list[dict],
    *,
    source_address: str,
    min_ts_ms: int,
    max_ts_ms: int | None = None,
) -> list[dict]:
    return sorted(
        [
            row
            for row in transfers
            if str(row.get("source", "")).lower() == source_address.lower()
            and _int_or_zero(row.get("ts_ms")) >= min_ts_ms
            and (max_ts_ms is None or _int_or_zero(row.get("ts_ms")) <= max_ts_ms)
        ],
        key=lambda row: (
            _int_or_zero(row.get("ts_ms")),
            str(row.get("txid") or ""),
            _int_or_zero(row.get("event_index")),
        ),
    )


def _attribute_edges(outgoing: list[dict], *, amount_base: int) -> tuple[list[dict], int]:
    from app.services.allocation import allocate_ordered_outgoing

    allocation = allocate_ordered_outgoing(
        (max(0, int(row.get("amount_base") or 0)) for row in outgoing),
        attributed_base=amount_base,
    )
    edges = []
    for index, (row, step) in enumerate(zip(outgoing, allocation.steps)):
        edge = dict(row)
        edge["event_order"] = index
        edge["stable_id"] = _edge_ref(edge, index)
        edge["attributed_base"] = step.result.outgoing_attributed_base
        edge["residual_numerator"] = step.result.residual_numerator
        edge["allocation"] = {
            "policy": "integer_proportional",
            "version": "trinetra.allocation/1",
            "incoming_attributed_base": step.attributed_before,
            "observed_outgoing_base": step.outgoing_base,
            "denominator_base": step.balance_before,
            "numerator_base": step.numerator_base,
            "outgoing_attributed_base": step.result.outgoing_attributed_base,
            "residual_before": step.residual_before,
            "residual_numerator": step.result.residual_numerator,
            "remaining_balance_base": step.result.remaining_balance_base,
            "remaining_attributed_base": step.result.remaining_attributed_base,
            "initial_balance_basis": {
                "incoming_attributed_base": amount_base,
                "observed_outgoing_total_base": allocation.outgoing_total_base,
                "denominator_base": allocation.initial_balance_base,
            },
        }
        edges.append(edge)
    return edges, allocation.remaining_attributed_base


def _schedule_edges(
    edges: list[dict],
    *,
    params: TraceParams,
    amount_base: int,
    next_depth: int = 1,
    visited_addresses: set[str] | None = None,
    time_window_end_ms: int | None = None,
    address_budget_remaining: int | None = None,
) -> tuple[list[dict], list[dict]]:
    if not edges:
        return [], []

    visited = {address.lower() for address in visited_addresses or set()}
    budget_remaining = (
        params.address_budget if address_budget_remaining is None else address_budget_remaining
    )
    value_floor_base = int(
        (Decimal(amount_base) * params.value_floor_share).to_integral_value(rounding=ROUND_FLOOR)
    )
    immediate_deferrals: dict[str, str] = {}
    candidates = []
    for edge in edges:
        attributed = int(edge.get("attributed_base") or 0)
        destination = str(edge.get("destination") or "")
        edge_ts = edge.get("ts_ms")
        if next_depth > params.max_depth:
            immediate_deferrals[edge["stable_id"]] = "depth_budget"
        elif destination and destination.lower() in visited:
            immediate_deferrals[edge["stable_id"]] = "cycle_detected"
        elif (
            time_window_end_ms is not None
            and edge_ts is not None
            and int(edge_ts) > time_window_end_ms
        ):
            immediate_deferrals[edge["stable_id"]] = "time_window"
        elif attributed <= 0 or attributed < value_floor_base:
            immediate_deferrals[edge["stable_id"]] = "value_floor"
        else:
            candidates.append(edge)
    ranked_candidates = _rank_evm_candidates(candidates, strategy=params.strategy)
    scheduled_limit = min(params.breadth_cap, max(0, budget_remaining), len(ranked_candidates))
    scheduled_ids = {edge["stable_id"] for edge in ranked_candidates[:scheduled_limit]}
    breadth_deferred_ids = {edge["stable_id"] for edge in ranked_candidates[scheduled_limit:]}
    queued = []
    deferred = []
    eligible_rank = {
        candidate["stable_id"]: index + 1
        for index, candidate in enumerate(ranked_candidates)
    }
    for edge in edges:
        item = dict(edge)
        if item["stable_id"] in immediate_deferrals:
            item["deferral_reason"] = immediate_deferrals[item["stable_id"]]
            deferred.append(item)
        elif item["stable_id"] in scheduled_ids:
            item["frontier_state"] = "queued"
            queued.append(item)
        elif item["stable_id"] in breadth_deferred_ids:
            item["deferral_reason"] = "address_budget" if budget_remaining <= 0 else "breadth_cap"
            deferred.append(item)
        elif budget_remaining <= 0 and _int_or_zero(item.get("attributed_base")) > 0:
            item["deferral_reason"] = "address_budget"
            deferred.append(item)
        else:
            item["deferral_reason"] = "address_budget"
            deferred.append(item)
        item["scheduling"] = {
            "strategy": params.strategy,
            "local_candidate_rank": eligible_rank.get(item["stable_id"]),
            "eligible_candidate_count": len(candidates),
            "breadth_cap": params.breadth_cap,
            "address_budget_remaining": budget_remaining,
            "decision": item.get("frontier_state") or "deferred",
            "deferral_reason": item.get("deferral_reason"),
        }
    return queued, deferred


def _rank_evm_candidates(candidates: list[dict], *, strategy: str) -> list[dict]:
    if strategy == "value_weighted":
        return sorted(
            candidates,
            key=lambda edge: (
                -_int_or_zero(edge.get("attributed_base")),
                _int_or_zero(edge.get("event_order")),
                str(edge.get("stable_id") or ""),
            ),
        )
    return sorted(
        candidates,
        key=lambda edge: (
            _int_or_zero(edge.get("event_order")),
            -_int_or_zero(edge.get("attributed_base")),
            str(edge.get("stable_id") or ""),
        ),
    )


def _hop_event(edge: dict, rank: int, seed_address: str, seed_amount_base: int) -> dict:
    attributed = _int_or_zero(edge.get("attributed_base"))
    txid = edge.get("txid")
    return {
        "hop": int(edge.get("depth") or rank),
        "trace_rank": rank,
        "address": edge.get("destination"),
        "source_address": seed_address,
        "value_base": attributed,
        "observed_amount_base": _int_or_zero(edge.get("amount_base")),
        "share_bp": _share_bp(attributed, seed_amount_base),
        "ts_ms": edge.get("ts_ms"),
        "event_index": edge.get("event_index"),
        "block": edge.get("block"),
        "txids": [txid] if txid is not None else [],
        "class": "live_frontier_candidate",
        "band": "unscored",
        "typology": "observed_outgoing_transfer",
        "classification": "frontier",
        "event_ref": edge["stable_id"],
        "frontier_state": edge.get("frontier_state") or "queued",
        "allocation": dict(edge.get("allocation") or {
            "policy": "integer_proportional",
            "residual_numerator": edge.get("residual_numerator", 0),
        }),
        "scheduling": dict(edge.get("scheduling") or {}),
        "global_pending_decision": dict(edge.get("global_pending_decision") or {}),
    }


def _parked_event(edge: dict) -> dict:
    return {
        "branch_id": edge["stable_id"],
        "address": edge.get("destination"),
        "source_address": edge.get("source"),
        "from_hop": max(0, int(edge.get("depth") or 1) - 1),
        "value_base": _int_or_zero(edge.get("attributed_base")),
        "observed_amount_base": _int_or_zero(edge.get("amount_base")),
        "txid": edge.get("txid"),
        "ts_ms": edge.get("ts_ms"),
        "event_index": edge.get("event_index"),
        "block": edge.get("block"),
        "reason": edge.get("deferral_reason"),
        "deferral_reason": edge.get("deferral_reason"),
        "next_retry_ts_ms": edge.get("next_retry_ts_ms"),
        "retry_attempts": edge.get("retry_attempts"),
        "provider_retry_after_ms": edge.get("provider_retry_after_ms"),
        "provider": edge.get("provider"),
        "event_ref": edge["stable_id"],
        "allocation": dict(edge.get("allocation") or {
            "policy": "integer_proportional",
            "residual_numerator": edge.get("residual_numerator", 0),
        }),
        "scheduling": dict(edge.get("scheduling") or {}),
    }


def _asset_ref_from_output(output: dict) -> AssetRef:
    asset_id = output.get("asset_id")
    contract = None
    if asset_id and str(asset_id).upper().startswith("ERC20:"):
        contract = str(asset_id).split(":", 1)[1]
    return AssetRef(
        str(output.get("token") or "ETH"),
        contract,
        int(output.get("decimals") or 18),
    )


def _asset_id(asset: AssetRef | None) -> str | None:
    if asset is None:
        return None
    if asset.contract:
        return f"ERC20:{asset.contract.lower()}"
    return asset.symbol.upper()


def _asset_json(asset: AssetRef | None) -> dict:
    if asset is None:
        return {"symbol": "unknown", "contract": None, "decimals": 0}
    return {"symbol": asset.symbol, "contract": asset.contract, "decimals": asset.decimals}


def _edge_ref(edge: dict, index: int) -> str:
    txid = edge.get("txid")
    suffix = edge.get("event_index") if edge.get("event_index") is not None else index
    return f"live:evm:{txid or 'unknown'}:{suffix}:{index}"


def _canonical_txid(txid: str) -> str:
    text = str(txid).strip()
    return text if text.lower().startswith("0x") else f"0x{text}"


def _int_or_zero(value: object) -> int:
    return 0 if value is None else int(value)


def _share_bp(part: int, whole: int) -> int:
    if whole <= 0:
        return 0
    return max(0, min(10000, (int(part) * 10000) // int(whole)))
