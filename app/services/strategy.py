from __future__ import annotations

from collections import defaultdict
from typing import Any, get_args, get_type_hints

from app.engine_bridge import TraceParams
from app.services.graph_view import omega_graph_payload


def engine_strategies() -> tuple[str, ...]:
    annotation = get_type_hints(TraceParams).get("strategy")
    values = tuple(str(value) for value in get_args(annotation))
    if not values:
        raise RuntimeError("TraceParams.strategy must remain a Literal contract.")
    return values


def strategy_analysis(snapshot: dict[str, Any], strategy: str) -> dict[str, Any]:
    strategies = engine_strategies()
    if strategy not in strategies:
        raise ValueError("Unknown trace strategy.")
    payload = omega_graph_payload(snapshot)
    graph = _flatten_tree(payload.get("tree") or {})
    outgoing_totals: dict[str, int] = defaultdict(int)
    for edge in graph["edges"]:
        if edge["kind"] != "terminal":
            outgoing_totals[edge["source"]] += edge["amount_base"]

    for edge in graph["edges"]:
        if strategy == "dominant_fund_flow":
            total = outgoing_totals.get(edge["source"], 0)
            edge["metric_value"] = (
                edge["amount_base"] * 10_000 // total if total > 0 else 0
            )
            edge["metric_unit"] = "basis_points_of_source_outflow"
        else:
            edge["metric_value"] = edge["amount_base"]
            edge["metric_unit"] = "asset_base_units"

    ranked = sorted(
        (edge for edge in graph["edges"] if edge["kind"] != "terminal"),
        key=lambda edge: (-edge["metric_value"], -edge["amount_base"], edge["key"]),
    )
    metric_values = [edge["metric_value"] for edge in ranked]
    minimum_metric = min(metric_values, default=0)
    maximum_metric = max(metric_values, default=0)
    for rank, edge in enumerate(ranked, start=1):
        edge["rank"] = rank
        edge["width_px"] = _bounded_metric_width(
            edge["metric_value"],
            minimum_metric,
            maximum_metric,
        )

    terminal = dict(snapshot.get("terminal") or {})
    terminal_address = str(terminal.get("deposit_address") or "")
    primary_edges, primary_addresses = _primary_path(
        graph,
        terminal_address=terminal_address,
    )
    primary_keys = {edge["key"] for edge in primary_edges}
    for edge in graph["edges"]:
        edge["primary"] = edge["key"] in primary_keys
        edge["deprioritized"] = edge["kind"] != "terminal" and not edge["primary"]

    projected_terminal = (
        terminal
        if terminal_address and terminal_address in primary_addresses
        else {
            "kind": "strategy_boundary",
            "deposit_address": primary_addresses[-1] if primary_addresses else None,
            "amount_credited_base": None,
        }
    )
    terminal_invariant = projected_terminal.get("deposit_address") == terminal.get(
        "deposit_address"
    )
    node_ranks = _node_ranks(graph, ranked, primary_addresses)
    criterion = (
        "Ranks each outgoing transfer by its share of that source address's observed outflow."
        if strategy == "dominant_fund_flow"
        else "Ranks transfers by the absolute integer base-unit value observed on the sealed graph."
    )
    return {
        "schema": "trinetra.strategy_view/1",
        "strategy": strategy,
        "available_strategies": list(strategies),
        "criterion": criterion,
        "edge_width_mapping": (
            "share of source outflow (basis points)"
            if strategy == "dominant_fund_flow"
            else "absolute transferred value (base units)"
        ),
        "edges": graph["edges"],
        "node_ranks": node_ranks,
        "primary_path": primary_addresses,
        "primary_edge_keys": [edge["key"] for edge in primary_edges],
        "priority_hop": primary_addresses[1] if len(primary_addresses) > 1 else None,
        "counts": {
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "primary_hops": len(primary_edges),
            "deprioritized_paths": sum(
                edge["deprioritized"] for edge in graph["edges"]
            ),
        },
        "terminal": projected_terminal,
        "terminal_invariant": terminal_invariant,
        "finding_projection": {
            "status": "recorded_terminal_reached" if terminal_invariant else "strategy_boundary",
            "terminal_kind": projected_terminal.get("kind"),
            "deposit_address": projected_terminal.get("deposit_address"),
            "amount_credited_base": projected_terminal.get("amount_credited_base"),
            "recorded_finding_unchanged": True,
        },
        "explanation": _explanation(
            strategy=strategy,
            criterion=criterion,
            primary_edges=primary_edges,
            graph=graph,
            terminal_invariant=terminal_invariant,
        ),
    }


def strategy_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    before = list(previous.get("primary_path") or [])
    after = list(current.get("primary_path") or [])
    before_terminal = (previous.get("terminal") or {}).get("deposit_address")
    after_terminal = (current.get("terminal") or {}).get("deposit_address")
    changed = before != after
    return {
        "from": previous.get("strategy"),
        "to": current.get("strategy"),
        "primary_path_changed": changed,
        "previous_primary_path": before,
        "current_primary_path": after,
        "deprioritized_count": int((current.get("counts") or {}).get("deprioritized_paths") or 0),
        "terminal_changed": before_terminal != after_terminal,
        "previous_terminal": before_terminal,
        "current_terminal": after_terminal,
        "summary": _delta_summary(previous, current, changed, before_terminal != after_terminal),
    }


def _flatten_tree(tree: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        address = str(node.get("address") or "Trace boundary")
        nodes.setdefault(
            address,
            {"address": address, "role": str(node.get("role") or "hop"), "depth": depth},
        )
        for position, item in enumerate(node.get("children") or []):
            child = dict(item.get("child") or {})
            recipient = str(child.get("address") or item.get("recipient") or "Trace boundary")
            kind = str((item.get("metadata") or {}).get("kind") or child.get("role") or "hop")
            output_index = item.get("output_index")
            if output_index is None:
                output_index = position
            key = f"{address}>{recipient}:{output_index}"
            edges.append(
                {
                    "key": key,
                    "source": address,
                    "target": recipient,
                    "kind": kind,
                    "amount_base": int(item.get("amount") or 0),
                    "output_index": output_index,
                    "rank": None,
                    "width_px": 1,
                }
            )
            if child:
                walk(child, depth + 1)

    if tree:
        walk(tree, 0)
    return {"nodes": list(nodes.values()), "edges": edges}


def _primary_path(
    graph: dict[str, list[dict[str, Any]]],
    *,
    terminal_address: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    nodes = graph["nodes"]
    if not nodes:
        return [], []
    root = min(nodes, key=lambda node: (node["depth"], node["address"]))["address"]
    addresses = [root]
    path: list[dict[str, Any]] = []
    visited = {root}
    current = root
    while current and current != terminal_address:
        candidates = [
            edge
            for edge in graph["edges"]
            if edge["source"] == current and edge["kind"] != "terminal"
        ]
        if not candidates:
            break
        selected = min(
            candidates,
            key=lambda edge: (-edge["metric_value"], -edge["amount_base"], edge["key"]),
        )
        if selected["target"] in visited:
            break
        path.append(selected)
        current = selected["target"]
        addresses.append(current)
        visited.add(current)
    return path, addresses


def _node_ranks(
    graph: dict[str, list[dict[str, Any]]],
    ranked_edges: list[dict[str, Any]],
    primary_addresses: list[str],
) -> list[dict[str, Any]]:
    incoming_rank = {edge["target"]: edge["rank"] for edge in ranked_edges}
    primary_order = {address: index for index, address in enumerate(primary_addresses)}
    return [
        {
            "address": node["address"],
            "depth": node["depth"],
            "rank": incoming_rank.get(node["address"], 0),
            "primary_order": primary_order.get(node["address"]),
        }
        for node in sorted(
            graph["nodes"],
            key=lambda item: (
                item["depth"],
                incoming_rank.get(item["address"], 0),
                item["address"],
            ),
        )
    ]


def _bounded_metric_width(value: int, minimum: int, maximum: int) -> int:
    if maximum <= minimum:
        return 8
    return 1 + ((value - minimum) * 7 // (maximum - minimum))


def _explanation(
    *,
    strategy: str,
    criterion: str,
    primary_edges: list[dict[str, Any]],
    graph: dict[str, list[dict[str, Any]]],
    terminal_invariant: bool,
) -> dict[str, Any]:
    selected_value = min(
        (edge["amount_base"] for edge in primary_edges),
        default=0,
    )
    deprioritized = [
        edge["key"]
        for edge in graph["edges"]
        if edge["kind"] != "terminal" and edge not in primary_edges
    ]
    return {
        "title": strategy.replace("_", " ").title(),
        "criterion": criterion,
        "selected_value_base": selected_value,
        "selected_hop_count": len(primary_edges),
        "deprioritized_edge_keys": deprioritized,
        "terminal_statement": (
            "The selected path reaches the recorded custody terminal; the stored finding is unchanged."
            if terminal_invariant
            else "This strategy view ends at a different evidence boundary; the stored finding is unchanged and cannot be replaced without a reviewed retrace."
        ),
    }


def _delta_summary(
    previous: dict[str, Any],
    current: dict[str, Any],
    path_changed: bool,
    terminal_changed: bool,
) -> str:
    path_state = "primary path changed" if path_changed else "primary path unchanged"
    terminal_state = "terminal changed" if terminal_changed else "terminal unchanged"
    count = int((current.get("counts") or {}).get("deprioritized_paths") or 0)
    return (
        f"{previous.get('strategy')} -> {current.get('strategy')}: "
        f"{count} path edges de-prioritised; {path_state}; {terminal_state}."
    )
