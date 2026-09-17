# TRINETRA Features Handoff

Current build date: 2026-09-16

This handoff lists the features included in the current TRINETRA build. It is written for a new engineer, reviewer, demo operator, or stakeholder who needs to understand what the product can do, what is fixture-only, what is gated, and what must not be overclaimed.

TRINETRA is an investigative aid for virtual-asset tracing. It helps an officer intake a complaint, trace attributable crypto value, preserve evidence, review possible custody outcomes, prepare specimen freeze notices, and monitor dispatch status. It does not assert guilt, identity, or confirmed restraint from tracing alone.

## 1. Product Modes

| Feature | Status | What it does | Key boundary |
| --- | --- | --- | --- |
| Controlled fixture mode | enabled | Replays the canonical demonstration case from controlled local data. This gives a stable end-to-end pitch flow. | Fixture data is demonstration data, not an authoritative production registry. |
| Live provider mode | live-verified for bounded TRON trace, gated | Accepts real TRON/USDT address or transaction-hash seeds and attempts a bounded provider trace through TronGrid gates. | Live trace does not create custody attribution unless separate certified evidence supports it. |
| Per-trace mode visibility | enabled | Every trace surface shows whether it is fixture or live. | Fixture and live results must never be blended. |
| Fail-closed terminal outcomes | integration-tested | Invalid seeds, wrong chain, provider errors, unsupported chains, disabled gates, and seed mismatches close as non-custody outcomes. | A failed live trace must never fall back to a demo custody result. |

## 2. Authentication, Roles, and Sessions

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Prototype officer login | integration-tested | Lets a demo officer enter the workspace using clearly marked prototype login. | `app/main.py`, `app/templates/login.html` |
| Supervisor login | integration-tested | Allows a supervising officer to countersign notices separately from the investigating officer. | `app/templates/supervisor_login.html` |
| Role separation | integration-tested | Prevents an officer from countersigning their own high-value notice. | `app/main.py`, notice routes |
| Server-side session records | integration-tested | Tracks active sessions, idle state, activity, revocation, and sign-out events. | `app/services/sessions.py`, `app/models.py` |
| Active sessions page | integration-tested | Shows active devices/sessions and supports sign-out-all. | `/sessions`, `app/templates/sessions.html` |
| CSRF protection | integration-tested | Protects workflow-changing forms such as logout, findings, notice actions, dispatch, and tracker updates. | `app/main.py` |
| Demo reset on logout | enabled | Resets demo notice countersignature and dispatch state when logging out, so the next demo session must request countersignature again. | `app/repository.py`, `app/main.py` |
| OIDC/WebAuthn shells | unavailable | Integration placeholders exist for future government identity integrations. | `app/providers/oidc.py`, `app/providers/webauthn.py` |

## 3. Case Intake and Docket

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Fixture complaint intake | enabled | Accepts the demo complaint reference and creates/reuses the controlled case. | `/cases/new`, `/cases/ingest` |
| Live trace intake | live-verified for gated TRON/USDT | Lets the investigator start a bounded live TRON/USDT trace using a transaction hash or address. | `/cases/live/new`, `/cases/live/start` |
| Case particulars review | enabled | Displays complaint details, payment information, validity checks, and case reference. | `app/templates/case_intake.html`, `app/templates/live_intake.html` |
| Case docket | enabled | Shows the active case, evidence workspace, trace, canvas, finding, notice, and exports. | `/docket`, `app/templates/docket.html` |
| Active-case session tracking | integration-tested | Keeps the current case available across pages during the session. | `app/main.py` |

## 4. Tracing Engine

| Feature | Status | What it does | Key boundary |
| --- | --- | --- | --- |
| Deterministic fixture trace | enabled | Replays a fixed TRON/USDT trace with known wallet roles and a custody finding path. | Fixture-only result. |
| Live TRON seed verification | live-verified | Resolves a TRON transaction hash or address seed and validates the exact USDT transfer event where applicable. | Requires gates and provider access. |
| Bounded live TRON traversal | live-verified | Follows confirmed TRC-20 transfers within sealed depth, time, value-floor, breadth, and address budgets. | Unscored and non-custodial without separate evidence. |
| Confirmed-only attribution | integration-tested | Confirmed events participate in trace attribution and canonical evidence. | Unconfirmed observations are separate leads, not attribution evidence. |
| Integer-only value allocation | integration-tested | Attributes victim-linked value using integer base units and deterministic residual carry. | No floating point attribution. |
| Dominant fund flow strategy | integration-tested | Follows the largest eligible onward branch from each expanded address. | Greedy, bounded, not exhaustive. |
| Value-weighted strategy | integration-tested | Follows up to the breadth cap and ranks pending branches by attributed value. | Best-first bounded exploration, not proof of ownership. |
| Deferral reasons | integration-tested | Records why branches were parked: value floor, depth, time window, address budget, breadth cap, cycle, or provider backoff. | Deferred does not mean cleared. |
| Trace cache identity | integration-tested | Reuses equivalent traces and creates new snapshots when seed, mode, bounds, gate state, or source identity changes. | Superseded snapshots are retained. |
| Active trace replay | enabled | Shows trace progress, events, terminal state, and action buttons. Numeric percent progress has been removed. | Completion controls unlock only when the trace result allows them. |
| SSE plus JSON fallback | integration-tested | Streams ordered trace events where possible and falls back to JSON status when SSE is unavailable. | `sse-starlette`, `/api/traces/{id}/stream` |

## 5. Live Provider and Evidence Handling

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| TronGrid adapter | live-verified for bounded trace | Calls TronGrid V1 for transaction events, TRC-20 transfer history, and wallet balances. | `engine/adapters/tron.py` |
| Typed provider errors | integration-tested | Converts HTTP, schema, rate-limit, socket, auth, and provider failures into explicit trace outcomes. | `engine/adapters/tron.py` |
| Provider pagination handling | integration-tested | Handles bounded page size, page count, fingerprint pagination, and cursor-loop detection. | `engine/adapters/tron.py` |
| Source coverage records | integration-tested | Persists provider query bounds, retrieval timestamps, watermarks, and coverage metadata. | `app/repository.py`, `app/models.py` |
| Raw provider evidence store | integration-tested | Stores provider payloads with retrieval metadata and SHA-256 where wired. | `app/services/evidence_store.py` |
| Provider budget/backoff helpers | integration-tested | Tracks request budgets, retry timing, and provider backoff state. | `app/services/provider_budget.py` |
| Wallet balance display on graph | integration-tested | Fetches and displays live balance metadata for graph nodes when available. | `app/services/graph_view.py`, `omega-graph.js` |

## 6. Frontier Resume and Worker Mechanics

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Frontier rows | integration-tested | Persists queued and deferred branches after a bounded trace. | `app/services/frontier.py`, `app/models.py` |
| Lease/complete/release workflow | integration-tested | Allows resumable frontier expansion without losing branch state. | `app/services/frontier.py` |
| Live TRON resume expansion | integration-tested | Expands one leased TRON frontier item, creates child evidence, and schedules/defer branches. | `app/services/live_tron_resume.py` |
| Provider retry release | integration-tested | Releases provider-backoff rows when retry time is due. | `app/services/live_tron_resume.py` |
| Supervised worker shell | integration-tested, gated | Can run bounded frontier cycles under a feature gate. | `app/services/worker.py` |
| Continuous production worker | unavailable | No production daemon is claimed as live. | Requires deployment and operational verification. |

## 7. Investigator Canvas and Graph

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Shared Omega graph renderer | enabled | Uses the adapted "Akshat's Graph" SVG behavior for demo and live canvases. | `app/static/omega-graph.js`, `app/templates/_omega_graph.html` |
| Demo canvas | enabled | Shows the controlled fixture trace with deposit address and hot wallet kept separate. | `app/templates/canvas.html` |
| Live canvas | enabled | Shows confirmed live trace data without implying custody attribution. | `app/templates/live_canvas.html` |
| Pan, zoom, fit, reset | enabled | Lets investigators navigate the graph visually. | `app/static/omega-graph.js` |
| Node and edge selection | enabled | Opens side details for addresses, hops, balances, amounts, and explanation targets. | `app/static/omega-graph.js` |
| Copyable full addresses | enabled | Short labels are display-only; full addresses remain copyable. | `omega-graph.js` |
| Strategy dropdown | enabled | Lets the canvas display selected trace strategy without mutating sealed trace parameters. | `app/templates/canvas.html` |
| Custody finding continuation | enabled where finding exists | Canvas/live trace surfaces show a route to custody finding when the snapshot actually has a finding. | `canvas.html`, `live_canvas.html`, `live_trace.html` |

## 8. Explainability

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Explainability drawer | integration-tested | Opens explanations for methodology, seed, hops, parked branches, and terminal outcomes. | `_trace_explainability.html`, `explainability.js` |
| Investigator register | integration-tested | Explains trace decisions in plain police/investigator language. | `app/services/explainability.py` |
| Technical register | integration-tested | Shows arithmetic, parameters, evidence refs, provider refs, scheduling, and terminal details. | `app/services/explainability.py` |
| Terminal explanation tabs | integration-tested | Investigator and technical terminal tabs show distinct content with defensive fallback for missing registers. | `explainability.js` |
| Methodology annex | integration-tested | Exports the tracing methodology and limitations with evidence bundles and notices. | `app/services/explainability.py`, `app/services/notices.py` |
| No probability explanation | enabled | Explanations avoid confidence percentages and calibrated model claims. | Truthfulness enforced by tests and review helpers. |

## 9. Custody Findings

| Feature | Status | What it does | Key boundary |
| --- | --- | --- | --- |
| Fixture custody finding | enabled | Creates a custody finding for the controlled demo terminal. | Fixture-only unless verified custody evidence exists. |
| Nullable finding path | integration-tested | Non-custody traces render honestly without fake findings. | `Finding` can be absent. |
| Finding review checklist | integration-tested | Requires review checks before notice creation. | `app/templates/finding.html` |
| Custody assertion records | integration-tested | Stores custody evidence separately from trace behavior. | `app/services/custody.py`, `app/models.py` |
| Certified provider response contracts | integration-tested | Data contracts exist for ledger, KYC, trades, withdrawals, sessions, balances, and recoverable amounts. | Storage contract only unless approved integrations exist. |
| Verified custody terminal | integration-tested | Only a permitted terminal with deposit address and credited amount can create a finding. | Live behavioral similarity is not enough. |

## 10. Freeze Notice Workflow

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Notice drafting | enabled | Generates a specimen freeze notice from a custody finding. | `/findings/{id}/notice`, `app/repository.py` |
| Fixed 24-hour deadline wording | enabled | Notice states: "Response required: ASAP, and in any case within 24 hours of receipt of this notice." | `notice.html`, `notice_document.html` |
| Countersignature request | enabled | Officer requests supervisor countersignature before high-value dispatch. | `/notices/{id}/countersign-request` |
| Separate supervisor countersignature | integration-tested | Supervisor signs in their own session and records badge/time. | `/notices/{id}/countersign` |
| Dispatch recording | integration-tested | Records selected specimen portal/email/nodal copy channels and keeps dispatch pending/amber. | `/notices/{id}/dispatch` |
| Print and signed PDF control | enabled | Notice surface supports print/download controls for demonstration artifacts. | `notice.js`, `notice.css` |
| Dispatch is not restraint | enabled | UI says dispatch is awaiting acknowledgement, not confirmed freeze/restraint. | Truthfulness boundary. |

## 11. Dispatch Tracker

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Tracker page | enabled | Lists notices, case refs, VASP labels, deposit addresses, dispatch time, 24-hour deadline, time state, status, and last update. | `/dispatch-tracker`, `dispatch_tracker.html` |
| Effective status calculation | integration-tested | Computes Drafted, Dispatched, Acknowledged, Responded, Overdue, and Escalated. | `app/services/dispatch_tracker.py` |
| Overdue view state | integration-tested | Overdue is computed from first dispatch plus 24 hours unless manually escalated/responded. | Does not silently mutate records. |
| Manual status update | integration-tested | Officer can update status with CSRF, note, timestamp, and event history. | `/dispatch-tracker/{notice_id}/status` |
| Manual escalation | integration-tested | Records escalation with note/history. | `/dispatch-tracker/{notice_id}/escalate` |
| Tracker event history | integration-tested | Keeps status changes in `NoticeTrackerEvent`. | `app/models.py` |

## 12. Evidence, Exports, and Audit

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Evidence manifest | integration-tested | Exports case, snapshot, finding, notice, trace bounds, source coverage, and hashes. | `/api/cases/{id}/evidence-manifest` |
| Evidence bundle ZIP | integration-tested | Packages trace/finding/notice/evidence data into a ZIP artifact. | `/api/cases/{id}/evidence-bundle.zip` |
| Deterministic SVG exhibit | integration-tested | Produces an SVG graph exhibit for a snapshot. | `/api/exhibits/{snapshot_id}.svg`, `app/services/exhibit.py` |
| SAHYOG-shaped specimen export | integration-tested | Exports a specimen manifest without claiming production SAHYOG integration. | `/api/notices/{id}/sahyog-export` |
| Hash-chained audit log | integration-tested | Records important actions with a chained hash trail. | `app/services/audit.py` |
| Artifact identity | integration-tested | Includes generation identity, actor, time, and canonical hashes in exports. | `app/main.py` |
| Immutable snapshot posture | enabled | Supersedes old snapshots rather than deleting them. | Evidence rule. |

## 13. Risk Check and Behavioural Heuristics

| Feature | Status | What it does | Key boundary |
| --- | --- | --- | --- |
| Risk check page | live-verified for feature extraction path described in status docs | Lets an authenticated user check an address and review evidence bands and raw feature signals. | Not a calibrated risk score. |
| Raw behavioural features | integration-tested | Computes/exposes pass-through, peel, consolidation, fan-in/out, sweep timing, destination consistency, resting balance, forward ratio, lifetime, and exposure indicators where data supports them. | Sparse history can return `unknown`. |
| Evidence bands | integration-tested | Uses deterministic rules and evidence references instead of probabilities. | `score` and `posterior` stay null. |
| Calibration disabled banner | enabled | Keeps `probability_enabled=false` and `calibration_status=disabled_pending_independent_labelled_data` visible. | No ML claim. |
| Case escalation from risk check | integration-tested | Allows a risk lookup to be prepared for case intake instead of automatically becoming a case. | Officer review remains required. |
| No innocence assertion | enabled | Reports "no adverse findings in available sources" rather than clean/safe/legitimate. | Absence of evidence is not legitimacy. |
| Burner classification | unavailable | Burner detection is not enabled as a production classifier. | Requires reviewed definition and false-positive benchmark. |

## 14. Integrations and Capability Readiness

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Integration status page | enabled | Shows readiness for identity, live TRON, provider imports, protocol decoders, privacy review, and dispatch posture. | `/integrations`, `app/services/integrations.py` |
| Capability matrix | enabled | Reports each capability using explicit readiness vocabulary. | `app/services/capabilities.py` |
| Feature gates | enabled | Keeps risky features behind environment flags and independent verification gates. | `app/services/feature_flags.py` |
| Secret-safe reporting | enabled | Shows whether keys/config are present without printing secret values. | `/api/integrations/status` |
| Real SAHYOG/government dispatch | unavailable | Specimen export exists, but no production channel is claimed. | Requires approved metadata, credentials, legal authority, schemas, and routing. |
| Production SSO | unavailable | OIDC/WebAuthn shells exist only as pending integration surfaces. | Requires approved provider setup and deployment hardening. |

## 15. Search and Utility APIs

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Chain resolver API | integration-tested | Resolves a seed family as TRON, EVM, BTC, or unsupported. | `/api/chains/resolve` |
| Create trace API | integration-tested | Creates a trace snapshot for a case through API workflow. | `/api/cases/{case_id}/traces` |
| Continuation boundary API | fixture-tested | Records/returns a cross-chain continuation candidate boundary. | It does not run verified cross-chain tracing. |
| Case graph API | integration-tested | Returns graph data for a case. | `/api/cases/{case_id}/graph` |
| Search API | fixture-tested | Searches local records. | `/api/search`, `app/services/search.py` |
| Health check | enabled | Returns application health. | `/healthz` |

## 16. Protocol and Multi-Chain Boundaries

| Feature | Status | What it does | Key boundary |
| --- | --- | --- | --- |
| TRON/USDT tracing | live-verified for bounded trace | Operational chain slice for current live provider work. | Custody remains separately gated. |
| EVM detection | unavailable beyond detection | Recognizes EVM-style address family. | No live EVM token tracing adapter is enabled. |
| Bitcoin detection | unavailable beyond detection | Recognizes BTC address family. | No UTXO tracing engine is enabled. |
| CCTP evidence contract | fixture-tested | Models verified CCTP join evidence locally. | No live decoder enabled. |
| Swap evidence contract | fixture-tested | Models swap input/output-recipient evidence locally. | No live decoder enabled. |
| Correlation candidate | fixture-tested | Records possible continuation as a candidate boundary. | Candidate is not verified cross-chain continuation. |
| Unknown operation boundary | fixture-tested | Stops attribution at unsupported/unknown operations. | Unknown is an honest terminal. |

## 17. Data Storage and Local Runtime

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| SQLite WAL storage | enabled | Stores local prototype state in SQLite WAL mode. | `app/db.py` |
| Additive SQLite upgrades | integration-tested | Adds columns/tables/indexes idempotently without Alembic. | `app/db.py` |
| Core tables | enabled | Stores cases, snapshots, events, evidence, lots, frontier, findings, notices, dispatches, sessions, and tracker rows. | `app/models.py` |
| Local artifacts | enabled | Writes final artifacts under `output/` and temporary work under `tmp/`. | Repo convention |
| Local one-worker runtime | enabled | App is intended to run as a single Uvicorn worker locally. | SQLite/local-state constraint |

## 18. Frontend and UX Features

| Feature | Status | What it does | Key files |
| --- | --- | --- | --- |
| Server-rendered workspace shell | enabled | Provides navigation for cases, investigations, outputs, tools, and session identity. | `base.html`, `_workspace_nav.html` |
| Local vanilla JS/CSS | enabled | No frontend framework, no build step, no CDN. | `app/static/` |
| Trace mode banners | enabled | Makes controlled fixture and live provider mode visible. | `_trace_mode_banner.html` |
| Responsive trace/canvas/notice pages | enabled | Pages are styled for the demo workflow and local browser use. | `app/static/*.css` |
| Local fonts and marks | enabled | Uses vendored Noto fonts and local TRINETRA marks. | `app/static/fonts`, SVG marks |
| Print styles | enabled | Supports notice and document printing. | `print.css`, notice templates |

## 19. Testing and Verification Features

| Feature | Status | What it does |
| --- | --- | --- |
| Full pytest suite | enabled | Covers app smoke, trace truthfulness, allocation, live provider behavior, findings/notices, dispatch tracker, frontend integrity, protocol boundaries, and session lifecycle. |
| Provider contract tests | integration-tested | Check pagination, schema drift, zero preservation, and provider failure handling. |
| Truthfulness regression tests | integration-tested | Guard against fake custody findings, probabilities, guilt wording, dispatch-as-restraint claims, and fixture fallback on live failure. |
| UI route smoke tests | integration-tested | Verify important pages and assets render. |
| Notice workflow tests | integration-tested | Cover countersignature, dispatch, deadline wording, and demo reset behavior. |

Latest known full local run after the current feature changes: `154 passed`.

## 20. Features Deliberately Not Included as Production Capabilities

These are either unavailable, fixture-only, or integration-pending. Do not demo them as live production capability.

| Capability | Current status | What is required next |
| --- | --- | --- |
| Production custody from arbitrary live TRON trace | unavailable | Reviewed VASP/deposit registry, certified provider evidence, legal authority, approved schemas. |
| Real freeze/restraint confirmation | unavailable | Official dispatch/acknowledgement/hold evidence from an approved channel. |
| Production SAHYOG submission | unavailable | Approved SAHYOG metadata, credentials, schemas, and routing. |
| Production government SSO | unavailable | Approved OIDC/WebAuthn provider, secure origin, tenant/authority config. |
| EVM tracing | unavailable | Live adapter, event identity, provider evidence, chain confirmation workflow. |
| Bitcoin tracing | unavailable | Separate UTXO/outpoint allocation engine and tests. |
| Bridge/DeFi live decoding | unavailable | One verified protocol decoder at a time with cryptographic join evidence. |
| Calibrated ML risk scoring | unavailable | Independent labelled dataset, leakage-controlled training, calibration, review gate. |
| Burner-wallet classification | unavailable | Reviewable definition, thresholds, false-positive benchmark, and tests. |
| Production continuous monitoring | unavailable | Deployed worker, provider budget operations, alert UI, audit lifecycle, network verification. |

## 21. End-to-End Demo Flow

1. Officer logs in through the prototype login.
2. Officer ingests the controlled complaint reference.
3. TRINETRA creates/reuses the case and trace snapshot.
4. Active trace replays the ordered trace without visible numeric percentage progress.
5. Officer opens the investigator canvas and reviews Akshat's Graph.
6. Officer opens explainability for methodology, hops, parked branches, and terminal outcome.
7. If a valid fixture custody finding exists, officer opens custody finding.
8. Officer completes review checks and creates a notice draft.
9. Officer requests countersignature.
10. Supervisor logs in separately and countersigns.
11. Officer dispatches specimen channels.
12. Dispatch tracker shows pending/overdue/responded/escalated state and manual history.
13. Evidence manifest, evidence bundle, SVG exhibit, print/PDF controls, and SAHYOG specimen export are available.
14. Officer logs out; demo countersignature/dispatch state resets for the next demo session.

## 22. Key Implementation Map

| Area | Main files |
| --- | --- |
| App routes and workflow | `app/main.py` |
| Data models | `app/models.py` |
| SQLite setup/upgrades | `app/db.py` |
| Trace orchestration | `app/engine_bridge.py` |
| Trace persistence/evidence projection | `app/repository.py` |
| TRON provider adapter | `engine/adapters/tron.py` |
| Allocation | `app/services/allocation.py` |
| Frontier/resume/worker | `app/services/frontier.py`, `app/services/live_tron_resume.py`, `app/services/worker.py` |
| Explainability | `app/services/explainability.py`, `app/static/explainability.js` |
| Graph/canvas | `app/services/graph_view.py`, `app/static/omega-graph.js`, `app/templates/_omega_graph.html` |
| Custody/provider contracts | `app/services/custody.py` |
| Notices | `app/services/notices.py`, `app/templates/notice.html`, `app/templates/notice_document.html` |
| Dispatch tracker | `app/services/dispatch_tracker.py`, `app/templates/dispatch_tracker.html` |
| Risk check | `app/services/risk.py`, `app/services/behavior.py` |
| Sessions | `app/services/sessions.py`, `app/templates/sessions.html` |
| Audit | `app/services/audit.py` |
| Integration status | `app/services/integrations.py`, `app/services/capabilities.py`, `app/services/feature_flags.py` |

## 23. Golden Rules for Anyone Continuing the Build

1. Keep fixture and live behavior visibly separate.
2. Never turn provider failure into a demo custody result.
3. Never assert guilt, participation, identity, freeze, seizure, or confirmed restraint without the required evidence.
4. Keep amounts in integer base units internally.
5. Treat `0` and empty provider values as facts, not missing data.
6. Keep probabilities disabled unless independent calibration exists.
7. Preserve old snapshots and exported artifacts; supersede instead of deleting.
8. Do not expose `.env` secret values.
9. Do not add a frontend framework, CDN, build step, graph database, Alembic, or new HTTP client.
10. Run the full pytest suite before and after meaningful changes.
