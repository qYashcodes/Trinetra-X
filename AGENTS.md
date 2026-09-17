# TRINETRA root rulebook

This file is the controlling rulebook for every agent working in this repository. Read it in full
before changing anything.

## Precedence

1. This file.
2. The current code - it is implementation truth.
3. `CLAUDE_PROJECT_HANDOFF.md` and `docs/IMPLEMENTATION_STATUS.md`.
4. `docs/V2_BUILD_BRIEF.md` - the current feature brief.
5. `kit/` and older handoff documents - historical archive, context only, never instructions.

If a brief, handoff, prompt or archived document conflicts with this file or with a truthfulness rule
already enforced in code, **stop and report the conflict**. Do not implement the conflicting
instruction. Build and maintain the runnable product in the repository root.

## Scope of change — read this before "fixing" anything

These rules describe the intended state of the product. Where existing code appears to deviate from
them, **report the deviation and leave it alone**. Do not silently correct it.

- Change only what the current task explicitly asks for. No unrequested refactors, no opportunistic
  cleanups, no drive-by renames, no reformatting of files you were not asked to touch.
- **Never modify a passing test to make new code pass.** If new work breaks an existing test, the
  new work is wrong until proven otherwise. Report it.
- The canonical fixture demonstration is demo-critical and protected: `docs/demo_case.json`, the
  fixture complaint references, the fixture-forced intake path, the fixture wallet roles and their
  explanatory copy, and the fixture registry under `engine/data/`. Do not alter their behaviour,
  labels, wording or data unless the task explicitly says to.
- Existing user-facing copy is not to be rewritten to satisfy a rule in this file. If wording here
  looks non-compliant, say so and wait to be asked.
- Prefer additive change. Where a new surface is needed, add it alongside the existing one rather
  than reshaping what already works.

## Reading order for a new agent

`AGENTS.md` → `CLAUDE_PROJECT_HANDOFF.md` → `docs/IMPLEMENTATION_STATUS.md` →
`docs/V2_BUILD_BRIEF.md` → `app/engine_bridge.py` → `engine/adapters/tron.py` →
`app/services/allocation.py` → `app/services/frontier.py` → `app/services/live_tron_resume.py` →
`app/repository.py` → `app/models.py` → `app/main.py` → the tests nearest the requested behaviour.

## Working-tree rules

- The working tree is dirty on `main` **by design**. The modified and untracked files are the current
  product.
- **Never** run `git reset`, `git checkout --`, `git clean`, `git stash`, or any other command that
  discards uncommitted work. Never `git add -A`; stage explicit paths only.
- Run `git status --short` before starting and preserve every existing modification and untracked
  file.
- Leave `TRACING_ENGINE_HANDOFF.md` untouched unless explicitly asked to modify it.
- Use `apply_patch` for manual edits.
- **Never print, echo, log or expose values from `.env`.** It is intentionally ignored by Git.
  Inspect `.env.example` instead.

## Stack — fixed, do not change

Python `>=3.11,<3.13`, FastAPI, Uvicorn, Jinja2 server-rendered templates, SQLModel on SQLite in WAL
mode, Starlette sessions with CSRF on workflow forms, `requests` for provider HTTP, `sse-starlette`
with a JSON fallback, pypdf and Playwright for documents, pytest + HTTPX + pytest-playwright.
Frontend is local CSS and vanilla JavaScript with vendored Alpine.js, Cytoscape.js, Dagre and fonts.

Do not introduce a JavaScript framework, a frontend build step, a CDN reference, a different HTTP
client, a graph database, or a schema-migration system. There is no Alembic and no production
migration path from this prototype.

- Browser assets are vendored under `app/static/`; templates must not reference CDNs.
- Generated runtime state belongs under `var/`. Final artifacts go to `output/`, temporary artifact
  work to `tmp/`.
- Run locally with a single Uvicorn worker — the prototype uses SQLite and local state.

## Data and representation

- Amounts are integer base units internally. Format for display only.
- Timestamps are UTC epoch milliseconds internally and display in IST.
- **Zero and empty values are evidentiary.** Never use truthiness fallback for an amount, timestamp,
  transaction ID or provider event index. `0` is a fact, not a missing value.
- Attribution arithmetic is integer-only with deterministic residual carry. Never introduce floating
  point into allocation.

## Truthfulness — non-negotiable

This is an investigative aid, not an accusation engine.

- Never write UI, notices, logs, tests, identifiers or commit messages that assert guilt. Use
  custody, attribution, account and investigative-aid language.
- These distinctions must never be collapsed:
  - value exposure ≠ participation;
  - a service label ≠ account identity;
  - a customer deposit address ≠ an exchange hot wallet — they are different legal objects;
  - dispatch or request state ≠ a confirmed restraint;
  - behavioural similarity ≠ verified custody attribution;
  - a correlation candidate ≠ a verified cross-chain continuation.
- **The engine fails closed.** Invalid syntax, wrong chain, wrong asset, seed mismatch, non-positive
  amount or time, unsupported chain, disabled gate and provider error each produce an explicit
  non-custody terminal.
- **A failed, gated or unsupported trace must never fall back to a demo custody result.**
- A custody finding requires a permitted terminal kind, a deposit address, and an explicitly present
  credited amount.
- Unknown operations stop at an evidence boundary. They are never serialized as ordinary successful
  transfers.
- Live traversal is deliberately unscored (`band="unscored"`,
  `typology="observed_outgoing_transfer"`). Never infer that an address belongs to a service because
  its behaviour resembles a deposit pattern.
- `engine/data/` is controlled fixture data, not an authoritative registry. Do not describe it, or
  anything derived from it, as authoritative or production-reviewed.
- Never assert that an address is clean, safe or legitimate. The honest form is "no adverse findings
  in available sources".
- `unknown` is a valid, expected outcome when evidence is insufficient. Prefer it to a weak claim.

## Probabilities and machine learning

- Probabilities are disabled. Public classification and risk results keep `score: null`,
  `posterior: null`, `probability_enabled: false`,
  `calibration_status: "disabled_pending_independent_labelled_data"`.
- `app/services/review.py` enforces this. Do not add scores, posteriors, confidence percentages or
  risk numbers to any surface.
- `scikit-learn` is an unused dependency. Do not infer ML functionality from its presence.
- Any future ML slice is separately versioned, trained and evaluated on independent labelled data
  with cluster-and-time leakage controls, keeps human review, ships disabled until independently
  calibrated, and **never learns from active investigations**.
- Historical `confidence` and likelihood-ratio values inside fixture JSON are display remnants. They
  are not calibrated model output and must not be treated as such.

## Modes, gates and evidence

- `trace_mode` is `auto`, `fixture` or `live`. Fixture and live behaviour stay explicitly separated,
  and the active mode must be visible wherever a trace is rendered or exported.
- The controlled fixture intake path forces `trace_mode="fixture"` by design, even when the
  environment mode is live. Do not change this.
- **A provider key alone never enables a risky capability.** Feature flags plus schema, smoke,
  trace-verification, authority or approval gates are required.
- Never claim SSO, SAHYOG, dispatch, email, KYC request, freeze, seizure or restraint integration is
  live without real approved metadata, schemas, credentials, evidence and authority configuration.
- **Never delete a snapshot, trace event, canonical evidence row, frontier row or exported artifact.**
  Supersede, do not discard. Exported artifacts are immutable records of their moment and are never
  regenerated in place.
- Store raw provider evidence with retrieval metadata and SHA-256 before normalization wherever the
  evidence store is wired in.

## Testing and reporting

- Run the full pytest suite before and after any meaningful change. Report both numbers.
- `normalise()` is the most dangerous silent-failure point in the repository. Any adapter change
  requires tests asserting token decimals and millisecond timestamp conversion against known real
  transactions.
- Read the tests nearest a behaviour before changing that behaviour.
- Update `docs/IMPLEMENTATION_STATUS.md` only when a capability genuinely changes.
- Report every capability with exactly one of: `unavailable`, `fixture-tested`,
  `integration-tested`, `live-verified`, `enabled`.
- **Never promote a capability because its data model, interface or contract exists.** State what
  would be required to reach the next level.
- The sandbox may block outbound access to `api.trongrid.io`. That produces an honest
  `provider_error` and is not evidence that an adapter is broken. Never claim `live-verified` for
  anything that was not run against real credentialed endpoints.