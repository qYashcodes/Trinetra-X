from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.demo import demo_case
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

    deposit_display = f"{deposit_address[:4]}...{deposit_address[-6:]}"
    hot_wallet_display = f"{hot_wallet_address[:13]}...{hot_wallet_address[-6:]}"
    assert deposit_display != hot_wallet_display
    assert f'data-deposit-address="{deposit_address}"' in rendered
    assert f'data-hot-wallet-address="{hot_wallet_address}"' in rendered
    assert (
        f'aria-label="H4, Exchange deposit, exchange deposit address: {deposit_address}"'
        in rendered
    )
    assert (
        f'aria-label="H5, Coinsphere, custodian hot-wallet address: {hot_wallet_address}"'
        in rendered
    )
    assert f'aria-label="Custodian hot-wallet address: {hot_wallet_address}"' in rendered
    assert deposit_display in rendered
    assert hot_wallet_display in rendered
    for hop in data["dominant_path"][:-1]:
        assert f'blockchain address: {hop["address"]}' in rendered
        assert f'{hop["address"][:4]}...{hop["address"][-4:]}' in rendered


def test_canvas_copy_action_uses_the_full_fixture_address() -> None:
    script = (ROOT_DIR / "app" / "static" / "canvas.js").read_text(
        encoding="utf-8"
    )

    assert "fullAddress: fixtureAddresses.deposit" in script
    assert "fullAddress: fixtureAddresses.hotWallet" in script
    assert "navigator.clipboard.writeText(fullAddress)" in script
