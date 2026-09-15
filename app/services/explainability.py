from __future__ import annotations

from typing import Any, Iterable

from app.engine_bridge import CASE_STAGE_BY_TERMINAL, case_stage_for_terminal, terminal_creates_finding
from app.services.money import format_amount


EXPLAINABILITY_SCHEMA = "trinetra.trace_explainability/1"
METHODOLOGY_SCHEMA = "trinetra.methodology_annex/1"
ALLOCATION_APPROXIMATION = (
    "The synchronous live helper uses max(incoming attributed amount, observed outgoing total) "
    "as its initial allocation denominator. This is a bounded approximation, not full historical "
    "balance reconstruction."
)


def methodology_annex(snapshot: dict[str, Any]) -> dict[str, Any]:
    params = dict(snapshot.get("params") or {})
    terminal = dict(snapshot.get("terminal") or {})
    mode = str((snapshot.get("engine") or {}).get("mode") or "fixture")
    strategy = str(params.get("strategy") or "dominant_fund_flow")
    return {
        "schema": METHODOLOGY_SCHEMA,
        "trace_mode": mode,
        "strategy": {
            "selected": strategy,
            "dominant_fund_flow": (
                "At each scheduling decision, expand the eligible edge with the largest attributed "
                "integer value; retain every other observed edge with its deferral reason."
            ),
            "value_weighted": (
                "Rank all pending eligible work by attributed integer value, then depth, event order "
                "and stable evidence identity; expand up to the configured breadth and address bounds."
            ),
        },
        "allocation": {
            "version": "trinetra.allocation/1",
            "rule": (
                "outgoing_attributed_base = "
                "(outgoing_base * incoming_attributed_base + carried_residual) // balance_base"
            ),
            "residual": (
                "The integer remainder is carried into the next ordered outgoing allocation; value "
                "is never created by rounding."
            ),
            "known_approximation": ALLOCATION_APPROXIMATION,
        },
        "bounds": {
            key: value
            for key, value in params.items()
            if key not in {"trace_mode", "include_unconfirmed"}
        },
        "terminal": {
            "kind": terminal.get("kind"),
            "case_stage": case_stage_for_terminal(terminal),
            "finding_permitted": terminal_creates_finding(terminal),
            "note": terminal.get("note"),
            "stronger_outcome_requires": _stronger_outcome_requirement(terminal),
        },
        "terminal_taxonomy": [
            {"terminal_kind": kind, "case_stage": stage}
            for kind, stage in sorted(CASE_STAGE_BY_TERMINAL.items())
        ],
        "limits": [
            "Live frontier addresses are unscored observed outgoing transfers only.",
            "Behavioural similarity is not custody attribution, account identity, intent or participation.",
            "The fixture registry is controlled demonstration data; it is not a live authoritative registry.",
            "Probabilities are disabled pending independent labelled data and calibration.",
            "An unsupported, failed or incomplete trace cannot create a custody finding.",
            "Unconfirmed observations are excluded from attribution, canonical evidence and terminal decisions.",
            "No adverse findings in available sources is not an assertion of legitimacy.",
        ],
    }


def methodology_annex_text(snapshot: dict[str, Any]) -> str:
    annex = methodology_annex(snapshot)
    bounds = annex["bounds"]
    bound_lines = [f"- {key}: {value}" for key, value in bounds.items()]
    terminal = annex["terminal"]
    lines = [
        "TRINETRA methodology annex",
        "",
        f"Trace mode: {annex['trace_mode']}",
        f"Traversal strategy: {annex['strategy']['selected']}",
        annex["strategy"].get(annex["strategy"]["selected"], "Unknown strategy."),
        "",
        "Integer allocation",
        annex["allocation"]["rule"],
        annex["allocation"]["residual"],
        annex["allocation"]["known_approximation"],
        "",
        "Sealed bounds",
        *(bound_lines or ["- No traversal bounds were recorded."]),
        "",
        "Terminal",
        f"- kind: {terminal['kind']}",
        f"- case stage: {terminal['case_stage']}",
        f"- custody finding permitted: {str(terminal['finding_permitted']).lower()}",
        f"- stronger outcome requires: {terminal['stronger_outcome_requires']}",
        "",
        "What the system does not know",
        *[f"- {item}" for item in annex["limits"]],
        "",
    ]
    return "\n".join(lines)


def trace_explainability(
    snapshot: dict[str, Any],
    *,
    canonical_events: Iterable[Any] = (),
    frontier_items: Iterable[Any] = (),
    source_coverage: Iterable[Any] = (),
) -> dict[str, Any]:
    canonical = [_model_dict(item) for item in canonical_events]
    frontier = [_model_dict(item) for item in frontier_items]
    coverage = [_model_dict(item) for item in source_coverage]
    hops = [dict(item) for item in snapshot.get("hops") or []]
    parked = [dict(item) for item in snapshot.get("parked") or []]
    mode = str((snapshot.get("engine") or {}).get("mode") or "fixture")
    asset = dict(snapshot.get("asset") or {})
    items: list[dict[str, Any]] = []

    items.append(_seed_explanation(snapshot, mode=mode, asset=asset, coverage=coverage))
    edges = [
        *[("hop", index, item) for index, item in enumerate(hops)],
        *[("parked", index, item) for index, item in enumerate(parked)],
    ]
    for kind, index, edge in edges:
        items.append(
            _edge_explanation(
                snapshot,
                edge,
                item_id=f"{kind}:{index}",
                kind=kind,
                mode=mode,
                asset=asset,
                all_edges=[item for _, _, item in edges],
                canonical=canonical,
                frontier=frontier,
            )
        )
    items.append(_terminal_explanation(snapshot, asset=asset, coverage=coverage))
    annex = methodology_annex(snapshot)
    items.append(_methodology_explanation(snapshot, annex))
    return {
        "schema": EXPLAINABILITY_SCHEMA,
        "default_register": "investigator",
        "registers": {
            "investigator": "Plain-language investigative record",
            "technical": "Integer arithmetic, scheduling and evidence identity",
        },
        "items": items,
        "methodology_annex": annex,
    }


def _seed_explanation(
    snapshot: dict[str, Any],
    *,
    mode: str,
    asset: dict[str, Any],
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    case = dict(snapshot.get("case") or {})
    seed = dict(snapshot.get("seed_match") or {})
    amount = seed.get("amount_base")
    if amount is None:
        amount = case.get("amount_reported_base")
    evidence = {
        "txid": seed.get("txid") if seed.get("txid") is not None else case.get("payment_txid"),
        "event_index": seed.get("event_index"),
        "block": seed.get("block"),
        "retrieval_ts_ms": seed.get("retrieval_ts_ms"),
        "provider": seed.get("provider"),
        "raw_sha256": seed.get("raw_sha256"),
        "source_coverage": _coverage_refs(coverage),
    }
    matched = bool(seed.get("matched"))
    investigator_summary = (
        "The confirmed payment anchor used to begin the trace."
        if matched
        else "The supplied payment anchor was not confirmed; no stronger path claim is made."
    )
    return _item(
        item_id="seed",
        kind="seed",
        title="Reported payment anchor",
        address=str(case.get("reported_address") or ""),
        amount_base=amount,
        asset=asset,
        investigator=[
            _section("Why was this path followed?", investigator_summary),
            _section(
                "Where did this amount come from?",
                "The amount is the complaint-linked transfer amount accepted at intake and checked against the selected mode.",
                {"amount_base": amount, "display_amount": _display_amount(amount, asset)},
            ),
            _section(
                "What is this address, and what is it not?",
                "It is the reported recipient address. That fact alone is not custody attribution or account identity.",
            ),
            _section(
                "Why did the trace end here?",
                "This is the starting anchor, not a terminal decision.",
            ),
            _section("What is this based on?", _evidence_sentence(evidence), evidence),
        ],
        technical=[
            _section(
                "Why was this path followed?",
                f"Seed resolution mode: {mode}; matched: {str(matched).lower()}.",
                seed,
            ),
            _section(
                "Where did this amount come from?",
                "The seed amount enters allocation as integer base units without truthiness fallback.",
                {"amount_base": amount, "display_amount": _display_amount(amount, asset)},
            ),
            _section(
                "What is this address, and what is it not?",
                "Input role: reported recipient. No behavioural or custody class is inferred from the input role.",
            ),
            _section("Why did the trace end here?", "Not applicable to the seed item."),
            _section("What is this based on?", _evidence_sentence(evidence), evidence),
        ],
    )


def _edge_explanation(
    snapshot: dict[str, Any],
    edge: dict[str, Any],
    *,
    item_id: str,
    kind: str,
    mode: str,
    asset: dict[str, Any],
    all_edges: list[dict[str, Any]],
    canonical: list[dict[str, Any]],
    frontier: list[dict[str, Any]],
) -> dict[str, Any]:
    event_ref = edge.get("event_ref") or edge.get("branch_id")
    evidence = _edge_evidence(edge, canonical)
    frontier_row = next(
        (row for row in frontier if row.get("event_ref") == event_ref),
        None,
    )
    siblings = _siblings(edge, all_edges)
    scheduling = dict(edge.get("scheduling") or {})
    global_pending = dict(edge.get("global_pending_decision") or {})
    selected = kind == "hop"
    reason = edge.get("deferral_reason") or edge.get("reason")
    if selected:
        why = (
            f"This observed transfer was selected for expansion under the "
            f"{str((snapshot.get('params') or {}).get('strategy') or 'dominant_fund_flow').replace('_', ' ')} strategy."
        )
    else:
        why = f"This observed transfer was retained but not expanded. Deferral reason: {reason or 'not recorded'}."
    identity = (
        "Unscored - observed outgoing transfer only. It is not a custody or account-identity assertion."
        if mode == "live"
        else (
            f"Controlled fixture role: {str(edge.get('class') or edge.get('candidate') or 'unattributed').replace('_', ' ')}. "
            "The label is demonstration data, not a live authoritative attribution."
        )
    )
    allocation = _allocation_detail(edge, asset=asset, mode=mode)
    technical_schedule = {
        "local_decision": scheduling or {"status": "not_recorded_in_legacy_snapshot"},
        "pending_work_at_step": global_pending or {"status": "not_recorded_in_legacy_snapshot"},
        "sibling_edges": siblings,
        "persisted_frontier": frontier_row,
    }
    return _item(
        item_id=item_id,
        kind=kind,
        title=(
            f"Hop {edge.get('hop', item_id.split(':')[-1])}"
            if kind == "hop"
            else f"Deferred branch {edge.get('branch_id') or item_id.split(':')[-1]}"
        ),
        address=str(edge.get("address") or edge.get("destination") or ""),
        amount_base=edge.get("value_base"),
        asset=asset,
        investigator=[
            _section(
                "Why was this path followed?",
                why,
                {"decision": "expanded" if selected else "deferred", "siblings": siblings},
            ),
            _section(
                "Where did this attributed amount come from?",
                allocation["plain_language"],
                allocation["values"],
            ),
            _section("What is this address, and what is it not?", identity),
            _section(
                "Why did the trace end here?",
                (
                    f"Expansion was deferred because {reason}."
                    if reason
                    else "This item did not itself end the trace; review the terminal explanation."
                ),
            ),
            _section("What is this based on?", _evidence_sentence(evidence), evidence),
        ],
        technical=[
            _section("Why was this path followed?", _schedule_sentence(technical_schedule), technical_schedule),
            _section(
                "Where did this attributed amount come from?",
                allocation["technical"],
                allocation["values"],
            ),
            _section("What is this address, and what is it not?", identity),
            _section(
                "Why did the trace end here?",
                f"frontier_state={edge.get('frontier_state') or ('deferred' if kind == 'parked' else 'not_recorded')}; deferral_reason={reason}",
            ),
            _section("What is this based on?", _evidence_sentence(evidence), evidence),
        ],
    )


def _terminal_explanation(
    snapshot: dict[str, Any],
    *,
    asset: dict[str, Any],
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    terminal = dict(snapshot.get("terminal") or {})
    finding_permitted = terminal_creates_finding(terminal)
    stage = case_stage_for_terminal(terminal)
    amount = terminal.get("amount_credited_base")
    facts = {
        "terminal_kind": terminal.get("kind"),
        "case_stage": stage,
        "finding_permitted": finding_permitted,
        "amount_base": amount,
        "display_amount": _display_amount(amount, asset),
        "stronger_outcome_requires": _stronger_outcome_requirement(terminal),
        "source_coverage": _coverage_refs(coverage),
    }
    return _item(
        item_id="terminal",
        kind="terminal",
        title="Trace terminal",
        address=str(terminal.get("deposit_address") or ""),
        amount_base=amount,
        asset=asset,
        investigator=[
            _section("Why was this path followed?", "The terminal records the bounded outcome after all selected work and retained deferrals."),
            _section("Where did this attributed amount come from?", "The terminal amount is included only when the terminal evidence permits it.", facts),
            _section(
                "What is this address, and what is it not?",
                "A terminal address has only the role supported by the terminal evidence. A deposit address and a hot wallet remain different objects.",
            ),
            _section(
                "Why did the trace end here?",
                f"Terminal {terminal.get('kind')} maps to case stage {stage}. "
                f"A custody finding is {'permitted' if finding_permitted else 'not permitted'} by this terminal.",
                facts,
            ),
            _section("What is this based on?", str(terminal.get("note") or "No terminal note was recorded."), facts),
        ],
        technical=[
            _section("Why was this path followed?", "Terminal evaluation runs after the persisted event and frontier decisions."),
            _section("Where did this attributed amount come from?", "See terminal amount and per-edge integer allocation records.", facts),
            _section("What is this address, and what is it not?", "Terminal semantics are defined by the typed terminal kind, not by a score."),
            _section("Why did the trace end here?", f"terminal_kind={terminal.get('kind')}; case_stage={stage}; finding_permitted={str(finding_permitted).lower()}", facts),
            _section("What is this based on?", str(terminal.get("note") or "No terminal note was recorded."), facts),
        ],
    )


def _methodology_explanation(snapshot: dict[str, Any], annex: dict[str, Any]) -> dict[str, Any]:
    return _item(
        item_id="methodology",
        kind="methodology",
        title="Case methodology and limits",
        address="",
        amount_base=None,
        asset=dict(snapshot.get("asset") or {}),
        investigator=[
            _section("Why was this path followed?", annex["strategy"].get(annex["strategy"]["selected"], "The selected strategy was not recognised.")),
            _section("Where did this attributed amount come from?", annex["allocation"]["residual"]),
            _section("What is this address, and what is it not?", "Live frontier addresses are unscored observations unless separate evidence supports a role."),
            _section("Why did the trace end here?", str(annex["terminal"]["stronger_outcome_requires"]), annex["terminal"]),
            _section("What is this based on?", "The sealed snapshot parameters, ordered events, evidence identities and terminal taxonomy.", {"bounds": annex["bounds"], "limits": annex["limits"]}),
        ],
        technical=[
            _section("Why was this path followed?", annex["strategy"].get(annex["strategy"]["selected"], "The selected strategy was not recognised."), annex["strategy"]),
            _section("Where did this attributed amount come from?", annex["allocation"]["known_approximation"], annex["allocation"]),
            _section("What is this address, and what is it not?", "Classification fields do not enter integer attribution arithmetic."),
            _section("Why did the trace end here?", "The typed terminal maps to the recorded case stage and finding gate.", annex["terminal"]),
            _section("What is this based on?", "Versioned methodology and the snapshot's sealed bounds.", {"bounds": annex["bounds"], "terminal_taxonomy": annex["terminal_taxonomy"], "limits": annex["limits"]}),
        ],
    )


def _allocation_detail(
    edge: dict[str, Any],
    *,
    asset: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    allocation = dict(edge.get("allocation") or {})
    denominator = allocation.get("denominator_base")
    numerator = allocation.get("numerator_base")
    output = allocation.get("outgoing_attributed_base")
    if denominator is not None and numerator is not None and output is not None:
        formula = f"{numerator} // {denominator} = {output}"
        values = {
            **allocation,
            "formula": formula,
            "display_amount": _display_amount(output, asset),
        }
        return {
            "plain_language": (
                f"Integer proportional allocation carried {_display_amount(output, asset)} to this edge. "
                "Any division remainder was carried into the next ordered edge."
            ),
            "technical": (
                f"{allocation.get('observed_outgoing_base')} * "
                f"{allocation.get('incoming_attributed_base')} + "
                f"{allocation.get('residual_before')} = {numerator}; {formula}; "
                f"carried residual {allocation.get('residual_numerator')}. {ALLOCATION_APPROXIMATION}"
            ),
            "values": values,
        }
    amount = edge.get("value_base")
    return {
        "plain_language": (
            f"The carried amount is {_display_amount(amount, asset)}. "
            + (
                "This controlled fixture snapshot does not represent a live allocation calculation."
                if mode == "fixture"
                else "The complete arithmetic was not recorded in this legacy snapshot."
            )
        ),
        "technical": (
            "allocation_status=controlled_fixture_value"
            if mode == "fixture"
            else "allocation_status=not_recorded_in_legacy_snapshot"
        ),
        "values": {
            "amount_base": amount,
            "display_amount": _display_amount(amount, asset),
            "residual_numerator": allocation.get("residual_numerator"),
        },
    }


def _edge_evidence(edge: dict[str, Any], canonical: list[dict[str, Any]]) -> dict[str, Any]:
    txids = list(edge.get("txids") or [])
    txid = edge.get("txid") if edge.get("txid") is not None else (txids[0] if txids else None)
    event_ref = edge.get("event_ref")
    row = next(
        (
            item
            for item in canonical
            if (event_ref and item.get("evidence_ref") == event_ref)
            or (
                txid
                and item.get("txid") == txid
                and item.get("destination_address") == edge.get("address")
            )
        ),
        {},
    )
    return {
        "evidence_ref": row.get("evidence_ref") or event_ref,
        "txid": row.get("txid") or txid,
        "block_height": row.get("block_height") if row else edge.get("block"),
        "block_hash": row.get("block_hash") if row else edge.get("block_hash"),
        "event_index": row.get("event_index") if row else edge.get("event_index"),
        "observed_ts_ms": row.get("observed_ts_ms") if row else edge.get("ts_ms"),
        "retrieval_ts_ms": edge.get("retrieval_ts_ms"),
        "provider_request_ref": edge.get("provider_request_ref"),
        "raw_sha256": edge.get("raw_sha256") or row.get("raw_sha256"),
        "finality": row.get("finality"),
    }


def _siblings(edge: dict[str, Any], all_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = edge.get("source_address")
    if not source:
        return []
    rows = [item for item in all_edges if item.get("source_address") == source]
    ranked = sorted(
        rows,
        key=lambda item: (
            -int(item.get("value_base") or 0),
            int(item.get("ts_ms") or 0),
            str(item.get("event_ref") or item.get("branch_id") or ""),
        ),
    )
    return [
        {
            "rank": index + 1,
            "event_ref": item.get("event_ref") or item.get("branch_id"),
            "destination": item.get("address"),
            "attributed_base": item.get("value_base"),
            "decision": item.get("frontier_state") or (
                "deferred" if item.get("deferral_reason") or item.get("reason") else "recorded"
            ),
            "deferral_reason": item.get("deferral_reason") or item.get("reason"),
        }
        for index, item in enumerate(ranked)
    ]


def _coverage_refs(coverage: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "provider": row.get("provider"),
            "retrieval_ts_ms": row.get("retrieval_ts_ms"),
            "completeness": row.get("completeness"),
            "request_ref": (row.get("observed_watermark") or {}).get("request_ref"),
            "raw_sha256": (row.get("observed_watermark") or {}).get("raw_sha256"),
        }
        for row in coverage
    ]


def _stronger_outcome_requirement(terminal: dict[str, Any]) -> str:
    kind = str(terminal.get("kind") or "unknown")
    if terminal_creates_finding(terminal):
        return "Officer review of the exact deposit assignment, credited amount and provenance."
    if kind == "unsupported_chain":
        return "A supported, verified chain adapter and canonical event evidence."
    if kind in {"provider_error", "error"}:
        return "A successful provider response with retained raw evidence and schema validation."
    if kind in {"depth_exhausted", "trace_incomplete"}:
        return "Resume or re-run within approved larger bounds, then review any newly reached evidence."
    return (
        "Provider-certified deposit assignment and exact credit evidence; a service label or "
        "behavioural pattern alone is insufficient."
    )


def _schedule_sentence(schedule: dict[str, Any]) -> str:
    pending = schedule.get("pending_work_at_step") or {}
    local = schedule.get("local_decision") or {}
    if pending.get("ranking"):
        return (
            f"Selected pending rank {pending.get('selected_rank')} of {pending.get('pending_count')} "
            f"under {pending.get('strategy')}; local candidate rank "
            f"{local.get('local_candidate_rank')}."
        )
    return "The exact pending-work ranking was not recorded in this legacy snapshot."


def _evidence_sentence(evidence: dict[str, Any]) -> str:
    txid = evidence.get("txid")
    raw_sha = evidence.get("raw_sha256")
    if txid and raw_sha:
        return f"Transaction {txid} with retained evidence hash {raw_sha}."
    if txid:
        return f"Transaction {txid}; a provider raw-evidence hash was not recorded for this item."
    return "No transaction identity was recorded for this item."


def _display_amount(amount: Any, asset: dict[str, Any]) -> str:
    if amount is None:
        return "not recorded"
    return format_amount(
        int(amount),
        int(asset.get("decimals") or 0),
        str(asset.get("symbol") or "base units"),
    )


def _section(title: str, body: str, facts: Any | None = None) -> dict[str, Any]:
    return {"title": title, "body": body, "facts": facts}


def _item(
    *,
    item_id: str,
    kind: str,
    title: str,
    address: str,
    amount_base: Any,
    asset: dict[str, Any],
    investigator: list[dict[str, Any]],
    technical: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": kind,
        "title": title,
        "address": address,
        "amount_base": amount_base,
        "display_amount": _display_amount(amount_base, asset),
        "registers": {
            "investigator": investigator,
            "technical": technical,
        },
    }


def _model_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "model_dump"):
        return dict(item.model_dump(mode="json"))
    return dict(vars(item))
