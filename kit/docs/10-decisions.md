# 10 · Decisions

Three sections: what is decided (don't relitigate without new facts), what I recommend changing
(needs the lead's call), and open questions. Update this file when anything is decided; agents read it.

## A. Decided

| # | Decision | Why |
|---|---|---|
| A-01 | Python 3.11 only on the server | One language across six members; every line explainable |
| A-02 | FastAPI, plain `def` routes, one async SSE endpoint | Auto docs at `/docs` as a fallback demo surface; no async sprawl |
| A-03 | Jinja2 + Alpine.js, no build step (replaces Streamlit) | Custom 8-screen design Streamlit can't produce; no Node toolchain |
| A-04 | Cytoscape.js + dagre LR (replaces PyVis) | Custom card nodes, compound nodes, click sync, streaming |
| A-05 | SQLite + SQLModel; seed script instead of migrations | Tens of cases; zero ops |
| A-06 | requests-cache on disk (not Redis) | Offline demo resilience, zero code change in the engine |
| A-07 | `heapq` best-first traversal (not NetworkX, not Neo4j) | 15–40 nodes per trace; a priority queue is the algorithm |
| A-08 | Bayesian likelihood ratios, damped (not additive scores) | Combinable, explainable per signal, principled confidence |
| A-09 | Haircut taint | Correct under mixing; handles peel chains and consolidation |
| A-10 | TRON / USDT-TRC20 first, adapter interface for others | Where PS typologies concentrate |
| A-11 | Linkage never keys on hot wallets | Otherwise every case at one exchange becomes a false syndicate |
| A-12 | Scores with reasons, never labels; "account, not person" | Wrongly implicating an account holder is the real harm |
| A-13 | Login: CCTNS/Parichay link to real portals + PROTOTYPE BASED LOGIN; no TOTP/WebAuthn/attach step | Honest about onboarding status; no imitation of government forms |
| A-14 | Government-system realism in UI (emblem, letterhead, NCRP pill) + disclaimer on every screen | Show the deployed product; pitch states access is pending |
| A-15 | PDF watermark ON by default (`NOTICE_PDF_WATERMARK`), preview clean | The PDF is the artefact that leaves the room; emblem-misuse and notice-impersonation risk |
| A-16 | Light theme only in v1; dark, Hindi, WebAuthn deferred | Smaller, defensible demo surface |
| A-17 | Offline-first, all assets vendored | Venue Wi-Fi |
| A-18 | Integer base units for money, epoch ms for time | Kills float and timezone bugs at the root |
| A-19 | TraceRunner thread + persisted event log + SSE tail with `Last-Event-ID` | Survives refresh, replays instantly, keeps blocking engine off the event loop |
| A-20 | Server-side SVG graph exhibit for evidence | Deterministic; independent of browser canvas export |
| A-21 | ₹0 infrastructure; commercial forensics platforms excluded | Deployable to district cells without procurement |

## B. Recommended changes (lead to decide)

### D-01 · PDF renderer — **recommend Playwright (headless Chromium)**
| Option | For | Against |
|---|---|---|
| wkhtmltopdf + pypdf (current preference) | Familiar; simple Windows installer | Upstream archived (2023); old QtWebKit: no CSS grid, unreliable flexbox, ES5 only. The notice template must be written in a second, older CSS dialect, so "preview == PDF" only holds if the preview is also crippled |
| WeasyPrint | Excellent paged-media CSS (`@page`, running footers, `counter(page)`); pure Python API | Windows needs GTK/Pango setup; no JS; its layout still differs slightly from the browser preview |
| **Playwright Chromium** | Same engine as the preview → true parity; modern CSS; built-in header/footer templates with page x of y; `pip install playwright && playwright install chromium` on every OS; **already a dependency for e2e tests** | ~150 MB browser download (do it before the venue); keep one browser instance warm (~0.5–1 s per render); bigger Docker image |

pypdf stays for the watermark/metadata if needed. Whatever wins: the footer carries the notice
number + snapshot SHA-256, never the PDF's own hash (impossible by construction).

### D-02 · Canvas node cards
`cytoscape-node-html-label` renders DOM overlays: fast to match the mockup, but it's old and
unmaintained, can drift/blur during zoom, and **isn't included in `cy.png()`**. Recommendation: use it
for the on-screen canvas (≤ 60 nodes keeps it smooth), and never use `cy.png()` for evidence; the
exhibit comes from `services/exhibit.py` (A-20). If drift shows up in P4, switch cards to generated
SVG `background-image` data URIs (native canvas, exportable) — same data, different renderer.

### D-03 · HTMX for server-rendered partials — **recommend adopting, narrowly**
Docket filtering, right-panel tabs, notice-rail statuses, and risk results are "fetch → rebuild a
chunk of HTML". With HTMX that chunk is a Jinja partial (Python-side, explainable) instead of JS
templating. Alpine keeps local state; custom JS only for Cytoscape and the SSE page. Cost: one ~14 KB
vendored file and a second idiom. If adopted, add it to CLAUDE.md §3. If not, Alpine + `fetch` is fine.

### D-04 · Say what the data is — **recommend a footer data-mode indicator**
"Data: fixture replay" / "Data: live TRON (cached 15 min)". Quiet, not a banner. It doesn't break
the government-system look, and it turns "is this real data?" into a strength (then show the live
evidence page). Hiding it contradicts the team's own rule that a disclosed limit reads as competence.

### D-05 · Fixture pacing
Fixture traces finish in milliseconds; the Live Trace screen would flash. `TRACE_PACING_MS`
(fixture only) spaces events so the audience can follow. Pair it with D-04 so pacing is never
mistaken for network time.

### D-06 · Public tunnel — **recommend cloudflared quick tunnel, ngrok as backup**
ngrok's free tier shows a browser warning interstitial on first visit (bad on a judge's phone).
`cloudflared tunnel --url http://localhost:8000` needs no account and has no interstitial. Both pass SSE; keep GZip off the stream route.

### D-07 · Docker only for the HF backup
Local dev and the demo laptop run natively. One `Dockerfile`, maintained by the lead, for the HF Space.

### D-08 · Record the custodian's reply (added to P5)
The docket shows "restrained" amounts and ladder rung 4 depends on "a VASP replied to a prior
notice", but no screen captured replies. Added a "Record custodian response" action. Without it,
case memory (the honest ML-adjacent claim) has no input.

### D-09 · The AI/ML answer — **recommend measured calibration before any "ML" claim**
1. **Positives:** addresses whose outflows sweep into a registry hot wallet (from T-DATA). **Gold positives:** team members' own exchange deposit addresses (controlled deposits; small USDT + fees, not ₹0 — budget it).
2. **Negatives:** ordinary active wallets, known merchant/gateway addresses, labelled hot wallets themselves.
3. **Leakage caveat:** if positives are defined by "sweeps into a labelled hot wallet", then destination-consistency and hub-feed partly encode the label. Report metrics with and without those two signals.
4. **Fan-in caveat:** an ordinary customer's deposit address has few senders; high fan-in is typical of deposit addresses used for cash-out (mules, P2P sellers). Expect sweep timing, forward ratio, balance and hub feed to carry detection, and fan-in to be weak on gold positives. Check this before defending fan-in as a core signal.
5. **Metrics:** precision/recall at 0.60 and 0.85; **Precision@1** for custodian attribution on traces with known ground truth.
6. **Then** fit a logistic regression over the same six features as a comparison. If it wins, it slots in behind `classify()`; if not, the Bayesian model with measured numbers is the stronger story.
Pitch line: *"A probabilistic classifier with published likelihood ratios, calibrated on self-labelled ground truth, plus case memory that improves with every exchange reply."*

### D-10 · Footer accessibility claim
The mockup footer claims WCAG 2.1 AA and GIGW 3.0. Make it true in P7 (axe pass) or remove the claim. A judge from NIC may test it.

### D-11 · CCTNS link target — **fix before demo**
The supplied `https://cctns.megpolice.gov.in/Login.aspx` is **Meghalaya** Police's CCTNS, while the
letterhead and officer are **Maharashtra**. A judge who hovers or clicks will notice. Data/legal member
to find the correct Maharashtra (or national) CCTNS entry point and verify it loads. Also the Parichay
link uses a placeholder `sid=1234567899`, which may land on an error page; test it and, if so, link
the Parichay landing page without `sid`.

### D-12 · State Emblem asset
Replace the 69×104 raster with a high-resolution vector before any printed or projected output.

## C. Open questions

| # | Question | Blocks |
|---|---|---|
| Q-1 | Internal round date (and Round 2 submission date)? | Real calendar for docs/08 |
| Q-2 | Who drives the build: lead running Claude Code phase by phase, or members running parallel agent sessions on separate phases? | Branching, phase splitting, review load |
| Q-3 | Demo laptop OS and screen/projector resolution? | D-01 install path, P7 targets |
| Q-4 | D-01 renderer? | P5 |
| Q-5 | D-03 HTMX yes/no? | P0 vendoring, P4/P6 approach |
| Q-6 | D-04 data-mode indicator yes/no? | P0 footer |
| Q-7 | Show live mode on stage at all, or keep it as a backup page? | P8, demo script |
| Q-8 | Budget for controlled deposits (T-CAL gold positives)? | T-CAL |
| Q-9 | Does any real exchange's LE process (e.g. published law-enforcement portals) deserve a named `DispatchChannel` stub for the pitch? | P5 copy only |
