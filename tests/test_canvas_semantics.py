from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.demo import demo_case
from app.services.graph_view import omega_graph_payload
from app.services.money import format_amount
from app.services.time import format_ist
from app.settings import ROOT_DIR


def test_canvas_keeps_deposit_and_hot_wallet_addresses_distinct() -> None:
    data = demo_case()
    deposit_address = data["terminal"]["deposit_address"]
    hot_wallet_address = data["terminal"]["hot_wallet"]

    assert deposit_address == data["dominant_path"][-1]["address"]
    assert deposit_address != hot_wallet_address

    environment = Environment(
        loader=FileSystemLoader(ROOT_DIR / "app" / "templates"),
        autoescape=select_autoescape(("html",)),
    )
    environment.filters["amount"] = format_amount
    environment.filters["amount_number"] = lambda value: format_amount(value, symbol="").strip()
    environment.filters["pctbp"] = lambda value: f"{value / 100:.2f}%"
    environment.filters["ist"] = format_ist
    rendered = environment.get_template("canvas.html").render(
        user={"name": "Insp. R. Kulkarni", "desk": "Cyber Cell, Pune City"},
        demo=data,
    )

    assert f'data-deposit-address="{deposit_address}"' in rendered
    assert f'data-hot-wallet-address="{hot_wallet_address}"' in rendered
    assert 'data-omega-graph' in rendered
    assert 'data-omega-graph-json' in rendered
    assert 'data-strategy-select' in rendered
    assert 'data-explain-target="methodology"' in rendered
    assert 'class="canvas-chip canvas-chip-link"' not in rendered
    assert 'class="canvas-strategy-actions" aria-label="Trace actions"' in rendered
    assert 'title="No custody finding is recorded for this snapshot.">Custody findings</button>' in rendered
    assert 'data-explain-target="methodology">\n          <span aria-hidden="true">⇄' not in rendered

    payload = omega_graph_payload(
        {
            "case": {
                "reported_address": data["dominant_path"][0]["address"],
                "amount_reported_base": data["case"]["amount_reported_base"],
                "payment_txid": data["case"]["payment_txid"],
                "payment_ts_ms": data["case"].get("payment_ts_ms") or data["case"].get("payment_ts"),
            },
            "asset": data["case"]["asset"],
            "engine": {"mode": "fixture"},
            "hops": data["dominant_path"],
            "parked": data["parked"],
            "terminal": data["terminal"],
        }
    )

    seen_addresses: set[str] = set()

    def walk(node: dict) -> None:
        seen_addresses.add(node["address"])
        for edge in node.get("children", []):
            seen_addresses.add(edge["recipient"])
            if edge.get("child"):
                walk(edge["child"])

    walk(payload["tree"])
    assert deposit_address in seen_addresses
    assert hot_wallet_address in seen_addresses
    assert deposit_address != hot_wallet_address


def test_canvas_copy_action_uses_the_full_fixture_address() -> None:
    script = (ROOT_DIR / "app" / "static" / "omega-graph.js").read_text(
        encoding="utf-8"
    )

    assert "await copyAddress(node.address)" in script
    assert "navigator.clipboard.writeText(address)" in script


def test_omega_graph_payload_for_live_snapshot_excludes_unconfirmed_observations() -> None:
    payload = omega_graph_payload(
        {
            "case": {
                "reported_address": "TSeed1111111111111111111111111111111",
                "payment_txid": "0" * 64,
                "payment_ts": 1_789_337_235_000,
                "amount_reported_base": 700,
            },
            "asset": {"symbol": "USDT", "decimals": 6},
            "engine": {"mode": "live"},
            "hops": [
                {
                    "hop": 1,
                    "source_address": "TSeed1111111111111111111111111111111",
                    "address": "THop11111111111111111111111111111111",
                    "value_base": 280,
                    "observed_amount_base": 900,
                    "txids": ["1" * 64],
                    "event_index": 0,
                    "frontier_state": "expanded",
                }
            ],
            "parked": [
                {
                    "branch_id": "TRON:mainnet:2:0",
                    "source_address": "TSeed1111111111111111111111111111111",
                    "address": "TParked11111111111111111111111111111",
                    "value_base": 210,
                    "observed_amount_base": 800,
                    "deferral_reason": "breadth_cap",
                }
            ],
            "unconfirmed_observations": [
                {"address": "TUnconfirmed111111111111111111111111", "value_base": 999}
            ],
            "terminal": {"kind": "depth_exhausted"},
        },
        balance_lookup=lambda addresses, _asset, _result: {
            address: {
                "amount_base": 12_345_678 if address.startswith("THop") else 0,
                "source": "trongrid",
                "retrieval_ts_ms": 1_789_337_236_000,
            }
            for address in addresses
        },
    )

    serialized = str(payload)
    assert payload["mode"] == "live"
    hop_edge = payload["tree"]["children"][0]
    assert hop_edge["amount"] == 900
    assert hop_edge["metadata"]["observed_amount_base"] == 900
    assert hop_edge["metadata"]["attributed_amount_base"] == 280
    assert payload["tree"]["balance"]["available"] is True
    assert payload["tree"]["balance"]["value"] == 0
    assert hop_edge["child"]["balance"]["available"] is True
    assert hop_edge["child"]["balance"]["value"] == 12_345_678
    assert hop_edge["child"]["balance"]["display"] == 12.345678
    parked_edge = payload["tree"]["children"][1]
    assert parked_edge["child"]["balance"]["available"] is True
    assert parked_edge["child"]["balance"]["value"] == 0
    assert "THop11111111111111111111111111111111" in serialized
    assert "TParked11111111111111111111111111111" in serialized
    assert "TUnconfirmed111111111111111111111111" not in serialized


def test_omega_graph_repeated_arrival_is_a_tree_node_and_branches_from_latest_arrival() -> None:
    payload = omega_graph_payload(
        {
            "case": {
                "reported_address": "TSeed",
                "payment_txid": "",
                "payment_ts_ms": 0,
                "amount_reported_base": 100,
            },
            "asset": {"symbol": "USDT", "decimals": 0},
            "engine": {"mode": "live"},
            "hops": [
                {
                    "hop": 1,
                    "source_address": "TSeed",
                    "address": "TArrival",
                    "value_base": 80,
                    "observed_amount_base": 100,
                    "txids": [""],
                    "event_index": 0,
                },
                {
                    "hop": 2,
                    "source_address": "TArrival",
                    "address": "TMiddle",
                    "value_base": 60,
                    "observed_amount_base": 70,
                    "txids": ["2" * 64],
                    "event_index": 0,
                },
                {
                    "hop": 3,
                    "source_address": "TMiddle",
                    "address": "TArrival",
                    "value_base": 40,
                    "observed_amount_base": 50,
                    "txids": ["3" * 64],
                    "event_index": 0,
                },
            ],
            "parked": [
                {
                    "branch_id": "repeated-arrival-branch",
                    "source_address": "TArrival",
                    "address": "TParkedAfterRepeat",
                    "value_base": 10,
                    "observed_amount_base": 20,
                    "txids": ["4" * 64],
                    "event_index": 0,
                    "deferral_reason": "cycle_detected",
                }
            ],
            "terminal": {"kind": "depth_exhausted"},
        }
    )

    root = payload["tree"]
    first_edge = root["children"][0]
    first_arrival = first_edge["child"]
    middle = first_arrival["children"][0]["child"]
    repeated_arrival = middle["children"][0]["child"]

    assert [root["address"], first_arrival["address"], middle["address"], repeated_arrival["address"]] == [
        "TSeed",
        "TArrival",
        "TMiddle",
        "TArrival",
    ]
    assert first_arrival is not repeated_arrival
    assert [edge["recipient"] for edge in first_arrival["children"]] == ["TMiddle"]
    assert [edge["recipient"] for edge in repeated_arrival["children"]] == [
        "TParkedAfterRepeat"
    ]
    assert first_edge["amount"] == 100
    assert first_edge["metadata"]["attributed_amount_base"] == 80
    assert first_edge["output_index"] == 0
    assert first_edge["sent_txid"] == ""
    parked_edge = repeated_arrival["children"][0]
    assert parked_edge["amount"] == 20
    assert parked_edge["metadata"]["attributed_amount_base"] == 10
    assert parked_edge["output_index"] == 0
    assert parked_edge["branch_reasons"] == ["cycle detected"]


def test_omega_renderer_stable_edge_key_includes_preserved_output_index() -> None:
    script = (ROOT_DIR / "app" / "static" / "omega-graph.js").read_text(
        encoding="utf-8"
    )

    assert (
        "stableKey: `${currentNode.address}>${childNode.address}:${item.output_index ?? index}`"
        in script
    )


def test_omega_strategy_switch_changes_graph_visual_state() -> None:
    script = (ROOT_DIR / "app" / "static" / "omega-graph.js").read_text(
        encoding="utf-8"
    )
    css = (ROOT_DIR / "app" / "static" / "omega-graph.css").read_text(
        encoding="utf-8"
    )

    assert "shell.dataset.omegaStrategy = analysis.strategy" in script
    assert 'class: "omega-strategy-stamp"' in script
    assert "Strategy: ${label}" in script
    assert 'edge.strategyRank === 1 ? " is-rank-one" : ""' in script
    assert "omega-edge-rank-badge" in script
    assert "omega-strategy-changed" in script
    assert '.omega-graph-card[data-omega-strategy="value_weighted"] .omega-flow-edge.is-primary' in css
    assert ".omega-strategy-changed .omega-graph-viewport" in css
    assert "@keyframes omega-strategy-pulse" in css


def test_omega_export_is_self_styled_without_scope_controls() -> None:
    template = (ROOT_DIR / "app" / "templates" / "_omega_graph.html").read_text(
        encoding="utf-8"
    )
    script = (ROOT_DIR / "app" / "static" / "omega-graph.js").read_text(
        encoding="utf-8"
    )

    assert "data-omega-export-scope-option" not in template
    assert "Graph export scope" not in template
    assert "exportScope" not in script
    assert 'const scope = "whole_graph";' in script
    assert "function inlineExportStyles(clone)" in script
    assert "function applyExportPaint(clone)" in script
    assert "data-omega-export-style" in script
    assert ".omega-node-box { fill: #ffffff; stroke: #7890aa;" in script
    assert ".omega-node-address { fill: #071936;" in script
    assert '${strategy}_${scope}_${fileStamp(new Date())}' in script
    assert 'box.setAttribute("fill", "#ffffff")' in script
    assert 'label.setAttribute("fill", "#071936")' in script
    assert 'edge.setAttribute("stroke", "#47617d")' in script
    assert "function applyExportVisibility(clone)" in script
    assert 'setAttribute("transform", "translate(20,20) scale(1)")' in script
