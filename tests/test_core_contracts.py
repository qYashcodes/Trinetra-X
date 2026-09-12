from __future__ import annotations

import unittest
from decimal import Decimal

from app.engine_bridge import ChainRef, TraceParams, TraceSeed, detect_chain, explorer_url, run_trace
from app.services.audit import append_audit_event, verify_audit_chain
from app.services.demo import demo_case
from app.services.hash import sha256_json
from app.services.money import basis_points, format_amount, format_millions
from app.services.risk import risk_check


class CoreContractTests(unittest.TestCase):
    def test_chain_detection(self) -> None:
        self.assertEqual(detect_chain("TNq7CqVEANFoJjvekpNLtTYuTnTtA6x7Ti"), "TRON")
        self.assertEqual(detect_chain("0x0000000000000000000000000000000000000000"), "EVM")
        self.assertEqual(detect_chain("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"), "BTC")
        self.assertEqual(detect_chain("not-an-address"), "unsupported")

    def test_snapshot_v2_hash_is_canonical(self) -> None:
        snapshot = run_trace(
            TraceSeed(
                address="TNq7CqVEANFoJjvekpNLtTYuTnTtA6x7Ti",
                payment_ts_ms=None,
                amount_base=None,
            ),
            TraceParams(value_floor_share=Decimal("0.02")),
        )
        self.assertEqual(snapshot["schema"], "trinetra.snapshot/2")
        self.assertEqual(snapshot["terminal"]["kind"], "vasp_deposit")
        self.assertEqual(snapshot["terminal"]["amount_credited_base"], 17880000000)
        claimed = snapshot.pop("sha256")
        self.assertEqual(sha256_json(snapshot), claimed)

    def test_fixture_trace_close_time_and_hash_are_deterministic(self) -> None:
        seed = TraceSeed(
            address=demo_case()["case"]["reported_address"],
            payment_ts_ms=None,
            amount_base=None,
        )
        first = run_trace(seed, TraceParams())
        second = run_trace(seed, TraceParams())
        self.assertEqual(first["closed_ts"], demo_case()["trace_closed_ts_ms"])
        self.assertEqual(first["sha256"], second["sha256"])

    def test_money_and_basis_points_are_integer_safe(self) -> None:
        self.assertEqual(format_amount(17880000000), "17,880.00 USDT")
        self.assertEqual(format_millions(2_410_000_000_000), "2.41")
        self.assertEqual(basis_points(17880000000, 21940000000), 8150)

    def test_risk_bands_and_no_clearance_language(self) -> None:
        alert = risk_check("TGh3c9PkL8Qn7MuYbxV1aZP2R6EeSsQ7hC")
        clean = risk_check("RC_clean")
        self.assertEqual(alert["band"], "alert")
        self.assertEqual(clean["band"], "low")
        self.assertIn(
            "No notice is recorded; dispatch and restraint status are not established.",
            alert["signals"],
        )
        self.assertNotIn("active restraint", alert["verdict_line"].lower())
        self.assertIn("Absence of a record is not clearance.", clean["signals"])

    def test_explorer_urls_are_chain_specific(self) -> None:
        self.assertIn("tronscan.org", explorer_url("tx", "abc", ChainRef("TRON", "mainnet")))
        self.assertIn("blockstream.info", explorer_url("address", "bc1abc", ChainRef("BTC", "mainnet")))

    def test_audit_chain_verifies(self) -> None:
        append_audit_event("test", "unit", "core")
        self.assertTrue(verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
