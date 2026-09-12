# 03 · Engine specification (TRON / USDT-TRC20)

Describes the **finalised, built** engine. This is structurally complete; v2 work is breadth
(chains, labels, calibration), not a redesign. Where this doc and `engine/` code differ, **the code
wins**: record the difference in `ENGINE_MAP.md`.

Pipeline: **reported address → haircut-taint best-first traversal → per-hop classification → VASP resolution → terminal state.**

## 1. Module map

| File | Job |
|---|---|
| `engine/adapters/tron.py` | `matches` (base58check validation), `fetch` (TronGrid, paginated, capped), `normalise` (6 decimals, ms timestamps; **raises instead of guessing**), `explorer_url`, plus balance lookup, TronScan public tag, payment-hash lookup. Same code in fixture and live mode. |
| `engine/tracer.py` | `trace()` generator: best-first search on a value-priority heap, haircut taint, per-hop time window, value floor, breadth cap, address budget, stop conditions, UI events. `run_trace()` consumes it and returns the final result. |
| `engine/classifier.py` | `classify()`: Bayesian likelihood-ratio model over 6 deposit-address signals, hub-first, damped. `typology()`: names what a hop did (pass-through, peel chain, consolidation, fan-out, stationary). |
| `engine/vasp.py` | `resolve_vasp()`: 5-rung ladder. The rung fired sets the band. |
| `engine/linkage.py` | Union-Find over stored trace results, keyed on deposit addresses and non-service intermediates. Never hot wallets. |
| `engine/labels.py` | Merges `engine/data/vasp_registry.json` (real; Data role) with `fixtures/labels_demo.json` (**fixture mode only**). |
| `engine/config.py` | Every threshold with its rationale. Loads `.env` via python-dotenv. |
| `engine/cli.py` | Command-line runner. |
| `scripts/make_fixture.py` | Deterministic generator (seed 38) → TronGrid-shaped JSON in `fixtures/` from `docs/demo_case.json`. 680 transfers across 482 accounts. |

Chain adapter interface (per chain module): `matches(address) → bool`, `fetch(address, …) → list[raw]`, `normalise(raw) → Transfer`, `explorer_url(kind, value) → str`. EVM/BTC modules exist as scaffolds that make `detect_chain` return the right family and the tracer emit `unsupported_chain`.

## 2. Running it

```bash
pip install -r requirements.txt
python scripts/make_fixture.py                         # builds fixtures/ from docs/demo_case.json
python -m engine.cli TNq7CqVEANFoJjvekpNLtTYuTnTtA6x7Ti --victim-time "2026-08-29 21:14:07" --amount 21940
python -m engine.cli --txid <payment hash>             # seeds address, time, amount from chain
python -m pytest -q                                    # 25 tests
```

`.env`: `TRINETRA_MODE=fixture|live`, `TRONGRID_API_KEY`, `TRONSCAN_API_KEY`,
`TRINETRA_CACHE_EXPIRE` (seconds; `-1` = never expire, use before an offline demo),
`TRINETRA_MAX_RPS` (default 10; TronGrid keyed limit is 15 QPS), `TRINETRA_FIXTURE_DIR`, `TRINETRA_CACHE_PATH`.

## 3. Traversal

Best-first search on a **value-priority max-heap**: the address currently holding the most tainted
value is always expanded next. Implements "follow the money, not the edges". Plain `heapq`; no graph library.

| Parameter | Default | Meaning |
|---|---|---|
| `max_depth` | 5 hops | Real fraud chains reach an exchange in 1–5 |
| Time window | 8 h per hop | Only outflows after tainted funds arrive, within 8 h, are charged. Pre-existing history never is |
| Value floor | 2% of reported amount | Below = dust: logged, not followed (demo: 438.80 USDT) |
| Breadth cap | 3 outflows / address | Bounds fan-out |
| Address budget | 60 / trace | Hard ceiling regardless of branching |
| Strategy | `dominant_fund_flow` | Expand only the largest outflow per hop; other significant outflows become **parked branches** (recorded and classified one hop deep, not expanded). `value_weighted` expands every significant outflow |

Reference pseudocode (behavioural description; the real code is `engine/tracer.py`):

```
push (taint=seed_amount, depth=0, addr=seed, arrived_at=payment_ts)
while heap and visited < ADDRESS_BUDGET:
    node = pop max-taint
    transfers = normalise(fetch(node.addr))
    cls = classify(node.addr)                       # custody check first
    if custody point (deposit posterior ≥ 0.60, or labelled service):
        vasp = resolve_vasp(node.addr); emit terminal; continue/stop per strategy
    if labelled mixer / bridge:               emit terminal (mixer | bridge)
    outs = outgoing where arrived_at < ts ≤ arrived_at + 8h
    if no outs and taint still at address:    candidate terminal stationary_funds
    for o in outs: o.taint = haircut(o)             # §4
    dust = outs with taint < floor → log only
    sig  = top 3 of outs with taint ≥ floor
    if depth == max_depth:                     candidate terminal depth_exhausted
    dominant_fund_flow: push largest; others → parked (classify one hop, don't expand)
    value_weighted:     push all sig
    emit hop / parked / stats / work events
if remaining taint all below floor:            terminal trail_dissipated
```

## 4. Haircut taint

When tainted funds mix with unrelated funds, each subsequent outflow carries taint **in proportion to
the address's tainted share at that moment**:

```
outflow_taint = outflow_amount × (tainted_balance / total_balance)   # both at the instant before the outflow
tainted_balance -= outflow_taint
```

Example: address holds 10,000 clean, receives 5,000 tainted (share 1/3), then sends 6,000 → the
outflow carries 2,000 taint; 3,000 taint remains. This avoids both ignoring dilution (over-claiming)
and following whichever transfer "looks biggest" (under-principled). It is what makes peel chains
and consolidation behave correctly.

## 5. Seed matching

Seed = reported address (the PS input) or a payment tx hash (resolved to address, time, amount).
When both time and amount are reported, a nearby on-chain transfer is accepted as "the payment"
only if its amount is within **3% (minimum 1 USD)** of the reported figure. Otherwise the engine
reports "no on-chain transfer matched" and proceeds with the reported amount, rather than silently
mislabelling an unrelated transfer as the victim's payment. (This bug was caught in team testing and fixed; verified live.)

## 6. Classifier — the deposit-address fingerprint

Six signals, each a **likelihood ratio** LR = P(obs | deposit address) / P(obs | anything else):

| Signal | Measures |
|---|---|
| Fan-in | Distinct unrelated senders |
| Forward ratio | Outbound ÷ inbound value |
| Resting balance | Balance relative to typical inbound size |
| Destination consistency | Share of outflows going to one address |
| Sweep timing | Median minutes from an inbound credit to the next outbound |
| Hub feed count | How many other deposit-pattern addresses feed the same destination (hub-first) |

Combination (signals are correlated, so a naive product overstates confidence):

```
prior = 0.10                  → prior_odds = 0.10 / 0.90
combined = (∏ LR_i) ^ 0.6     # damping factor 0.6
posterior_odds = prior_odds × combined
posterior = posterior_odds / (1 + posterior_odds)
```

- A signal with **< 5 observations** is neutral (LR = 1), not guessed.
- Bands: **high ≥ 0.85**, **medium 0.60–0.85**, **low < 0.60**. **0.60** is also the custody-point threshold.
- **Hub-first inference**: classify the sweep destination (is it fed by many deposit-pattern addresses?) and let that inform each feeder. The hub explains the box.
- Output shape: `{class, posterior, band, fingerprint: [{signal, value, pass, lr}], vasp_key?, rung?}`.
- `typology()` labels intermediate hops: pass-through, peel chain, consolidation, fan-out, stationary.

All thresholds are defensible defaults documented in `engine/config.py`. **None are calibrated yet** (docs/04, docs/10 D-09). The module is isolated so a scikit-learn model can replace the scoring behind the same interface.

## 7. VASP resolution ladder

| Rung | Evidence | Band |
|---|---|---|
| 1 | Address in the curated label registry | High |
| 2 | TronScan public tag | High |
| 3 | Deposit-pattern address whose sweep destination is named by rung 1 or 2 | Classifier band |
| 4 | Address or its hub confirmed in an earlier case (a VASP replied to a prior notice) | Medium |
| 5 | Deposit pattern present, hub unnamed | Low |

Rungs 1–2 name a company. Rungs 3–5 are the product value: naming the one customer account a deposit address maps to. **Rung 4 depends on the app recording VASP replies** (docs/08 P5).

## 8. Terminal states

| Kind | Meaning |
|---|---|
| `vasp_deposit` | Reached a deposit address at a registered exchange. Core win |
| `unregistered_vasp` | Named entity, not FIU-IND registered; recovery flagged low-probability |
| `vasp_hot_wallet` | Reached a labelled hot wallet without isolating a deposit address one hop earlier |
| `stationary_funds` | Funds sitting with no custodian; escalate now |
| `mixer` | Trail terminates; shared industry limit |
| `bridge` | Exits the chain |
| `trail_dissipated` | Remaining taint fragmented below the value floor |
| `depth_exhausted` | Cap reached before a custody point |
| `unsupported_chain` | EVM or BTC detected; "adapter in progress" |

The app adds one of its own: `error` (network/unknown failure) — rendered with a restart action, never a stack trace.

## 9. Case linkage

Union-Find across stored trace results, keyed only on **deposit addresses and non-service
intermediate wallets**. **Never on exchange hot wallets**, which receive from thousands of
unrelated customers and would merge every case touching the same exchange into one false syndicate.
v2: add gas-funding clustering (who sent the burner its TRX).

## 10. Known engine limits

- Multi-hop not yet exercised on live data (fixture verified 5 hops deep).
- Registry empty → live runs name a company only via TronScan tags (rung 2) or leave the hub unnamed (rung 5).
- Classifier uncalibrated.
- Stateless by design: persistence, auth, audit belong to the app.
- No bridge continuation matching, no gas-funding clustering, no pre-transaction module.
