from __future__ import annotations

from app.engine_bridge import AssetRef, ChainRef, TraceParams, TraceSeed, run_trace
from app.services.demo import demo_case
from app.services.graph_view import omega_graph_payload
from app.services.strategy import engine_strategies, strategy_analysis, strategy_delta


def fixture_result() -> dict:
    data = demo_case()
    case = data["case"]
    return run_trace(
        TraceSeed(
            address=case["reported_address"],
            payment_ts_ms=case["victim_payment_ts_ms"],
            amount_base=case["amount_reported_base"],
            chain=ChainRef(
                family=case["chain"]["family"],
                network=case["chain"]["network"],
            ),
            asset=AssetRef(
                symbol=case["asset"]["symbol"],
                contract=case["asset"].get("contract"),
                decimals=case["asset"]["decimals"],
            ),
            payment_txid=case["payment_txid"],
            ack_no=case["ack_no"],
        ),
        TraceParams(trace_mode="fixture"),
    )


def test_strategy_projection_uses_engine_contract_and_preserves_sealed_amounts() -> None:
    result = fixture_result()
    dominant = strategy_analysis(result, "dominant_fund_flow")
    weighted = strategy_analysis(result, "value_weighted")

    assert engine_strategies() == ("dominant_fund_flow", "value_weighted")
    assert dominant["available_strategies"] == list(engine_strategies())
    dominant_amounts = {row["key"]: row["amount_base"] for row in dominant["edges"]}
    weighted_amounts = {row["key"]: row["amount_base"] for row in weighted["edges"]}
    assert dominant_amounts == weighted_amounts
    assert any(
        left["width_px"] != right["width_px"]
        for left, right in zip(dominant["edges"], weighted["edges"], strict=True)
    )
    assert dominant["finding_projection"]["recorded_finding_unchanged"] is True
    assert weighted["finding_projection"]["recorded_finding_unchanged"] is True
    assert dominant["terminal_invariant"] is True
    assert weighted["terminal_invariant"] is True

    delta = strategy_delta(dominant, weighted)
    assert delta["from"] == "dominant_fund_flow"
    assert delta["to"] == "value_weighted"
    assert delta["terminal_changed"] is False
    assert "terminal unchanged" in delta["summary"]


def test_parked_branches_attach_to_their_recorded_source_hop() -> None:
    result = fixture_result()
    graph = omega_graph_payload(result)
    root = graph["tree"]
    by_address: dict[str, dict] = {}

    def walk(node: dict) -> None:
        by_address[node["address"]] = node
        for edge in node.get("children") or []:
            if edge.get("child"):
                walk(edge["child"])

    walk(root)
    hops = {int(row["hop"]): row["address"] for row in result["hops"]}
    for branch in result["parked"]:
        parent = by_address[hops[int(branch["from_hop"])]]
        recipients = {edge["recipient"] for edge in parent.get("children") or []}
        assert branch["address"] in recipients
