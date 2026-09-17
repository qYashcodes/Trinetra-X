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
