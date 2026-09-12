# TRINETRA — rules for coding agents

Law-enforcement tool for Indian cyber cells. An officer opens a case from an NCRP complaint; the
engine traces USDT-TRC20 on TRON forward from the reported address to the earliest KYC-regulated
exchange deposit address; the officer issues a freeze notice to that exchange with verifiable
transaction hashes. SIH 2026, PS 26183, Team 38 (Trinetra), BPIT.

Full context lives in `docs/`. This file is the rulebook. If a doc and this file disagree, this file wins; flag the conflict.

---

## 1. Hard rules

1. **Do not rewrite the engine.** `engine/` is built and tested (25 tests). The app imports it
   only through `app/engine_bridge.py`. If the bridge needs something the engine lacks, write a
   stub in the bridge that raises `NotImplementedError("TODO_ENGINE: <what>")` and list it in
   `TODO_ENGINE.md`. Never write a second tracer, classifier, adapter, or linkage.
2. **Engine changes need explicit permission.** If an engine bug blocks you, stop, describe it,
   propose a minimal diff plus a failing test. Do not apply it unasked.
3. **No frontend framework, no build step.** Jinja2 + Alpine.js + plain CSS + Cytoscape.js.
   No React/Vue/Svelte/Vite/webpack/npm scripts/Tailwind/TypeScript/CSS frameworks.
4. **Offline-first.** Everything the browser loads is vendored in `app/static/`. No CDN, no Google
   Fonts, no runtime fetch to any third party from the browser. Fixture mode must run with Wi-Fi off.
5. **One data source.** Seed from `docs/demo_case.json`. Counts, KPIs, ages, deadlines, "overdue"
   and "needs attention" are computed at request time, never typed into templates.
6. **Mockups are a visual spec, not code.** Match layout, spacing, colour, copy, states. Never copy
   code or constants from `design/mockups/*.dc.html`. Never open `_preview_runtime/`.
7. **Money is never a float.** Amounts are integer base units (USDT: 6 decimals) in storage and
   `Decimal` in Python; formatted only at render (`17,880.00 USDT`). Timestamps are stored as UTC
   epoch milliseconds and displayed in IST with the suffix `IST`.
8. **Evidence integrity.** Every hop shows its tx hashes with explorer links. Every trace snapshot
   stores `sha256(canonical_json(result))`. Every generated PDF's SHA-256 is stored and shown. The
   audit log is append-only and hash-chained. A document never claims to contain its own hash.
9. **Precise language.** A deposit address identifies an exchange *account*, not a person. Never
   write "fraudster", "scammer", "criminal", "suspect wallet owner" in UI copy, notices, logs, or
   test names. Scores are investigative aids, not findings of guilt.
10. **Honest terminal states.** Every trace ends in a designed terminal state (docs/06 §3). None
    renders as a crash, a blank screen, or a stack trace.
11. **Login.** CCTNS and Parichay buttons are links to the official portals only. Never build a
    page that imitates their login forms or accepts their credentials. The demo path is the
    PROTOTYPE BASED LOGIN button.
12. **Government integrations sit behind adapters.** NCRP feed, LE portal, dispatch channels:
    Protocol in `app/integrations/<x>/base.py`, implementations named `Fixture…` / `Mock…`,
    selected by `.env`. Swapping to a real integration must be a config change.
13. **Secrets.** Keys live in `.env` only. Never commit `.env`, `trinetra.db`, `.cache/`, generated
    PDFs, or anything under `var/`.

## 2. Stop and ask when

- The engine repo is missing, tests fail before you've changed anything, or a bridge function has no engine equivalent.
- A requirement here conflicts with a mockup, screenshot, or `demo_case.json`.
- You want to add a dependency not listed in docs/05 §2.
- A phase's Done-when check cannot pass without changing scope.
- You are about to delete, rename, or restructure an existing directory.

## 3. Stack (locked; changes go through docs/10)

Python 3.11 · FastAPI + Uvicorn (single worker) · Jinja2 · SQLModel on SQLite (WAL) ·
sse-starlette · requests + requests-cache (engine) · Alpine.js 3 · Cytoscape.js 3 + dagre +
cytoscape-dagre · HTML→PDF renderer per docs/10 D-01 (default until decided: wkhtmltopdf + pypdf) ·
smtplib · pytest + Playwright (e2e smoke).

Not allowed: Redis, Celery, Postgres, Neo4j, WebSockets, NetworkX in the app, ReportLab for
notices, any JS/CSS framework, Alembic (the seed script rebuilds the DB).

## 4. Working protocol

- One phase per session (docs/08). Build vertically: every phase ends runnable end-to-end.
- Before coding a phase: restate its Done-when checks. After: run them and report results verbatim.
- `python -m pytest -q` must pass at the end of every phase (engine tests included).
- Commit per phase: `phase-N: <summary>`. Never commit to `main` directly once protected; use `feat/phase-N`.
- Every function you write must be explainable line by line by a Python-first student. Prefer
  plain functions and dicts over clever abstractions. No metaclasses, no decorators you wrote yourself, no async except the SSE endpoint.
- Keep all user-facing strings for a screen at the top of its template or in `app/copy.py` so a
  Hindi/GIGW pass later is mechanical.

## 5. Known traps (read before touching data)

- TronGrid `value` is an integer string in base units; divide by `10**token_info.decimals`.
  `block_timestamp` is **milliseconds**. Both fail silently. The engine's `normalise()` handles
  this and raises instead of guessing; never re-normalise in the app.
- The deposit address is the legally significant object. The hot wallet is a landmark one hop later. Never display one address as both.
- Parked branches are "off the dominant flow", not "below the value floor". Dust is below the floor.
- `0x…` means the whole EVM family, not Ethereum. v1 returns `unsupported_chain`.
- The value floor is 2% of the reported amount (438.80 USDT for the demo), not a fixed 250 USDT.
- Don't wrap the SSE route in GZip middleware; it buffers the stream.
- SQLite + threads: `check_same_thread=False`, WAL, `busy_timeout=5000`, one writer per trace.
- Coinsphere Global and all demo entities are fictional and exist in fixture mode only.
