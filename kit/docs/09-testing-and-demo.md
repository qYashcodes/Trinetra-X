# 09 · Testing, demo, deployment

## 1. Test layers

| Layer | Where | What must be covered |
|---|---|---|
| Engine (existing, 25) | `tests/` | normalise traps, traversal, taint, seed matching, classifier, ladder, linkage. **Never delete or weaken.** |
| Bridge contract | `tests/app/test_bridge.py` | Fixture trace → event types/order, terminal kind, amounts in base units, ms timestamps, hop count; `detect_chain` for T/0x/bc1/garbage |
| Services | `tests/app/test_*.py` | stage machine; countersign rules (self, non-supervisor, threshold); audit chain tamper detection; risk bands; attention/overdue with `DEMO_CLOCK`; linkage excludes hot wallets; fund summary reconciles |
| API | httpx `TestClient` | auth redirects, CSRF rejection, review gating, dispatch blocked before countersign |
| SSE | TestClient streaming | full stream; reconnect with `Last-Event-ID` replays the tail exactly; `error` on engine exception |
| PDF | `test_pdf.py` | stored SHA-256 == hash of served bytes; watermark present iff configured; page count ≥ 1; footer contains notice number |
| E2E | Playwright `tests/e2e/golden_path.py` | login → intake → trace → canvas → verdict → notice (two contexts for supervisor) → dispatch → docket, at 1366×768 and 1920×1080 |
| Copy lint | `tests/app/test_copy.py` | grep templates + copy.py for banned words (fraudster, scammer, criminal) |

Rule: a bug fix starts with a failing test.

## 2. Live verification protocol (T-LIVE)

1. `TRINETRA_MODE=live`, keys set, `TRINETRA_CACHE_EXPIRE=900`.
2. Pick a recent address on TronScan that has **both received and forwarded** USDT in the last few days (mid-chain, not an exchange, not the USDT contract).
3. `python -m engine.cli <addr> --victim-time "<an inbound's time>" --amount <that inbound>`; record hops, terminal, API call count, duration.
4. Resolve one address that carries a TronScan public exchange tag → expect rung 2.
5. Once the registry has entries: trace an address that deposits into a registry hot wallet → expect rung 1/3.
6. Save CLI output + screenshots in `docs/evidence/live-<date>.md`. Then `TRINETRA_CACHE_EXPIRE=-1` to freeze those responses for offline replay.
7. Never present a real address holder as anything but "an exchange account" (CLAUDE.md rule 9). Don't put real third-party addresses in slides without need.

## 3. Demo script (≈ 5 min, fixture mode, `DEMO_CLOCK=2026-08-30T09:41:00+05:30`, `TRACE_PACING_MS≈500`)

| t | Screen | Say / do |
|---|---|---|
| 0:00 | Login | "Officers sign in with CCTNS or Parichay once we're onboarded; today, prototype login." Click PROTOTYPE BASED LOGIN |
| 0:20 | Docket | "Kulkarni's desk: 37 dockets, restraints to date." Point at Needs attention |
| 0:40 | Intake | Ingest `NCRP/2026/MH/0084213`; particulars fill in; tick review; Start trace |
| 1:10 | Live trace | "It follows the money, not the edges: most-tainted address first." Narrate peel at hop 2; terminal appears: 17,880.00 USDT credited to a Coinsphere account |
| 2:00 | Canvas | Click H4. Fingerprint: 187 senders, forwards 100%, swept in 4 min, hub fed by 214. "We identify the hub, and the hub explains every address feeding it." Show txids + explorer link |
| 3:00 | Verdict | Linked case MH/0081902 (same deposit address, other state). Tick four checks |
| 3:30 | Notice | Preview; deadline 24 h; request countersign → switch window as ACP Deshmukh → countersign → dispatch; show PDF hash |
| 4:15 | Risk check | H4 → alert; clean address → "absence of a record is not clearance" |
| 4:40 | Close | "Weeks to minutes, and an honest answer at every trail end." Limits slide |

Backup beats: open `/findings/<stationary>` to show the escalate variant; open the live-trace evidence page if asked "is this real data?".

## 4. Failure drills (run on D14, again on D17)

| Drill | Expected |
|---|---|
| Wi-Fi off, fixture mode | Full golden path works; no browser console errors |
| Live mode, Wi-Fi off, cache pinned | Pre-warmed live trace replays |
| TronGrid 429 in live mode | `source` shows degraded; engine backs off; trace completes or ends in `error` with Retry |
| Kill server mid-trace, restart | Snapshot marked failed(interrupted); Restart works |
| Browser refresh mid-trace | Stream resumes via Last-Event-ID |
| Projector 1366×768, browser zoom 125% | No horizontal scroll on main screens; canvas usable |
| SMTP down | Email channel → Failed, retry required; other channels unaffected |
| Second officer double-clicks countersign | One countersignature row (UNIQUE) |

## 5. Deployment runbook

**Local (primary):**
```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/make_fixture.py && python scripts/seed.py
uvicorn app.main:app --port 8000                       # single worker, no --reload on demo day
```
**Public link:** tunnel per D-06 (`cloudflared tunnel --url http://localhost:8000`, or ngrok). Test from a phone on mobile data.

**Backup (Hugging Face Spaces, Docker SDK):** `Dockerfile` installs system deps for the chosen PDF renderer, runs `make_fixture` + `seed` at build, serves on port 7860, `TRINETRA_MODE=fixture`. Spaces sleep when idle; open it 10 minutes before judging. Secrets (SMTP) via Space secrets, never in the image.

## 6. Pre-demo checklist

- [ ] `pytest -q` green on the demo laptop; `demo-v1` tag checked out
- [ ] `scripts/demo_reset.sh` run; DEMO_CLOCK set; pacing set
- [ ] Browser: zoom 100%, bookmarks bar hidden, second profile logged in as supervisor
- [ ] Tunnel URL + HF URL tested from a phone
- [ ] Test inbox open in a tab
- [ ] Live-evidence page and limits slide ready
- [ ] Laptop on power, notifications off, display sleep off
