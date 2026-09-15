# TRINETRA v2 — BUILD BRIEF FOR CODEX
### Repo-aware revision. Supersedes any earlier version of this brief.

---

## 0. PRECEDENCE AND GROUND RULES — read before anything else

**Rule precedence, highest first:**

1. `AGENTS.md` in the repository — the controlling rulebook.
2. The current code, which is implementation truth.
3. `CLAUDE_PROJECT_HANDOFF.md` and `docs/IMPLEMENTATION_STATUS.md`.
4. This brief.
5. `kit/` and older handoffs — historical context only, never instructions.

If this brief conflicts with `AGENTS.md` or with a truthfulness rule already enforced in code, **the repository wins and you stop and report the conflict** rather than implementing what this brief says.

**The stack is fixed. Do not choose a new one.** Python 3.11–3.12, FastAPI, Uvicorn, Jinja2 server-rendered templates, SQLModel on SQLite in WAL mode, `requests` for provider HTTP, `sse-starlette` with JSON fallback, vanilla JS + vendored Alpine.js + vendored Cytoscape/Dagre, local fonts, **no CDN, no frontend build step**, all browser assets under `app/static/`. pytest + HTTPX + pytest-playwright for tests. Do not introduce a JS framework, a build pipeline, a graph database, an ORM migration system, or a new HTTP client.

**Before writing any code:**

1. Read `AGENTS.md`, `CLAUDE_PROJECT_HANDOFF.md`, `docs/IMPLEMENTATION_STATUS.md`.
2. `git status --short`. The working tree is dirty on `main` by design. **Preserve every existing modification and untracked file.** Do not reset, checkout, clean, stash or discard. Leave `TRACING_ENGINE_HANDOFF.md` alone.
3. Read the tests nearest the behaviour you are about to change.
4. Run the full pytest suite for a baseline. Record the number. The last known-good run was `107 passed`.
5. Inspect `.env.example`. **Never print, echo, log or expose values from `.env`.**
6. Use `apply_patch` for manual edits.
7. Update `docs/IMPLEMENTATION_STATUS.md` only when a capability genuinely changes.

**Status vocabulary.** When you report on anything you build, use exactly one of: `unavailable`, `fixture-tested`, `integration-tested`, `live-verified`, `enabled`. **Never promote a capability because its data model, interface or contract exists.** A typed contract with fixture tests is `fixture-tested`, not `enabled`.

---

## 1. THE TRUTHFULNESS DOCTRINE — this constrains every feature below

TRINETRA is an **investigative aid**, not an accusation engine. It must not assert guilt anywhere — UI, logs, notices, tests, variable names, commit messages.

These distinctions are load-bearing and must survive every change:

| Must never be collapsed | |
|---|---|
| value exposure | ≠ participation |
| a service label | ≠ account identity |
| a customer deposit address | ≠ an exchange hot wallet |
| dispatch or request state | ≠ a confirmed restraint |
| behavioural similarity | ≠ verified custody attribution |
| a correlation candidate | ≠ a verified cross-chain continuation |

**The engine fails closed.** Invalid syntax, wrong chain, wrong asset, seed mismatch, non-positive amount or time, unsupported chain, disabled gate, provider error — each produces an explicit non-custody terminal. **A failed or unsupported trace must never fall back to a demo custody result.**

**Probabilities are disabled.** Public classification and risk results set `score: null`, `posterior: null`, `probability_enabled: false`, `calibration_status: "disabled_pending_independent_labelled_data"`. `app/services/review.py` enforces this. Do not introduce probabilities, posteriors, confidence percentages or risk scores anywhere in this work. Use **evidence bands and raw features with evidence references**. `scikit-learn` sits unused in `requirements.txt` — do not infer ML functionality from it and do not start using it outside the gated path in §8.

**Other invariants:** integer base units internally; UTC epoch milliseconds internally, IST on display; zero values are evidentiary and must never be lost to truthiness fallback; generated state under `var/`, artifacts under `output/`; a provider key alone never enables a risky capability — feature flags plus schema/smoke/trace/authority gates do.

---

## 2. THE CENTRAL SCOPING TRUTH — understand this before Feature 1

The requested outcome is: *"paste any real wallet address or transaction hash from a public ledger and the system does everything it does on the demo data."*

**The trace can do this. The custody finding cannot — and must not be made to.**

The fixture demonstration reaches `vasp_deposit` and a custody finding only because `engine/data/` contains controlled, reviewed address-role evidence for those exact addresses. `resolve_vasp()` resolves controlled fixture addresses only. Live traversal is deliberately unscored: `live_frontier_candidate`, `band="unscored"`, `typology="observed_outgoing_transfer"`. The system is forbidden from inferring that an address belongs to an exchange merely because its behaviour resembles a deposit pattern.

So an arbitrary live address will honestly terminate at `custody_candidate`, `trace_incomplete`, or a boundary — **not** at a custody finding. That is correct behaviour, not a bug, and this brief does not ask you to change it.

**The approach this brief takes (a genuine capability increase without breaking the doctrine):**

- Make the **live traversal itself** production-grade so it truthfully matches the fixture path in everything that is behaviourally honest: seed verification, attribution, scheduling, coverage, events, persistence, evidence, exhibits, graph, export.
- Introduce a **reviewed, versioned VASP registry** with explicit provenance per entry (§9, Workstream R). Where a live trace reaches an address that the registry covers, it may reach a custody finding **at the registry's stated evidence quality**. Where it does not, it stops at an honest boundary that names what is missing.
- Never let registry coverage be implied where it is absent. An uncovered terminal says so.

The registry — its sourcing, review and provenance — is the difference between "traces real money and stops at a boundary" and "traces real money to a named exchange". Treat it as a first-class deliverable, not a data chore.

---

## 3. FEATURE 1 — LIVE DATA OPERATION

### Current state
Live TRON is `integration-tested` behind gates: credentialed TronGrid seed verification and bounded multi-hop traversal exist, with typed provider errors, fingerprint pagination, backoff and a persistent resume path in `app/services/live_tron_resume.py`. No daemon drains the frontier queue. Raw provider payload storage exists in `app/services/evidence_store.py` but is not called for every request. Fixture intake deliberately forces `trace_mode="fixture"`.

### Target

**3.1 Seed types.** Accept both an address and a transaction hash as a case seed on the live path. A txid seed verifies the exact TRC-20 Transfer event (txid + contract + recipient + integer base amount) and anchors case chronology on its `ts_ms`. Preserve existing fail-closed behaviour for every mismatch class.

**3.2 Live intake route.** The fixture intake path stays fixture-forced — do not change it. Add a **separate, clearly labelled live intake** that accepts an arbitrary address or txid, resolves the chain via `detect_chain()`, and runs with `trace_mode="live"` under existing gates. The two paths must be visually and structurally distinct in the UI, and a trace's mode must be visible on every screen that renders it and in every export.

**3.3 Mode toggle in the UI.** Surface a mode control that sets `trace_mode` per trace rather than relying on operators editing `.env`. A persistent, unmistakable banner states the active mode. `fixture` must be visually unmissable so a controlled demonstration can never be mistaken for live data — this is a truthfulness requirement, not a UI nicety. The control respects existing gates: if `TRINETRA_ENABLE_LIVE_TRON` or the schema/smoke/trace verification flags are false, live is shown as unavailable with the specific missing gate named, and is not selectable.

**3.4 Provider hardening (Stage A).** This is the core of the feature:
- validate current TronGrid schemas against credentialed response samples; handle schema drift explicitly;
- exercise real pagination, rate limits and retries against live endpoints;
- **automatically store raw provider evidence before normalization** via `evidence_store.py` — wire it into every provider request, with retrieval metadata and SHA-256;
- persist exact provider query ranges, cursors, watermarks, gaps and conflicts in `SourceCoverage`;
- add a **supervised worker** around the existing `FrontierItem` lease/retry/backoff contracts so the queue actually drains — this is also the prerequisite for Feature 5;
- operational telemetry that exposes no keys and no sensitive response data.

**3.5 Rate limiting — pause, countdown, resume.** Provider backoff state and timed retry release already exist. Surface them: when a trace defers on `provider_backoff`, the UI shows what happened, which provider, and a live countdown to the scheduled retry; already-resolved hops stay on the canvas; the worker resumes from the lease without losing frontier state. Never truncate silently, never present a deferred trace as complete. Deferred reasons already carry a vocabulary — `value_floor`, `depth_budget`, `time_window`, `address_budget`, `breadth_cap`, `cycle_detected`, `provider_backoff` — render each in plain language.

**3.6 Re-tracing — show latest, retain all.** The request was "overwrite, keep only latest". Implement it as **UI-level**, not storage-level: the case shows only the current snapshot, while superseded snapshots are retained through the existing supersession links and cache-identity mechanism. **Never delete a snapshot, event, canonical evidence row or exported artifact** — an evidence system that discards prior states is not an evidence system. The docket records that a re-trace occurred, by whom, when, and which exports predate it. Exported artifacts are immutable snapshots of their moment and are never regenerated in place.

**3.7 Freshness.** Every node carries the retrieval timestamp of the provider data it was built from. The canvas carries a persistent "chain data as of <IST timestamp>" strip. Data older than a configurable threshold is flagged on the node and in the strip with a refresh action. Freshness metadata and provider watermarks appear in the evidence manifest and in every export — a notice must state when the underlying chain data was read.

**3.8 Investigator-controlled traversal bounds.** `TraceParams` already carries exactly the right knobs. Expose them as a pre-trace control panel with plain-language explanations, current defaults shown, and the ability to leave each unbounded where the engine permits:

| Parameter | Default | Plain-language meaning |
|---|---|---|
| `max_depth` | 5 | how many hops from the reported payment |
| `time_window_hours` | 8 | how long after the payment to keep following |
| `value_floor_share` | 0.02 | stop following a branch below this share of attributed value |
| `breadth_cap` | 3 | how many onward branches per address (`value_weighted` only) |
| `address_budget` | 60 | total addresses this trace may expand |
| `strategy` | `dominant_fund_flow` | largest single flow, or `value_weighted` best-first |

The active bounds are part of the cache identity, are recorded with the trace, and must be printed in the evidence annex — they define the completeness of the search and therefore what the result can and cannot claim.

**3.9 Unconfirmed transactions.** The adapter currently requests confirmed transfers only. Add unconfirmed transfers as an **explicitly separate observation channel**: fetched only when requested, rendered visually distinct, never entering attribution, never entering canonical evidence, never contributing to a terminal, never appearing in an export except as a clearly labelled observation with a warning. An unconfirmed transfer is a lead, not evidence.

### Acceptance
A real TRON address or txid produces a live bounded trace whose amounts, timestamps and hop sequence verify against a public explorer; raw provider payloads are stored with hashes for every request; killing the network mid-trace produces a visible deferral with a countdown and a clean resume; the mode banner makes fixture mode unmistakable. Report the result as `live-verified` only after running it against real credentialed endpoints from the user's own network — the Codex sandbox may block `api.trongrid.io`, which produces an honest `provider_error` and proves nothing about the adapter.

---

## 4. FEATURE 2 — CHAINS BEYOND TRON

### Current state
`engine/adapters/evm.py` and `engine/adapters/btc.py` exist as explicit unavailable adapters that raise typed errors. `detect_chain()` recognises the address families. Protocol evidence contracts for CCTP, swaps, correlation candidates and unknown boundaries exist in `app/services/protocols.py`, fixture-tested, with no live decoder. **The product must not currently be described as operationally multi-chain.**

### Target — strictly in this order, one at a time

**4.1 EVM token tracing first.** Implement token-event identity and chain-specific pagination for one EVM network before adding a second. EVM shares TRON's account model, so `app/services/allocation.py` applies unchanged — the work is adapter-side: event decoding, log identity, reorg handling, provider pagination semantics. Same four-function contract as `tron.py`, same typed provider errors, same raw-evidence storage, same fail-closed validation. Ship one network as `integration-tested` before starting the next.

**4.2 Address ambiguity.** A `0x…` address is valid on several EVM networks and is different money on each. On ingest, probe the supported networks, present which show activity with a one-line summary each, and **require the investigator to confirm the network**. Never auto-select. Record the confirmed `ChainRef` on the case. `/api/chains/resolve` is the natural home for the probe.

**4.3 Bitcoin second, and separately.** UTXO/outpoint allocation is a different model and **must not reuse the account-based allocation path**. Build it as its own allocation implementation sharing the case, evidence and persistence layers. Common-spend (joint-input) clustering is the deterministic baseline. Change-address inference is probabilistic: implement it only with a precise reviewable definition, label every such inference explicitly in data and UI, keep it out of attribution arithmetic, and give it false-positive tests. A wrong change-address inference attributing funds to an uninvolved party is the worst failure this system can produce.

**4.4 Bridges and protocols.** Enable one deployment at a time, behind `TRINETRA_ENABLE_PROTOCOL_DECODERS` plus `TRINETRA_PROTOCOL_DECODERS_LIVE_VERIFIED`. Only verified, executed protocol links with cryptographic join evidence may consume attribution. **A correlation candidate must never be serialized as a verified cross-chain continuation.** Unknown operations stop at an evidence boundary. The existing `/api/traces/{snapshot_id}/continuations` behaviour — recording a candidate boundary rather than running a continuation — is correct and stays correct until real join evidence is available.

**4.5 Honest reporting.** Until an adapter is live-verified against real credentialed endpoints, it stays `unavailable` in `/integrations` and in every status report. Do not soften the unavailable adapters' error messages into something that reads like partial support.

---

## 5. FEATURE 3A — EXPLAINING HOW THE TRACE WORKS

### Current state
Ordered events (`source`, `work`, `hop`, `parked`, `dust`, `stats`, `candidate`, `bridge_candidate`, `terminal`, `done`) are persisted and replayed through `/api/traces/{snapshot_id}/stream`. Deferral reasons are recorded. The UI explains fixture wallet roles and their intended evidence basis. Nothing exposes the attribution arithmetic or the scheduling decision to the operator.

### Target
Every number on screen becomes openable, down to the transaction. Clicking a node or edge opens an explanation panel answering:

| Question | What is shown |
|---|---|
| Why was this path followed? | The active strategy, the pending-work ranking at that step, the attributed value that won, and every sibling edge that was parked with its exact deferral reason |
| Where did this attributed amount come from? | The full integer arithmetic: incoming attributed base, outgoing base, the denominator actually used, `numerator // balance_base`, the carried residual — shown as a readable calculation in base units and in display units |
| What is this address, and what is it not? | The evidence basis for any label, or an explicit "unscored — observed outgoing transfer only" for live frontier addresses |
| Why did the trace end here? | The terminal kind, the case stage it maps to, whether a finding is permitted, and precisely what evidence would have been required for a stronger outcome |
| What is this based on? | Transaction IDs, block, event index, provider retrieval time, and the stored raw-evidence hash |

Additional requirements:

- **Two registers, toggled.** An investigator register in plain language, and a technical register with the arithmetic, parameters and evidence references. Same facts, two vocabularies. Default to the investigator register.
- **A case-level methodology panel** explaining the traversal strategies, the attribution rule and its residual carry, what the bounds did to this result, the terminal taxonomy, and — most importantly — **an explicit statement of what the system does not and cannot know**: that live addresses are unscored, that behavioural similarity is not custody attribution, that the registry is controlled data, that probabilities are disabled pending independent calibration.
- **A methodology annex in every export**, because the recipient of a notice will not have the application.
- **The known attribution approximation must be stated, not hidden**: the synchronous live helper uses `max(incoming attributed amount, observed outgoing total)` as the allocation denominator, which is a bounded approximation rather than full historical balance reconstruction. Say so in the technical register and in the annex. Disclosing a known limitation is what makes the rest credible.

---

## 6. FEATURE 7 — RISK CHECK AND BEHAVIOURAL HEURISTICS
*(Presented before Feature 3B because the ML slice depends on it.)*

### Current state
`/risk-check` and `POST /api/risk-check` are a deterministic fixture/local-record lookup presenting **evidence bands, not probabilities**. There is no general live wallet classifier and no burner detector. `classify()` looks up canonical fixture addresses.

### Target — Stage C: transparent feature extraction

**6.1 Compute features, expose them before any band.** Implement transparent, testable feature extraction over an address's observed transfer history. For each feature, store and display the raw computed value **with its evidence references**, before and independently of any band assignment:

| Feature | What is computed |
|---|---|
| Pass-through pattern | principal inbound/outbound pairing, sweep latency, resting balance |
| Peel signature | largest share forwarded while smaller branches park or remain |
| Consolidation | fan-in breadth and onward-destination commonality |
| Fan-in / fan-out ratio | distinct counterparties in versus out |
| Sweep timing | distribution of hold intervals |
| Destination consistency | proportion of outflow to a single destination |
| Resting balance | retained versus forwarded ratio |
| Forward ratio | value forwarded / value received |
| Address lifetime | first-seen to last-seen against activity volume |
| Counterparty exposure | direct transfers with registry-listed or reported addresses |

**6.2 Bands, never scores.** Map features to evidence bands with an explicit, reviewable, documented rule set. `score`, `posterior` and `probability_enabled` stay as they are — null, null, false — with `calibration_status` surfaced in the response and on screen. Do not introduce a numeric risk score.

**6.3 `unknown` is a first-class outcome.** When observed history is insufficient to support any band, return `unknown` with a statement of what was insufficient. An address with four transactions cannot support a behavioural characterisation and the system must say so rather than characterising it anyway.

**6.4 Burner class — only with a precise definition.** Add it only with a reviewable definition, explicit thresholds, and false-positive tests against legitimate short-lived addresses. If that standard cannot be met, do not ship the class.

**6.5 Never assert innocence.** An address with nothing found is reported as **no adverse findings in available sources**, never as clean, safe or legitimate. Absence of evidence in available public data is not evidence of legitimacy, and the wording must not imply otherwise. Carry the caveat into every export.

**6.6 Attribution lookups.** Where reviewed source data exists — sanctions listings, reported-abuse records, the registry from Workstream R, and the system's own prior case records — surface each as a separate evidence line with its source, retrieval time and provenance. The system's own case history is the most defensible of these and the most differentiating; surface it prominently, subject to case visibility rules.

**6.7 Lookup record.** Every risk check is recorded in the audit chain: who checked what, when, in which mode, and what was returned. Querying a citizen's wallet is an action that belongs on the record. A check is a throwaway lookup by default with an explicit escalate-to-case action.

### Acceptance
A live address returns raw features with evidence references and either a documented band or an honest `unknown`; a sparse address returns `unknown` rather than a band; no probability, score or posterior appears anywhere in the response; the response's `calibration_status` is visible in the UI.

---

## 7. FEATURE 4 AND FEATURE 6 — IDENTITY AND SESSION LIFECYCLE

### Current state
Session-backed prototype identity with officer and supervisor roles, same-site cookies, CSRF on authenticated workflow forms, authenticated exports, separate supervisor countersignature, append-only hash-chained audit JSONL. OIDC and WebAuthn exist as **integration shells** in `app/providers/`. The development session secret has a fallback.

### Target

**7.1 Login screen.** Improve the existing prototype login screen in the product's visual language. It stays labelled as a prototype login. **Do not describe or imply that government SSO is live.** The OIDC and WebAuthn shells remain shells and remain reported as `unavailable` in `/integrations` until real approved metadata, credentials and an authority configuration exist. A one-click demonstration login is acceptable and must be visibly marked as a demonstration account, with demonstration-generated artifacts marked accordingly.

**7.2 Roles.** Officer and supervisor exist and the countersignature separation is already correct. Add an administrator role only if it earns its place: provisioning, gate/readiness visibility, audit review. Accounts are provisioned, never self-registered. Replace the development session-secret fallback with a required configured secret and fail closed without it.

**7.3 Identity on evidence.** Every export already runs authenticated. Ensure name, service identifier, unit, role and generation time appear in the artifact, alongside the existing canonical SHA-256 and a reference into the audit chain so any artifact can be tied back to the action that produced it.

**7.4 Sign-out.** A visible officer badge in the top bar — identity, role, unit — with sign-out. On sign-out: clear the session server-side, invalidate the CSRF token, and write a sign-out event into the audit chain carrying session duration, cases touched, traces run and artifacts exported. An in-flight trace continues on the worker and its result is waiting on return.

**7.5 Idle handling.** Obscure the screen after a short idle period requiring re-entry to reveal; terminate the session after a configurable idle period with a warning and an extend action. An unattended workstation displaying an open case is a real risk in the deployment this product describes.

**7.6 Sessions.** List active sessions with device and last-active time; support signing out of all devices; require it on credential change.

**7.7 Honest security reporting.** The production gaps listed in the handoff — no TLS locally, prototype login is not strong authentication, no secret vault, no multi-tenant authorization boundary, no production database or backup strategy, no monitoring — remain true after this work and must remain visible in `/integrations` and in any status report. Do not let a better-looking login screen imply a better security posture.

---

## 8. FEATURE 5 — WALLET WATCH AND ALERTS

### Current state
No monitoring exists. `app/services/live_tron_resume.py` provides leasing, expansion, deferral, backoff and bounded worker cycles — the mechanics — but no process drains the queue. **This feature is blocked on the supervised worker in §3.4 and must not be started before it.**

### Target

**8.1 Alerts are observations, never assertions.** A watch hit reports an observed transfer. It must never assert custody, participation or guilt, and must never create a finding. Alert copy follows the vocabulary in §1 exactly.

**8.2 Watch triggers and priority.**

| Trigger | Priority | Meaning |
|---|---|---|
| Observed transfer into a registry-listed deposit address | highest | a reviewable custody path may now exist — for officer review, not automatic finding |
| Watched address matches a sanctions or reported-abuse listing | highest | escalation for review |
| Observed transfer into a protocol/bridge boundary | high | the trail reaches an evidence boundary |
| Outbound transfer above a configured value | high | material movement |
| Dormant address becomes active | medium | change in behaviour |
| Any outbound transfer | low | situational awareness |

**8.3 Watchlist population.** Automatically watch addresses in open cases above a configured attributed-value threshold, plus manually pinned addresses. Cap the watchlist against the provider request budget and, at the cap, show what would be dropped and let the officer prioritise — never drop silently.

**8.4 Polling tiers — this shares a provider budget with live tracing.**

| Tier | Contents | Interval |
|---|---|---|
| Hot | high attributed value in active cases, or moved recently | short |
| Standard | other addresses in open cases | medium |
| Cold | closed or dormant cases | long |

Addresses promote on movement and demote on quiet. **Interactive traces always take priority over watch polling under budget contention** — an officer waiting on a trace must never be starved by background monitoring. Surface current budget consumption in `/integrations`.

**8.5 Delivery — in-app only.** A notification centre inside the application. **No email, no messaging platforms.** Dispatch integrations are specimen/mock and gated; adding an alert email channel would be exactly the kind of claim the repository rules forbid. If an email channel is ever built, it goes behind its own gate, defaults off, and carries no case or wallet data — only a case reference and a link.

**8.6 Lifecycle.** Unread → read → acknowledged (officer and time, audited) → dismissed with a reason (audited) or resolved. Support snooze, reassignment within the unit, and a direct jump to the exact node in the exact case. Every state change enters the audit chain.

**8.7 Graph updates.** When a watch hit extends a case's known graph, extend the canvas and mark the new elements as new since last view until acknowledged. **Already-exported artifacts are never mutated**; the docket records that the case evolved after that export.

---

## 9. FEATURE 3B — THE ML QUESTION, AND WORKSTREAM R

### 9.1 ML status and what may be built

No model is bundled, loaded or trained; no dataset exists in the repository; no online learning occurs; new cases do not update weights, thresholds or labels. This is a deliberate position, and **it is defensible in front of judges precisely because it is honest**.

PS 26183 names AI/ML. The correct response is not to bolt on a model — it is to build the thing that makes a model possible and to say exactly where the boundary is. Stage D work, and only in this shape:

- define labels and their provenance explicitly;
- split data by address cluster or entity **and** by time to prevent leakage;
- establish non-ML baselines first — the Stage C rule set in §6 is that baseline, and any model must beat it measurably to justify itself;
- evaluate calibration, false positives, drift and subgroup behaviour;
- version models, datasets, features and thresholds independently of the application;
- keep it **offline and disabled**: no model output reaches a user-facing surface until independent calibration exists, `app/services/review.py` remains the gate, and `calibration_status` remains `disabled_pending_independent_labelled_data` until it is honestly otherwise;
- **never learn from active investigations.** No online adaptation, no feedback from live cases into weights.

The ML slice is delivered as a separately versioned pipeline plus documentation of the labelling and evaluation design. If it cannot be evaluated on independent labelled data, it ships disabled and is reported as `unavailable`. That is the honest answer, and it is a stronger answer than a demo model with no denominator.

### 9.2 Workstream R — the reviewed VASP registry (runs in parallel; it decides what live traces can conclude)

Per §2, this is what determines whether a live trace can ever reach a custody finding. It is data and process work, not only code:

- an authoritative, **versioned, reviewable** registry with per-entry provenance, source, retrieval time, validity interval and dispute status;
- strict preservation of the distinction between registry label, deposit address, hot wallet, account identity and action state — these are five different objects and `CustodyImport` validation already refuses to collapse the first two;
- controlled self-owned observations (small real deposits to accounts at known exchanges, recording the resulting deposit addresses) are the cleanest source of ground truth available to this project and should be collected systematically, with provenance recorded per entry;
- `verified_custody` remains gated behind evidence and authority checks and is enabled only when those pass;
- `engine/data/` stays clearly marked as controlled fixture data until a reviewed registry genuinely replaces it. Do not quietly upgrade fixture data into something described as authoritative.

---

## 10. BUILD ORDER

| Stage | Work | Depends on |
|---|---|---|
| 0 | Baseline: read rules, `git status`, full pytest run, record the number | — |
| 1 | §7 identity and session lifecycle — login screen, sign-out, idle, sessions, audit events, session-secret hardening | independent; safe first win |
| 2 | §3.4 provider hardening and the supervised worker (Stage A) | 0 |
| 3 | §3 remaining live-data work — live intake, mode control, bounds panel, freshness, deferral UI, unconfirmed channel | 2 |
| 4 | §6 Stage C feature extraction and the risk-check rebuild | 3 |
| 5 | §5 explainability surfaces | 4 (explains what exists by then) |
| 6 | §8 watch and alerts | 2, 3 |
| 7 | §4.1–4.2 one EVM network, then ambiguity resolution | 2 |
| 8 | §4.3 Bitcoin UTXO allocation | 7 |
| 9 | §9.1 ML slice, offline and disabled | 4 |
| R | §9.2 registry workstream | parallel throughout |

Each stage leaves the application fully working and demonstrable. No stage leaves a broken intermediate state. **Stability over completeness** — this is built for a live demonstration where a smaller set of features that never fails beats a complete set that breaks on stage.

---

## 11. TESTING

Run the full suite before and after every meaningful change; the baseline was `107 passed`. Add tests alongside each stage:

- adapter `normalise` against known real transactions, asserting token decimals and millisecond timestamp conversion — this is the repository's most dangerous silent-failure point;
- zero-value and empty-value preservation wherever amount, timestamp, transaction ID or event index carries evidentiary meaning;
- integer allocation: residual carry determinism, no overspend, no attribution above spendable balance, conservation per asset;
- fail-closed behaviour for every invalid, unsupported, mismatched and gated seed class, asserting that none produces a custody result;
- fixture/live cache separation and snapshot supersession;
- frontier lease, release, retry, backoff and resume ordering under the new worker;
- role enforcement, CSRF, countersignature, authenticated exports, sign-out and idle termination;
- risk check: `unknown` on insufficient history, no probability fields populated, no innocence assertion in any response path;
- overclaim prevention: assert that no new surface asserts participation, guilt, custody without evidence, or a confirmed restraint from a dispatch state.

---

## 12. REPORTING

When reporting on this work, for each capability give exactly one of `unavailable`, `fixture-tested`, `integration-tested`, `live-verified`, `enabled`, and state what would be required to reach the next level. Do not promote a capability because its contract, model or interface exists. If something could not be verified — for example because the sandbox blocked `api.trongrid.io` — say so explicitly and say what verification is still outstanding on the user's own network.
