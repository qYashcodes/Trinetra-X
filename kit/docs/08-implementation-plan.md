# 08 · Implementation plan

Goal: the full 8-screen application on top of the existing engine, demo-ready offline, with one
verified live trace as backup proof. Build **vertically**: each phase ends runnable end to end.

Calendar assumption: **15 build days + 3 rehearsal days**, Day 1 = the day Phase 0 starts. Map to
real dates once the internal-round date is fixed (docs/10 Q-1). Code freeze at the end of D15.

## 0. Day-zero setup (lead, ~2 h, before any agent session)

- [ ] Push repo to GitHub; protect `main` (PR + passing checks); add all six members.
- [ ] Tag the engine baseline `engine-v1.0` (so any app-side regression is diffable).
- [ ] GitHub Actions: `pytest -q` on push/PR (fixture mode, no keys).
- [ ] Copy this handoff: `CLAUDE.md` (+ `AGENTS.md` duplicate) to root, `docs/0*.md` into `docs/`, delete superseded `docs/ARCHITECTURE.md` and `docs/SCREENS.md` (keep `demo_case.json`).
- [ ] Decide D-01 (PDF renderer) and D-02 (node rendering) from docs/10 — they affect P4/P5.
- [ ] Run the kickoff prompt (README) → review `ENGINE_MAP.md`.

## 1. Critical path

```
P0 skeleton → P1 data/seed → P3 intake+live trace ─→ P4 canvas+verdict ─→ P5 notice+dispatch → P7 polish → P8 hardening
                   └→ P2 auth (small, anytime after P1)        P6 docket+risk (after P3; parallel with P5)
Parallel from D1:  T-DATA registry · T-LIVE verification · T-CAL calibration · T-LEGAL notice text · T-QA drills · T-PITCH
```
The bridge + TraceRunner (P3) is the riskiest integration; it gets the lead's attention first.

## 2. Phases

### P0 · Skeleton — D1–D2 · owner: Lead + UI
- [ ] `app/main.py` factory, `config.py` (Settings from .env), static mount, Jinja2 env with `money`/IST/truncate filters (`app/money.py`).
- [ ] Vendor Alpine, Cytoscape, dagre, cytoscape-dagre (+ node-html-label if D-02 keeps it), Noto fonts; `VERSIONS.txt`.
- [ ] `tokens.css`, `base.css`, `components.css` (chips, KPI strip, tables, mono data cell with copy, rails, drawers, dialogs).
- [ ] `base.html` + nav rail + header + footer matching screenshots; A-/A/A+.
- [ ] Route shells for all 8 screens with correct nav highlight; `/healthz`.
- [ ] `engine_bridge.py` with every contract function wired or stubbed per `ENGINE_MAP.md`.

**Done when:** every route renders its chrome identically to the screenshot at 1620×972 and without horizontal scroll at 1366×768; network tab shows zero external requests; `pytest -q` green.

### P1 · Data, seed, audit — D2–D3 · owner: Backend
- [ ] `models.py` (docs/05 §6), `db.py` pragmas, `money.py` base-unit helpers.
- [ ] `scripts/seed.py`: rebuild `var/trinetra.db` from `demo_case.json` — officers, entities, attribution set, main complaint (`awaiting_trace`), other cases with minimal stored snapshots, referrals; `--filler N` closed cases so desk counts look real.
- [ ] `audit.py` append + `verify_chain()`.
- [ ] Tests: base-unit round-trip (incl. 6-decimal edge values), IST formatting, audit chain detects a tampered row, seed idempotent.

**Done when:** `python scripts/seed.py && pytest -q` green from a clean clone; docket counts computed from DB match `demo_case.json`.

### P2 · Auth — D3 · owner: Backend
- [ ] `login.html` per `login-final.png`; SSO links from config; PROTOTYPE BASED LOGIN.
- [ ] Session + CSRF middleware, `current_officer`, `require_role`; unauthenticated → `/login`.
- [ ] `/auth/prototype/supervisor` (unlinked), `/auth/callback/{provider}` pending page, logout, `/me/activity`.
- [ ] `auth_event` + audit rows.

**Done when:** both officers can sign in (two browsers at once); every protected route redirects when signed out; CSRF-less POST is rejected.

### P3 · Intake → Live trace — D4–D6 · owner: Lead (bridge/runner), UI (screens)
- [ ] `integrations/complaints/ncrp_fixture.py` (fetch, referrals_today, health).
- [ ] Intake screen: validation, ingest, sequential particulars, referrals table, review checkbox gating, chain detection panel.
- [ ] `runner.py` TraceRunner (docs/05 §3): thread, semaphore, event persistence, notifier, completion → hops/finding/result/sha256, failure path, startup recovery, fixture pacing.
- [ ] `routes/sse.py` stream with `Last-Event-ID` replay + heartbeat.
- [ ] Live Trace screen: three columns, event handlers, bottom bar, restart dialog + supersede.
- [ ] Case stage transitions + audit.
- [ ] Tests: bridge contract test (fixture trace yields `terminal.kind == "vasp_deposit"`, credited 17,880.00, 5 hops); SSE replay after disconnect returns identical sequence; restart supersedes; failure yields `error` event.

**Done when:** ingest `NCRP/2026/MH/0084213` → Start trace → five hops stream → `vasp_deposit` at Coinsphere, 17,880.00 USDT, 81.5%; refresh mid-trace resumes; refresh after completion replays instantly; numbers match the oracle (or the oracle is updated to the engine's output, with a note).

### P4 · Canvas + verdict — D6–D9 · owner: UI (canvas), Backend (APIs)
- [ ] `/api/traces/{id}`, `/api/addresses/{addr}`, notes API.
- [ ] `canvas.js`: build elements from snapshot, dagre LR, node cards, edge labels, parked-branch collapse/expand, toggles, zoom/fit, ←/→ stepping, selection store shared with timeline + right panel.
- [ ] Right panel tabs (Overview fingerprint, Transactions, Attribution, Notes); explorer links via bridge.
- [ ] Bottom tabs: timeline, fund summary (must reconcile to reported amount).
- [ ] `services/exhibit.py` SVG exhibit + `/api/snapshots/{id}/exhibit.svg`.
- [ ] Verdict: view model per terminal kind (all 10 variants), four review checks with persistence, gating.
- [ ] Tests: fund summary sums to reported; review gating server-side (API refuses notice draft with < 4 checks); each terminal variant renders (use the `stationary_funds` referral + synthetic snapshots for the rest).

**Done when:** clicking any node, timeline card, or arrow step selects the same hop everywhere; exhibit SVG is byte-identical across two renders of the same snapshot; every terminal variant has a screenshot in `tests/app/snapshots/`.

### P5 · Freeze notice, dispatch, custodian response — D9–D11 · owner: Backend + Legal (copy)
- [ ] `notice_document.html` + `report_document.html` (trace report with **verification appendix**: every txid, explorer URL, snapshot SHA-256, engine version, the CLI command that reproduces the trace).
- [ ] `pdf.py` per D-01: render, footer (notice no., page x of y, snapshot SHA-256 short), watermark if configured, store under `var/pdf/`, SHA-256 of final bytes.
- [ ] Notice numbering, deadline text, countersign workflow + rules, supervisor queue.
- [ ] Dispatch: `EmailChannel` (SMTP, test inbox, PDF attached), `MockLEPortal` (returns request id), nodal copy; statuses, retry.
- [ ] Record custodian response → `vasp_response`, stage `restraint_confirmed`, feeds rung 4 lookup and docket values.
- [ ] Tests: IO cannot countersign own notice; non-supervisor rejected; below-threshold notice needs no countersign; PDF hash on screen == `sha256sum` of download; dispatch blocked before countersign.

**Done when:** full flow IO → supervisor (second browser) → dispatch → email arrives with PDF → response recorded → docket shows restraint; preview and PDF are visually the same document.

### P6 · Docket + risk check — D11–D12 (parallel with P5) · owner: Backend + UI
- [ ] `services/cases.py` computed KPIs, stage chip counts, attention flags, search.
- [ ] Linked cases via `engine_bridge.linked_cases` over stored snapshots.
- [ ] `services/risk.py` scoring (docs/07 §07), advisory lookups (OFAC local file, Chainabuse cache), recent checks, audit.
- [ ] Tests: the three `risk_check_examples` land in their bands; linkage never merges on a hot wallet (construct two cases sharing only HW → not linked); overdue derivation with `DEMO_CLOCK`.

**Done when:** after tracing the main case, `MH/0081902` appears under Linked cases; Risk Check examples match expected bands.

### P7 · Polish — D13 · owner: UI + QA
- [ ] Fluid layout 1366×768 → 1920×1080; projector check at 1024×768 degrades gracefully (rails collapse).
- [ ] Keyboard pass (focus ring, tab order, Esc closes dialogs/drawers), `prefers-reduced-motion`.
- [ ] axe/Lighthouse accessibility pass; fix contrast issues (the footer claims WCAG 2.1 AA and GIGW 3.0: make it true or remove the claim).
- [ ] Empty/loading/error states for every screen; 404/500 templates.
- [ ] Copy pass against docs/07 §1 and CLAUDE.md rule 9 (grep for banned words).

**Done when:** Playwright golden-path e2e passes at both resolutions; zero axe "serious" issues.

### P8 · Demo hardening — D14–D15 · owner: QA + Lead
- [ ] Offline drill: Wi-Fi off, fixture mode, full golden path.
- [ ] Live backup: pre-warm cache for the verified live trace (T-LIVE), `TRINETRA_CACHE_EXPIRE=-1`.
- [ ] Tunnel (D-06) + HF Spaces Docker backup (D-07) deployed and smoke-tested from a phone.
- [ ] `scripts/demo_reset.sh`: reseed, clear `var/pdf`, set `DEMO_CLOCK`, start server.
- [ ] Tag `demo-v1`; **code freeze**.

**Done when:** three consecutive clean run-throughs on the demo laptop, one of them offline, one through the tunnel.

### D16–D18 · Rehearse, break, fix bugs only
Demo script (docs/09), Q&A drill against docs/02 §7–8 and docs/04 §4, every member explains one screen and its backing function.

## 3. Parallel tracks (non-app, start D1)

| Track | Owner | Deliverable | Done when |
|---|---|---|---|
| **T-DATA** registry | Data | `vasp_registry.json` per docs/06 §7: hot wallets for major exchanges serving Indian users and every FIU-IND-registered VASP with TRON USDT activity; FIU-IND status table (domestic / offshore / non-compliant) | ≥ 30 verified addresses, each with a `source_ref` a second member opened; live trace of one known exchange address resolves at rung 1 |
| **T-LIVE** verification | Lead | One live multi-hop trace from a mid-chain address that has received and forwarded; one TronScan-tag resolution | Trace ≥ 2 hops live, terminal reached, cached for replay, screenshots saved |
| **T-CAL** calibration | Python dev | Labelled set + evaluation script (`scripts/evaluate_classifier.py`): positives = addresses that swept into a registry hot wallet; negatives = ordinary active wallets, merchants; report precision/recall at 0.60 and 0.85 and Precision@1 for VASP attribution | A metrics table the team can put on a slide (docs/10 D-09 for the leakage caveat) |
| **T-LEGAL** | Investigator | Legal-basis paragraph, requests list, deadline wording, copy-to list, stationary-funds issuer-freeze template, verdict copy per terminal kind | Signed off text in `app/copy.py` / notice template |
| **T-QA** | QA | Test matrix, failure drills (docs/09 §4), demo script timing | Drills pass; script ≤ 5 min |
| **T-PITCH** | Lead | Deck incl. "every trail end is an output" table, limits slide, metrics slide | Rehearsed twice |

## 4. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Registry still empty at demo | Medium | High (live demo can't name exchanges) | T-DATA from D1; fixture demo carries the story; live backup uses a tagged exchange |
| Live multi-hop reveals an engine bug | Medium | High | T-LIVE in week 1, not week 3; engine fixes via rule 2 with a failing test first |
| Classifier score challenged | High | Medium | T-CAL metrics; show signals and reasons, not just a number |
| Trace streaming fragile (threads/SQLite) | Medium | High | TraceRunner design + replay tests in P3; single worker |
| PDF renderer install pain (Windows laptops) | Medium | Medium | Decide D-01 on D0; install on the demo laptop on D1 |
| Canvas performance / label drift at zoom | Low | Medium | ≤ 60 nodes by design; D-02 |
| Venue Wi-Fi / TronGrid 429 | High | Low | Fixture mode + pinned cache; live is backup only |
| Judges click CCTNS link and see an unrelated state's portal | Medium | Medium | D-11 |
| Emblem misuse perception | Low | High | PDF watermark default on; disclaimer on every screen |
| Scope creep in the last week | High | High | Code freeze D15; v2 list below is where ideas go |

## 5. After the internal round → Round 2 / Grand Finale (36 h sprint) backlog

Priority order, from the final brief plus gaps found here:
1. Registry breadth (never stops).
2. EVM adapter (chain-family resolution: check which EVM chain has activity).
3. Linked-cases UI depth + gas-funding clustering.
4. Bridge continuation candidates with amount-uniqueness scoring.
5. Classifier calibration → optional scikit-learn model behind `classify()` / `services/risk.py`.
6. Pre-transaction risk check against live chain for unseen addresses (wallet age, sender count, funding pattern, forwards-everything, touched known-scam addresses) — framed as a screening signal for exchanges/P2P platforms, never an accusation.
7. BTC adapter (three PS typologies are Bitcoin-first).
8. App polish: dark theme, Hindi/GIGW bilingual, WebAuthn, fuzzy search, real SSO after onboarding, SAHYOG payload export.

Grand Finale tactic: pre-build each item behind a feature flag on a branch so the 36 h is integration and polish, not greenfield.
