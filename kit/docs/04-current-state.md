# 04 · Current state (11 Sep 2026)

## 1. Component matrix

Legend: **built** = exists and tested · **partial** = exists with gaps · **spec** = specified, not built · **planned** = v2.

| Layer | Technology | State | Note |
|---|---|---|---|
| Language | Python 3.11 | built | Single language |
| Chain data | TronGrid REST (trc20 transfers), TronScan (tags, tx lookup) | partial | Transfer fetch verified live; tag lookup built but not exercised against a real tagged address |
| Traversal | Hand-written best-first (`heapq`) | built | Replaced planned NetworkX |
| HTTP / cache | requests + requests-cache (SQLite) | built | Offline replay confirmed |
| Classifier | Bayesian LR, 6 signals, damped | partial | Not calibrated |
| Attribution | 5-rung ladder + JSON registry | partial | Ladder tested; **registry empty** |
| Case linkage | Union-Find | built | Excludes hot wallets |
| Tests | pytest | built | **25/25**, incl. normalise traps |
| Fixtures | Deterministic generator, seed 38 | built | 680 transfers, 482 accounts |
| Backend | FastAPI | spec | |
| Frontend | Jinja2 + Alpine.js | spec | Replaced Streamlit |
| Graph | Cytoscape.js + dagre (+ node-html-label, see docs/10 D-02) | spec | Replaced PyVis |
| Storage | SQLite + SQLModel | spec | |
| Streaming | SSE (sse-starlette) | spec | |
| Report / notice | One Jinja2 template → preview + PDF | spec | Renderer choice open (docs/10 D-01) |
| Auth | Session; CCTNS/Parichay portal links + prototype login | spec | Real SSO needs NIC / state police onboarding |
| Deployment | Local + tunnel; HF Spaces backup | planned | |
| Git | GitHub, feature branches, protected main | partial | `.gitignore` final; **not pushed** |

Existing spec artefacts in the repo (Sep build-spec package): `CLAUDE.md`, `docs/ARCHITECTURE.md`,
`docs/SCREENS.md`, `docs/demo_case.json`, `design/mockups` (00–07 + NavRailLight),
`design/screenshots`, `design/reference/login-final.png`, `design/reference/canvas-layout-reference.png`,
`assets/brand/trinetra-mark.png`, `assets/gov/state-emblem.*` (low-res). **This handoff replaces the
three markdown files; keep `demo_case.json`, `design/`, `assets/`.**

## 2. Numbers

| Metric | Value |
|---|---|
| Engine + test lines | ~1,912 |
| Tests passing | 25 / 25 |
| Verified registry entries | 0 |
| Deepest live-chain test | hop 0 |
| Fixture trace depth verified | 5 hops |

## 3. Verified against live TronGrid

- Decimal and ms-timestamp normalisation: correct amounts, 2026 dates.
- Seed-matching tolerance: correctly declines an unrelated nearby transfer (bug found in team testing, fixed, re-verified live).
- `.env` loading and live/fixture switching.
- Disk caching: repeat calls served from cache.

## 4. Not yet verified (gaps, not known defects)

| Gap | Why it matters | Fix (track in docs/08) |
|---|---|---|
| Multi-hop live trace | Both live tests ended at hop 0 (USDT contract itself; an address that hadn't moved funds yet) | T-LIVE: one trace from a mid-chain address that has already received and forwarded |
| Registry empty | Live runs can't say "this is Exchange X" beyond TronScan tags | T-DATA: populate with verified hot wallets |
| Classifier uncalibrated | A live posterior shown to judges is an unvalidated number | T-CAL: score known deposit addresses; report Precision@1 |
| TronScan tag path unexercised | Rung 2 untested live | T-LIVE: resolve one known tagged exchange address |
| Fixture addresses vs live chain | Synthetic, checksum-valid, seed-generated; not re-checked against current chain state | Check before public demo |

## 5. Accepted scope limits

TRON/USDT only · no bridge continuation · no pre-transaction module · no persistent store in the
engine (app's job) · no auth in the engine (app's job) · fixture addresses synthetic by construction.

## 6. Cut from the original teammate prototype

The first engine draft covered 8 chains through 4 providers, seeded from a tx hash only, and
followed the single largest recipient with no time window, value floor, taint accounting, or
classifier. Kept and rebuilt: follow-the-largest (now `dominant_fund_flow`), visited set, CLI.
Replaced: everything else. Detail in `engine/README.md`.

## 7. Documentation drift — resolved

Older documents and notes contradict the final state. Agents must use the right-hand column.

| Topic | Older source said | Final |
|---|---|---|
| UI | Streamlit | Jinja2 + Alpine.js, custom 8-screen design |
| Graph | PyVis; later "NetworkX" | Cytoscape.js + dagre in the browser; engine uses `heapq`, no graph lib |
| Backend | Flask (prototype); FastAPI sync | FastAPI; plain `def` routes, one async SSE endpoint |
| Report | ReportLab (+ pypdf) | One HTML template for preview and PDF; renderer per docs/10 D-01 |
| Frontend language | "No JavaScript on team" | Team is JS-comfortable; still no framework/build step |
| Login | Mock CCTNS IdP + TOTP + case-attach step | Single screen: CCTNS + Parichay links to real portals, PROTOTYPE BASED LOGIN; no TOTP, no WebAuthn, no attach step |
| Chains | 8-chain detection | TRON only; EVM/BTC detected → `unsupported_chain` |
| Linkage key | "keyed on sweep destination" (an old note) | Deposit addresses + non-service intermediates; **never hot wallets** (a sweep destination is usually a hot wallet) |
| Sanctions screening | Chainalysis Sanctions API listed | Not wired in v1; OFAC SDN local file + Chainabuse cache feed Risk Check only |
| Graph layouts | dagre and fcose | dagre LR only (Network/Case views cut for v1) |
| PDF footer | "PDF SHA-256 (first 16)" printed in the PDF | Impossible (a file can't contain its own hash). Footer prints snapshot SHA-256 + notice number; PDF SHA-256 is stored and shown on screen / in dispatch records |

## 8. Open PS-level gaps (judges will probe)

1. **AI/ML** is named in the PS. v1 answer: a probabilistic (Bayesian) classifier plus case memory; calibration with a measured metric is the planned credibility step (docs/10 D-09).
2. **Bitcoin**: three of the seven crime typologies named in the PS are Bitcoin-first. v1 says "adapter in progress"; a BTC adapter is on the v2 list after EVM.
3. **Complaint ingestion schema** with typology and payment timestamp closes several PS gaps at once; it's in the app data model (`complaint.typology`, `complaint.payment_ts`).
