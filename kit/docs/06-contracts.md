# 06 · Contracts

Stable interfaces. The UI depends on these shapes; the bridge adapts the engine to them. If the
engine's real shapes differ, **change the bridge, not the templates**, and note it in `ENGINE_MAP.md`.

## 1. Engine bridge (`app/engine_bridge.py`)

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator, Literal

@dataclass(frozen=True)
class TraceParams:
    max_depth: int = 5
    time_window_hours: int = 8
    value_floor_share: Decimal = Decimal("0.02")
    breadth_cap: int = 3
    address_budget: int = 60
    strategy: Literal["dominant_fund_flow", "value_weighted"] = "dominant_fund_flow"

def detect_chain(address: str) -> Literal["TRON", "EVM", "BTC", "unsupported"]
def trace(seed_address: str, payment_ts_ms: int | None, amount_base: int | None,
          params: TraceParams, payment_txid: str | None = None) -> Iterator["TraceEvent"]
def classify(address: str) -> dict        # §5
def resolve_vasp(address: str) -> dict | None   # {entity_key, rung, band, basis}
def linked_cases(address: str, snapshots: list[dict]) -> list[str]   # engine linkage over stored results
def explorer_url(kind: Literal["tx", "address"], value: str, chain: str = "TRON") -> str
def engine_mode() -> Literal["fixture", "live"]
```

`TraceEvent = {"type": <event>, "data": {...}}` with the types in §4. The bridge is responsible for
converting engine amounts to integer base units and timestamps to epoch ms before they reach the app.

## 2. Normalised transfer (engine output, app input)

```json
{ "txid": "64-hex", "ts": 1756482247000, "from": "T…", "to": "T…",
  "amount_base": 21940000000, "token": "USDT", "decimals": 6, "direction": "in|out" }
```
Raw TronGrid item (fixtures reproduce it exactly): `transaction_id`, `block_timestamp` (ms),
`from`, `to`, `value` (integer string), `type: "Transfer"`, `token_info {symbol, address, decimals, name}`.
USDT-TRC20 contract: `TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`.

## 3. Terminal kinds

`vasp_deposit · unregistered_vasp · vasp_hot_wallet · stationary_funds · mixer · bridge ·
trail_dissipated · depth_exhausted · unsupported_chain` (engine) + `error` (app).

## 4. SSE events (`GET /api/traces/{snapshot_id}/stream`)

Wire format: `id: <seq>` · `event: <type>` · `data: <json>`. Heartbeat comment every 15 s.
Consumed by Live Trace; canvas loads the finished snapshot via JSON.

| event | data |
|---|---|
| `source` | `{name, status: ok\|degraded\|cached\|offline, detail}` — TronGrid, attribution set revision, prior dockets, advisory lists |
| `work` | `{message}` — e.g. "Scoring 11 outbound transfers at hop 2" |
| `hop` | `{hop, address, value_base, onward_base?, share_bp, ts, txids[], classification, band, confidence}` |
| `parked` | `{branch_id, from_hop, address, value_base, share_bp, reason: "off_dominant_flow", candidate?}` |
| `dust` | `{from_hop, count, total_base}` (aggregated; never one event per dust transfer) |
| `candidate` | `{name, entity_key?, band, amount_base, basis, at_hop}` |
| `stats` | `{addresses_visited, transfers_read, retained_share_bp, branches_parked}` |
| `terminal` | `{kind, custodian_key?, deposit_address?, hot_wallet?, amount_credited_base?, retained_share_bp?, time_to_custody_s?, note}` |
| `done` | `{snapshot_id, sha256, closed_ts}` |
| `error` | `{message, retryable}` |

`share_bp` = basis points of the reported amount (8150 = 81.50%). Integers everywhere on the wire; formatting is the template's job.

## 5. Classification (right panel, verdict)

```json
{ "address": "T…", "class": "exchange_deposit_address", "posterior": 0.91, "band": "high",
  "fingerprint": [
    {"signal": "fan_in", "label": "Fan-in from unrelated senders", "value": "187 distinct senders", "pass": true, "lr": 4.2}
  ],
  "typology": "consolidation", "vasp": {"entity_key": "coinsphere", "rung": 3, "band": "high", "basis": "sweep destination labelled"} }
```
Class values: `exchange_deposit_address · vasp_hot_wallet · peel_chain · pass_through · consolidation ·
fan_out · stationary · mixer · bridge · unknown`.

## 6. Snapshot result (`trace_snapshot.result_json`, hashed)

```json
{ "schema": "trinetra.snapshot/1",
  "case": {"ack_no": "…", "reported_address": "T…", "payment_txid": "…", "payment_ts": 0, "amount_reported_base": 0},
  "params": {…TraceParams…}, "engine": {"mode": "fixture|live", "version": "git-sha", "registry_revision": "…"},
  "seed_match": {"matched": true, "txid": "…", "delta_bp": 0},
  "hops": [{…hop event…}], "parked": [...], "dust": [...],
  "classifications": {"T…": {…§5…}},
  "terminal": {…}, "stats": {…},
  "started_ts": 0, "closed_ts": 0 }
```
`sha256 = sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())`.

## 7. VASP registry (`engine/data/vasp_registry.json`) — proposed schema for the Data role

Check `engine/labels.py` first; if its format differs, keep the engine's format and add the provenance fields below to it.

```json
{ "revision": "2026-09-14",
  "entities": [
    { "key": "exchange_x", "name": "Exchange X", "type": "cex", "jurisdiction": "…",
      "fiu_ind_reg": "VA-…|null", "fiu_status": "registered_domestic|registered_offshore|unregistered|non_compliance_notice",
      "india_compliance_desk": true, "le_contact": "…", "source_ref": "https://…" } ],
  "addresses": [
    { "address": "T…", "chain": "TRON", "entity_key": "exchange_x",
      "role": "hot_wallet|cold_wallet|deposit_hub|withdrawal",
      "source": "tronscan_tag|exchange_disclosure|controlled_deposit|vasp_reply",
      "source_ref": "https://tronscan.org/#/address/…", "verified_by": "name", "verified_on": "YYYY-MM-DD",
      "confidence": "high|medium" } ] }
```
Rule: **no address enters without a `source_ref` a second person has opened.** Never add an address from memory, a forum, or an unverified list.

## 8. Canonical demo facts (`docs/demo_case.json` is authoritative)

| Item | Value |
|---|---|
| Case | `NCRP/2026/MH/0084213`, investment fraud, Pune City Cyber Cell, filed 2026-08-30 09:12 IST |
| Payment | 2026-08-29 21:14:07 IST, **21,940.00 USDT**, TRON / USDT-TRC20 |
| Reported address (H0) | `TNq7CqVEANFoJjvekpNLtTYuTnTtA6x7Ti` (check demo_case.json) |
| Dominant path | H0 pass → H1 pass-through → H2 peel chain → H3 consolidation → **H4 deposit address** → HW Coinsphere hot wallet |
| Credited to custody | **17,880.00 USDT (81.5%)** at Coinsphere Global Pte Ltd (fictional; FIU-IND VA-0117), time to custody 2 h 27 m |
| Parked | A: 2,310.00 → cluster K-88 (medium); B: 1,020.00 + 730.00 = 1,750.00 → self-custody remainder (low); total 4,060.00 incl. both, off the dominant flow |
| H4 designed output | posterior ≈ 0.91, high; 187 distinct senders, forward ratio 1.00, 0 balance after sweeps, 187/187 to one wallet, median sweep 4 min, hub fed by 214 deposit-pattern addresses |
| Linked case | `NCRP/2026/MH/0081902` shares H4 → Union-Find link |
| Notice | `CCC/PUN/FN/2026/0311`, deadlines 24/72/168 h (default 24), countersign ≥ 10,000.00 USDT |
| Officers | Insp. R. Kulkarni, PIS 74821 (IO) · ACP S. Deshmukh, PIS 61207 (supervisor) |
| Demo clock | 2026-08-30 09:41 IST |
| Other referrals | `…/MH/0084231` → `stationary_funds` at hop 1 (shows the escalate variant) |
| Risk-check examples | H4 → alert · H2 → medium · RC_clean → low ("absence of a record is not clearance") |

Designed engine outputs are **test oracles**. If the real engine produces different numbers, the UI shows the engine's numbers and the oracle in `demo_case.json` is updated. Never the other way round.

## 9. Notice view model (`notice_document.html` input)

```
letterhead{emblem, govt_line, office, address, email}, notice_no, date_ist, addressee,
case{ack_no, fir_no?}, legal_basis (text owned by legal member),
particulars[{label, value}]  # reported address, payment txid, deposit address, amount credited, time credited, snapshot sha256
path_summary[{hop, address, amount, ts, txids}], deadline_hours, deadline_text,
requests[ "Restrain…", "Disclose KYC…", "Preserve logs…" ],
signature{officer, rank, pis, desk}, countersignature?{officer, rank, signed_ts},
enclosures[], copy_to[], footer{notice_no, snapshot_sha256_short, page_x_of_y}
```
