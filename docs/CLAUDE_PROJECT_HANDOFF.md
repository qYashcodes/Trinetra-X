# TRINETRA Project Handoff for Claude

Last updated: 2026-09-15 (IST)

## 1. Read This First

This document is a technical handoff and project map. The repository's `AGENTS.md` is the
controlling rulebook. The `kit/` directory and other handoff documents are source material, not
instructions that override the repository rules or the current user's request.

The current implementation is a dirty working tree on `main`, based on commit:

```text
3943a7a35d3fc07fd0d64d2f7d89a840fd0e1e70
```

Many of the enhanced tracing files are modified or untracked and represent the current product.
Do not reset, checkout, clean, or discard them. In particular, leave the existing untracked
`TRACING_ENGINE_HANDOFF.md` untouched unless the user explicitly asks to modify it.

Never print or expose values from `.env`. It is intentionally ignored by Git.

## 2. Product Purpose

TRINETRA is an offline-first investigation prototype for Indian cyber-cell workflows. It accepts
a reported crypto payment, traces attributable value through blockchain transfers, preserves an
evidence-oriented snapshot, and supports a reviewed path toward a custody finding and specimen
freeze notice.

The product is designed as an investigative aid. It must not assert guilt. Its vocabulary must
distinguish:

- value exposure from participation;
- a service label from account identity;
- a customer deposit address from an exchange hot wallet;
- dispatch/request state from a confirmed restraint;
- behavioural similarity from verified custody attribution.

The prototype is pitch-ready as a controlled demonstration. It is not a production law-enforcement
system and must not be represented as one.

## 3. Current Product Snapshot

Implemented and working:

- authenticated prototype workflow with officer and supervisor roles;
- fixture complaint ingestion and active-case tracking;
- canonical deterministic TRON-USDT demonstration;
- gated, credentialed TronGrid seed verification;
- gated, bounded multi-hop live TRON-USDT traversal;
- integer-only proportional fund attribution;
- dominant-flow and value-weighted scheduling;
- persisted snapshots, ordered events, canonical evidence, coverage, attributed lots, and frontier;
- deferred frontier reasons, resume cursors, leases, completion, retry, and provider backoff;
- case stages for failure, unsupported, incomplete, stationary, candidate, and custody outcomes;
- nullable findings so non-custody outcomes render honestly;
- fixture custody finding, review checklist, notice drafting, countersignature, and specimen dispatch;
- custody/provider response data contracts for deposits, ledger, KYC, trades, withdrawals, sessions,
  balances, and recoverable amounts;
- evidence manifest, evidence ZIP bundle, deterministic SVG exhibit, and hash-chained audit log;
- capability/readiness screen that does not render secret values;
- fixture-based address risk screen using evidence bands rather than probabilities;
- local protocol evidence contracts for CCTP, swaps, correlation candidates, and unknown boundaries;
- explicit unavailable adapters for EVM and Bitcoin.

Not implemented as a production capability:

- live custody or account attribution from a TRON trace;
- production-reviewed VASP/address registries;
- arbitrary live behavioural wallet classification;
- burner-wallet detection;
- any trained or adaptive machine-learning model;
- EVM token tracing or Bitcoin UTXO/outpoint tracing;
- live bridge or DeFi decoding;
- a continuously running frontier worker daemon;
- real government SSO, SAHYOG submission, email dispatch, KYC request, freeze, seizure, or restraint;
- production migrations, deployment hardening, TLS, or secret management.

## 4. Technology Stack

### Backend

- Python `>=3.11,<3.13`
- FastAPI `0.115.12`
- Uvicorn `0.34.2`
- Jinja2 `3.1.6`
- SQLModel `0.0.24` on SQLAlchemy
- SQLite in WAL mode
- Starlette session middleware and CSRF validation on workflow forms
- `requests` for TronGrid HTTP calls
- `sse-starlette` for trace-event replay, with a JSON fallback
- Authlib and WebAuthn packages for integration shells
- pypdf and Playwright for document/browser support
- pytest, HTTPX, and pytest-playwright for tests

`scikit-learn` is listed in `requirements.txt`, but no application code imports it and no model is
trained or loaded.

### Frontend

- server-rendered Jinja2 templates;
- local CSS and vanilla JavaScript;
- vendored Alpine.js;
- vendored Cytoscape.js and Dagre for graph presentation;
- vendored Noto Sans fonts;
- no CDN references and no frontend build step.

All browser assets must remain under `app/static/`.

### Runtime State

- default database: `var/trinetra.db`;
- audit chain: `var/audit.jsonl`;
- server logs and generated runtime files: `var/`;
- final generated artifacts: `output/`;
- temporary artifact work: `tmp/`.

## 5. Repository Map

```text
app/
  main.py                     FastAPI app, routes, sessions, exports, UI workflow
  engine_bridge.py            Typed engine boundary and fixture/live trace orchestration
  models.py                   SQLModel tables and case-stage enum
  repository.py               Case/trace persistence and evidence projection transaction
  db.py                       SQLite engine, WAL, idempotent additive upgrades/indexes
  env.py                      Minimal .env loader
  settings.py                 Environment-backed settings
  integrations/               Complaint and dispatch integration contracts/fixtures
  providers/                  OIDC and WebAuthn integration shells
  services/                   Allocation, frontier, custody, evidence, risk, notices, etc.
  templates/                  Server-rendered product screens
  static/                     Local CSS, JS, fonts, graph libraries, and marks

engine/
  adapters/tron.py            Operational TronGrid contract
  adapters/evm.py             Explicit unavailable EVM adapter
  adapters/btc.py             Explicit unavailable Bitcoin adapter
  data/                       Controlled fixture registry JSON

docs/
  demo_case.json              Canonical fixture source
  IMPLEMENTATION_STATUS.md    Enhanced-feature implementation ledger
  DECISIONS.md                Earlier locked architecture decisions

tests/                        Truthfulness, persistence, allocation, UI, and contract tests
kit/                          Historical handoff archive; not the runnable product
var/                          Generated local state; ignored by Git
output/                       Generated PDFs and artifacts
```

Recommended reading order for a new agent:

1. `AGENTS.md`
2. this handoff
3. `docs/IMPLEMENTATION_STATUS.md`
4. `app/engine_bridge.py`
5. `engine/adapters/tron.py`
6. `app/services/allocation.py`
7. `app/services/frontier.py`
8. `app/services/live_tron_resume.py`
9. `app/repository.py`
10. `app/models.py`
11. `app/main.py`
12. relevant tests before changing behavior

## 6. Application Flow

The fixture user journey is:

```text
prototype login
  -> complaint reference ingestion
  -> particulars review
  -> create/reuse Case
  -> create/reuse TraceSnapshot
  -> trace workspace/event replay
  -> investigation canvas
  -> optional custody finding
  -> review checks
  -> notice draft
  -> countersignature
  -> specimen dispatch/export
```

The complete fixture complaint reference used by the intake screen is:

```text
NCRP/2026/MH/0091001
```

The incomplete validation example is:

```text
NCRP/2026/MH/0091002
```

The canonical case inside `docs/demo_case.json` uses acknowledgement
`NCRP/2026/MH/0084213`. The complete sample reference clones the same canonical payment details
with a different acknowledgement number.

Important fixture/live behavior: fixture complaint ingestion explicitly calls the engine with
`trace_mode="fixture"`, even if the global `.env` mode is `live`. This keeps the controlled pitch
flow deterministic and prevents provider/network failures from replacing the fixture result. Manual
API-created traces use `trace_mode="auto"` and therefore follow the configured runtime mode.

## 7. Tracing Engine Public Boundary

The principal types in `app/engine_bridge.py` are:

- `ChainRef`: family, network, and optional chain ID;
- `AssetRef`: symbol, contract, and decimals;
- `NormalizedTransfer`: canonical transfer identity and integer amount;
- `TraceSeed`: address, payment time, amount, chain, asset, transaction ID, and acknowledgement;
- `TraceParams`: traversal budgets and strategy;
- `TraceResult`: typed status, stage, chain, asset, terminal, events, timing, and finding eligibility.

Public engine interfaces:

```python
run_trace_result(seed, params) -> TraceResult
run_trace(seed, params) -> dict
trace(seed, params) -> Iterator[dict]
detect_chain(address) -> "TRON" | "EVM" | "BTC" | "unsupported"
classify(address, chain) -> dict
resolve_vasp(address, chain) -> dict | None
```

`run_trace_result()` is the truthful internal boundary. `run_trace()` is the compatibility
serializer that produces `trinetra.snapshot/2` and a canonical SHA-256.

`TraceParams` defaults:

```text
max_depth          5
time_window_hours  8
value_floor_share  0.02
breadth_cap        3
address_budget     60
strategy           dominant_fund_flow
trace_mode         auto
```

## 8. Modes, Validation, and Truthfulness

`trace_mode` may be `auto`, `fixture`, or `live`:

- `auto`: use `TRINETRA_MODE`;
- `fixture`: accept only the canonical fixture payment details and replay deterministic evidence;
- `live`: validate a real supported seed and use enabled providers.

Fixture and live validation preserve explicit zero values. Code must not use truthiness fallback for
amount, timestamp, transaction ID, or provider event index when zero/empty has evidentiary meaning.

The engine fails closed. Invalid address syntax, wrong chain, wrong asset, seed mismatch, non-positive
amount/time, unsupported chain, disabled gates, and provider errors produce explicit non-custody
terminals. They do not produce a fixture VASP deposit.

Terminal-to-case-stage examples:

| Terminal | Case stage | Finding allowed |
| --- | --- | --- |
| `invalid_seed`, `seed_mismatch`, `provider_error` | `trace_failed` | No |
| `unsupported_chain` | `trace_unsupported` | No |
| `stationary_funds` | `stationary_observed` | No |
| `depth_exhausted`, `trail_dissipated`, `bridge`, `mixer`, boundary | `trace_incomplete` | No |
| `custody_candidate` | `custody_candidate` | No automatic custody finding |
| `vasp_deposit` | `custody_found` | Fixture demo only |
| `verified_custody` | `custody_found` | Only with required verified evidence fields |

A custody finding requires a permitted terminal kind, a deposit address, and an explicitly present
credited amount.

## 9. TRON Adapter

`engine/adapters/tron.py` is the only operational blockchain adapter.

It uses TronGrid V1 endpoints:

```text
GET /v1/transactions/{txid}/events
GET /v1/accounts/{address}/transactions/trc20
```

Seed verification requires one exact TRC-20 Transfer event matching:

- transaction ID;
- USDT contract (or explicitly supplied contract);
- recipient address;
- integer base-unit amount.

History requests use confirmed transfers, ascending block timestamp order, optional minimum/maximum
timestamps, optional contract filtering, bounded page size, bounded page count, and fingerprint
pagination. Repeated cursors, malformed data, authorization failure, rate limiting, HTTP errors, and
socket failures become typed provider errors.

Normalization emits:

```text
txid, ts_ms, source, destination, amount_base, token, decimals,
contract, block, event_index
```

`TRONGRID_API_KEY` is consumed by this adapter. `TRONSCAN_API_KEY` currently appears in readiness
status as a fallback configuration item but no production TRONSCAN adapter consumes it yet.

## 10. Live TRON Traversal

The live path performs the following steps:

```text
validate seed
  -> check feature gates
  -> verify exact seed Transfer event
  -> fetch seed-address TRC-20 history
  -> normalize and chronologically filter outgoing transfers
  -> attribute victim-linked value across outputs
  -> schedule eligible frontier edges
  -> fetch and expand child addresses
  -> stop/defer at configured boundaries
  -> emit source/work/hop/parked/stats/terminal/done events
```

The live synchronous traversal is intentionally bounded. It records live hops as
`live_frontier_candidate`, `band="unscored"`, and
`typology="observed_outgoing_transfer"`. It does not run arbitrary wallets through a production
classifier and does not create live custody findings.

### Traversal strategy

The implementation is not classic DFS and not exhaustive BFS.

- `dominant_fund_flow`: per address, schedule only the largest eligible attributed edge. Pending
  work is ranked by depth first, then descending attributed value. This behaves like a greedy,
  level-oriented beam search with width one.
- `value_weighted`: schedule up to `breadth_cap` eligible edges and rank the global pending set by
  descending attributed value, then depth/event order. This is best-first/max-priority behavior,
  currently implemented by deterministic list sorting rather than a heap object.

All observed outgoing edges remain represented. An edge that is not expanded is parked/deferred
with a reason:

```text
value_floor
depth_budget
time_window
address_budget
breadth_cap
cycle_detected
provider_backoff
```

### Allocation rule

Amounts are always integer base units. For each chronologically ordered outgoing transfer:

```text
numerator = outgoing_base * attributed_base + residual_numerator
outgoing_attributed_base = numerator // balance_base
next_residual = numerator % balance_base
```

The residual is carried deterministically to avoid floating-point drift. The implementation rejects
negative values, overspending, and attributed value above the spendable balance.

Current limitation: the synchronous live helper sets the allocation denominator to
`max(incoming attributed amount, observed outgoing total)`. This is a bounded attribution
approximation, not full historical balance reconstruction across all assets and prior arrivals.

### Persistent resume path

`app/services/live_tron_resume.py` supports:

- leasing queued `FrontierItem` rows;
- expanding one leased TRON address;
- creating canonical child transfer evidence;
- creating separate child attributed lots;
- ancestry-aware cycle deferral;
- stationary remainder preservation;
- child frontier queue/defer state;
- provider source-coverage rows;
- provider-backoff state and timed retry release;
- bounded worker cycles.

This service is tested but is not wired to a continuously running production daemon.

## 11. Classification and Behavioural Heuristics

The current engine is honest about classification maturity.

### Fixture labels

The canonical fixture presents these wallet roles:

- pass-through wallet;
- peel-chain wallet;
- consolidation wallet;
- exchange deposit address;
- verified VASP hot wallet.

The UI explains the intended evidence basis:

- pass-through: one principal inbound/outbound, rapid sweep, low resting balance;
- peel chain: largest share forwarded while smaller branches are parked or retained;
- consolidation: fan-in and a common onward destination;
- deposit pattern: unrelated senders, destination consistency, high forward ratio, near-zero resting
  balance, short sweep timing, and multiple feeder addresses;
- verified VASP: exact address-role evidence in a controlled/reviewed registry, not behaviour alone.

These are currently fixture-backed labels and explanations. `classify()` looks up canonical fixture
addresses. `risk_check()` is also a deterministic fixture/local-record lookup. It is not a general
live wallet classifier.

There is no live burner-wallet detector. Short wallet lifetime and rapid emptying appear as fixture
risk indicators only.

### VASP identity boundary

`resolve_vasp()` currently resolves only controlled fixture addresses. It preserves the difference
between a customer deposit address and a hot wallet. Live traversal remains unscored and cannot infer
that an address belongs to an exchange solely because its transaction behavior resembles a deposit
pattern.

The JSON under `engine/data/` is controlled fixture data, not a production authoritative registry.

## 12. Machine Learning Status

No machine-learning model is active.

- no model file is bundled;
- no model is loaded at runtime;
- no training pipeline exists;
- no predefined training dataset exists in the repository;
- no online learning occurs;
- new cases do not update weights, thresholds, or labels;
- there is no self-improving behavior.

The current engine is deterministic and rule/evidence based. For the same seed, provider data,
parameters, engine mode, registry revision, and source revision, it should produce the same result.

Some fixture JSON contains historical `confidence` and likelihood-ratio display values. They are not
used as a calibrated model output. Public classification/risk results explicitly set:

```json
{
  "score": null,
  "posterior": null,
  "probability_enabled": false,
  "calibration_status": "disabled_pending_independent_labelled_data"
}
```

`app/services/review.py` prohibits probability claims unless independent calibration is explicitly
available. Any future ML slice should be separately versioned, trained and evaluated on independent
labelled data with leakage controls; it must not silently learn from active investigations.

## 13. Trace Result and Final Output

`run_trace()` serializes the typed result into `trinetra.snapshot/2` with:

- case seed and payment details;
- trace parameters;
- engine mode/version/registry revision;
- chain and asset identity;
- seed verification result;
- resolved hops;
- parked and dust branches;
- bridge candidates;
- classifications, only when a qualifying fixture/verified finding exists;
- terminal outcome;
- trace statistics;
- continuation boundary;
- linked-case evidence;
- fee accounting;
- ordered trace events;
- outcome status/stage/finding flag;
- cache identity;
- canonical SHA-256.

Ordered event types include:

```text
source, work, hop, parked, dust, stats, candidate,
bridge_candidate, terminal, done
```

The UI replays persisted events through `/api/traces/{snapshot_id}/stream`. If SSE support is
unavailable, the route returns equivalent JSON. The trace page suppresses custody candidate UI and
disables the finding action when `finding_id` is null.

## 14. Persistence and Evidence Model

`get_or_create_trace()` in `app/repository.py` performs the primary trace write. It derives a cache
identity from seed, parameters, mode, engine/source/registry revisions, and coverage. Duplicate or
concurrent equivalent trace requests reuse one snapshot. Changed identities create a new snapshot
and preserve the older snapshot through supersession links.

The following are written in one primary trace transaction:

- `TraceSnapshot`;
- ordered `TraceEvent` rows;
- `CanonicalTraceEvent` projections;
- `SourceCoverage` rows;
- `AttributedLot` rows;
- queued/deferred `FrontierItem` rows;
- optional `CustodyAssertion`;
- optional `Finding`;
- case-stage transition.

The transaction rolls back on a pre-commit failure. SQLite uniqueness/index guards provide
idempotency for cache identities, canonical evidence, frontier work, protocol links, provider
responses, ledger rows, and custody credits.

Important models in `app/models.py`:

```text
Case
TraceSnapshot
TraceEvent
CanonicalTraceEvent
SourceCoverage
AttributedLot
FrontierItem
OperationBridgeLink
CustodyAssertion
ProviderResponseRecord
ProviderLedgerRecord
ProviderKycRecordRow
ProviderTradeRecordRow
ProviderWithdrawalRecordRow
ProviderSessionRecordRow
CustodyAction
Finding
Notice
Dispatch
VaspResponse
WebAuthnCredential
OidcIdentity
```

`app/db.py` uses `SQLModel.metadata.create_all()` plus additive, idempotent SQLite column/index
helpers. There is no Alembic migration system. Do not perform a production migration from this
prototype workflow.

`app/services/evidence_store.py` can write canonical JSON provider payloads with retrieval metadata
and SHA-256 under a supplied evidence root. The helper is tested but is not yet wired into every live
TronGrid request.

`app/services/accounting.py` verifies per-asset conservation across custody, stationary, deferred,
fees/loss, and unresolved buckets. Duplicate evidence references and unbalanced totals are rejected.

## 15. Custody and Provider Response Contracts

Custody is intentionally separate from behavioral tracing.

`CustodyImport` records:

- provider key;
- chain/network;
- deposit address;
- exact chain credit reference;
- service role;
- optional account reference;
- label validity interval and provenance;
- dispute status;
- current balance;
- recoverable amount.

Validation prevents recoverable value above current balance, expired or inverted labels, and
collapsing the deposit address into the hot-wallet object.

`CertifiedProviderResponse` can additionally carry:

- exact provider response reference and signer;
- ledger entries tied to the exact chain credit;
- KYC/beneficiary record;
- trade records;
- withdrawal records;
- session/device/IP-country records.

Duplicate identical imports are idempotent; conflicting duplicates are rejected.

These contracts are implemented and tested locally. There is no enabled production provider-import
route without schema approval and authority configuration.

## 16. Action and Notice Workflow

The custody-action contract uses explicit states:

```text
draft -> review/reviewed -> submitted -> acknowledged
  -> confirmed/confirmed_hold -> released or seizure_control
  -> restoration/released

submitted/acknowledged may also become rejected or expired
```

Submission or acknowledgement is not a confirmed restraint. Only bounded confirmed states with
required outcome evidence can be represented as confirmed.

The user-facing fixture notice workflow includes:

- finding review checklist;
- notice draft;
- countersignature request;
- separate supervisor countersignature;
- specimen portal/email/nodal-copy dispatch records;
- SAHYOG-shaped export manifest.

Government SSO and SAHYOG remain integration-pending. Dispatch remains specimen/mock unless real
metadata, credentials, approved schemas, legal copy, and channels are configured.

## 17. Protocol and Multi-Chain Boundaries

`app/services/protocols.py` defines evidence contracts for:

- verified Circle CCTP burn/message/attestation/destination mint joins;
- verified swap input/output-recipient joins;
- non-cryptographic correlation candidates;
- unknown-operation boundaries.

Only verified, executed protocol links may consume attribution. Correlation candidates cannot.
Unknown operations stop at an evidence boundary instead of being serialized as ordinary successful
transfers.

These contracts are fixture-tested. No live bridge/DeFi decoder is enabled. EVM and Bitcoin address
families can be detected, but their adapters throw typed unavailable errors. The current product must
not be described as operationally multi-chain.

## 18. Feature Gates and Environment

`.env` is loaded automatically by `app/__init__.py` unless
`TRINETRA_DISABLE_DOTENV=true`. Existing process environment variables win by default.

Tests set `TRINETRA_DISABLE_DOTENV=true` so developer secrets and live settings cannot influence the
suite.

Safe fixture mode:

```dotenv
TRINETRA_MODE=fixture
```

Live TRON seed and bounded-trace gates:

```dotenv
TRINETRA_MODE=live
TRINETRA_ENABLE_LIVE_TRON=true
TRONGRID_API_KEY=replace-with-real-key
TRINETRA_LIVE_TRON_SCHEMA_VERIFIED=true
TRINETRA_LIVE_TRON_SMOKE_VERIFIED=true
TRINETRA_LIVE_TRON_TRACE_VERIFIED=true
```

Optional TronGrid tuning:

```dotenv
TRONGRID_BASE_URL=https://api.trongrid.io
TRINETRA_PROVIDER_TIMEOUT_S=10
TRINETRA_PROVIDER_PAGE_LIMIT=200
TRINETRA_PROVIDER_MAX_PAGES=20
```

Credentials alone do not enable risky capabilities. Feature flags also require schema, smoke,
trace-verification, authority, or approval gates as appropriate.

Additional gated areas:

```text
TRINETRA_ENABLE_PROVIDER_IMPORTS
TRINETRA_PROVIDER_SCHEMA_APPROVED
TRINETRA_PROVIDER_AUTHORITY_CONFIGURED
TRINETRA_ENABLE_PROTOCOL_DECODERS
TRINETRA_PROTOCOL_DECODERS_LIVE_VERIFIED
TRINETRA_ENABLE_PRIVACY_REVIEW
TRINETRA_PRIVACY_REVIEW_APPROVED
```

The `/integrations` page and `/api/integrations/status` expose readiness without exposing key values.

## 19. Authentication and Security Posture

Implemented prototype protections:

- session-backed prototype identity and role;
- same-site session cookies;
- CSRF tokens on authenticated workflow forms;
- authenticated evidence and SAHYOG exports;
- separate supervisor workflow for countersignature;
- secret values excluded from integration-status output;
- append-only hash-chained audit JSONL;
- canonical SHA-256 for snapshots and evidence artifacts.

Production gaps:

- development session secret has a fallback and must be replaced;
- local HTTP has no TLS;
- prototype login is not strong authentication;
- OIDC/WebAuthn implementations are integration shells;
- no external secret vault;
- no multi-tenant authorization boundary;
- no production database or backup strategy;
- no operational monitoring or alerting.

## 20. Important Routes

Pages:

```text
/                         Redirect to login or docket
/login                    Prototype login
/docket                   Active case and evidence workspace
/cases/new                Fixture complaint intake
/traces                   Latest active trace redirect
/traces/{snapshot_id}     Trace event workspace
/cases/{case_id}/canvas   Investigation graph/canvas
/findings/{finding_id}    Custody finding review
/notices                  Notice workspace
/notices/{notice_id}      Notice document/workflow
/risk-check               Fixture/local-record risk aid
/integrations             Capability and configuration readiness
```

APIs/exports:

```text
GET  /api/chains/resolve
POST /api/cases/{case_id}/traces
GET  /api/traces/{snapshot_id}/stream
POST /api/traces/{snapshot_id}/continuations
GET  /api/cases/{case_id}/graph
GET  /api/cases/{case_id}/evidence-manifest
GET  /api/cases/{case_id}/evidence-bundle.zip
GET  /api/search
GET  /api/integrations/status
POST /api/risk-check
GET  /api/notices/{notice_id}/sahyog-export
GET  /api/exhibits/{snapshot_id}.svg
GET  /healthz
```

The continuation endpoint currently records/returns a candidate boundary response; it does not run a
fully integrated cross-chain continuation.

## 21. Local Setup and Operation

Standard setup:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

Health check:

```text
http://127.0.0.1:8000/healthz
```

Use the `PROTOTYPE BASED LOGIN` option. The local process should use one Uvicorn worker because the
prototype uses SQLite and local state.

The Codex bundled verification interpreter used during implementation is:

```powershell
C:\Users\Yash\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
```

## 22. Test Coverage

The suite covers:

- canonical fixture workflow and page/API smoke tests;
- invalid, unsupported, mismatched, zero, negative, and gated live seeds;
- live TRON seed verification and bounded multi-hop behavior with mocked provider responses;
- provider pagination, cursor loops, malformed responses, zero preservation, and socket errors;
- fixture/live cache separation and stale snapshot supersession;
- duplicate and concurrent trace requests;
- rollback of snapshots, events, evidence, and stage on pre-commit failure;
- integer allocation, deterministic residual carry, 100-way splits, exact budgets, and cycles;
- repeated arrivals and two victim deposits reaching the same destination;
- frontier persistence, leasing, release, completion, retry, and resume ordering;
- per-asset conservation and duplicate evidence rejection;
- nullable findings and no custody UI/export leakage on non-custody terminals;
- deposit/hot-wallet separation and reused-deposit/shared-hot-wallet custody cases;
- certified provider response imports and duplicate conflicts;
- action authority, transitions, depleted balances, and confirmed-outcome bounds;
- CCTP/swap/correlation/unknown-operation evidence boundaries;
- probability/participation overclaim prevention;
- CSRF, countersignature, authenticated exports, and local frontend assets.

Run the entire suite before and after meaningful changes. At the time this handoff was prepared, the
latest completed full run in this development session was `107 passed`.

## 23. Known Operational Caveats

- The Codex sandbox may block outbound access to `api.trongrid.io`. That produces an honest
  `provider_error`; it is not evidence that the adapter is structurally broken. Test live access from
  the user's own PowerShell/network when necessary.
- The controlled fixture intake path remains fixture-mode even when `.env` is live by design.
- Existing snapshots are cache-identity keyed. Changing seed, mode, gates, engine version, or coverage
  should produce/reuse the correct identity rather than showing stale results.
- Raw provider payload storage exists as a helper but is not automatically called for every request.
- Persistent frontier worker functions exist, but no service process continuously drains the queue.
- The fixture risk screen and search operate on local demo records, not live intelligence feeds.
- The VASP and bridge registries are controlled fixtures.
- `scikit-learn` is an unused dependency; do not infer ML functionality from its presence.
- The README's architecture/version language predates part of the enhanced feature pass; prefer the
  current code and `docs/IMPLEMENTATION_STATUS.md` for implementation truth.

## 24. Recommended Next Engineering Stages

### Stage A: Production-grade live TRON evidence

- validate current TronGrid schemas against credentialed response samples;
- exercise real pagination, rate limits, retries, and schema drift;
- automatically store raw provider evidence before normalization;
- persist exact provider query ranges, cursors, watermarks, gaps, and conflicts;
- add a supervised worker/service around the existing frontier lease/retry contracts;
- add operational telemetry without exposing keys or sensitive response data.

### Stage B: Real attribution and custody boundary

- source an authoritative, versioned, reviewable VASP registry;
- implement authenticated provider-import adapters against approved schemas;
- verify exact deposit assignment, account reference, ledger credit, current balance, and recoverable
  amount;
- keep registry label, deposit address, hot wallet, account identity, and action state separate;
- enable `verified_custody` only after evidence and authority gates pass.

### Stage C: General behavioral classification

- implement transparent feature extraction for pass-through, peel, consolidation, short lifetime,
  fan-in/fan-out, sweep timing, destination consistency, and resting balance;
- expose raw features and evidence references before assigning bands;
- add a burner class only with a precise, reviewable definition and false-positive tests;
- retain an `unknown` outcome when evidence is insufficient.

### Stage D: ML only after labelled-data readiness

- define labels and provenance;
- split data by address cluster/entity and time to prevent leakage;
- establish non-ML baselines;
- evaluate calibration, false positives, drift, and subgroup behavior;
- version models, datasets, features, and thresholds;
- keep human review and disable online adaptation from individual active cases.

### Stage E: Additional chains and protocols

- implement EVM token-event identity and chain-specific pagination first;
- implement Bitcoin UTXO/outpoint allocation separately from account-based allocation;
- enable one bridge/protocol deployment at a time with cryptographic join evidence;
- never serialize a correlation candidate as a verified cross-chain continuation.

## 25. Non-Negotiable Engineering Rules

- Preserve existing user changes in the dirty worktree.
- Use `apply_patch` for manual edits.
- Keep browser assets local under `app/static/`.
- Keep amounts as integer base units internally.
- Keep timestamps as UTC epoch milliseconds internally and display them in IST.
- Keep generated state under `var/`.
- Do not assert guilt in UI, logs, notices, or tests.
- Keep deposit addresses and hot wallets as different legal/evidence objects.
- Do not claim SSO, SAHYOG, dispatch, freeze, or custody integration is live without real approved
  metadata, schemas, credentials, evidence, and authority.
- Keep probabilities disabled until independently calibrated on labelled data.
- Unsupported or failed traces must never fall back to demo custody.
- A provider key alone must never enable a risky capability.

## 26. Suggested Claude Kickoff

Before changing code:

1. Read `AGENTS.md`, this handoff, and `docs/IMPLEMENTATION_STATUS.md`.
2. Run `git status --short`; preserve every existing modification and untracked file.
3. Read the tests nearest the requested behavior.
4. Run the full pytest suite for a baseline.
5. Inspect `.env.example`, but do not print or expose `.env`.
6. Treat the current code as implementation truth and historical handoffs as context.
7. Keep fixture and live behavior explicitly separated.
8. Implement, test, and update `docs/IMPLEMENTATION_STATUS.md` when a capability genuinely changes.

When reporting status, distinguish among:

```text
unavailable
fixture-tested
integration-tested
live-verified
enabled
```

Do not promote a capability merely because its data model or interface contract exists.
