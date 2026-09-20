# TRINETRA v3 implementation report

Date: 2026-09-20

## Outcome

The v3 handoff is implemented as an additive workflow and presentation layer. Canonical fixture
data, attribution arithmetic, provider normalization, sealed trace evidence, and existing passing
tests were preserved. No migration framework, frontend framework, CDN dependency, probability
surface, or destructive demo-reset control was introduced.

The pre-change baseline was 160 passing tests. The final suite is 169 passing tests, including nine
new v3 regression tests. The canonical repository fixture snapshot remains
`5ffa67c393b4b27ce0d1dea47e1430a25ef92722c9ba9eb429d5418f92be9c99`.

## Stage results

### Stage 0 — baseline and safeguards

- Persisted the approved plan in `IMPLEMENTATION_PLAN.md`.
- Recorded protected fixture/registry and canonical snapshot hashes in
  `docs/V3_BASELINE_MANIFEST.json`.
- Recorded route/role, counter, and state inventories in
  `docs/V3_IMPLEMENTATION_INVENTORY.md`.
- Preserved the intentionally dirty working tree and did not modify protected canonical fixtures,
  engine registry files, or `TRACING_ENGINE_HANDOFF.md`.

### Stage 1 — scoped foundations

- Added stable officer profiles, case assignments, watchers, and case-specific escalation history.
- Added a validated, server-authoritative working context with sessionStorage refresh mirroring and
  ordinary-link fallbacks.
- Added assignment checks to case, snapshot, finding, notice, dispatch, canvas, strategy, and event
  routes. Switching cases clears incompatible notice/dispatch identifiers.
- Added a central role/panel registry, pre-Alpine incompatible-markup removal, server-side omission
  of IO mutation panels in ACP views, and a responsive navigation drawer.
- Extended the legacy hash-chained JSONL audit format with nullable v2 identity/entity fields and a
  transactional SQLite outbox with idempotent stable-event flushing.
- Added the v3 notice, attachment/version, verification, dispatch, SLA, officer, assignment,
  watcher, and escalation tables through the repository's additive create/idempotent-upgrade path.
- Added one scoped workspace SSE stream with JSON fallback for context, counters, dispatch, and
  escalation state.

### Stage 2 — real dashboard state

- Replaced action/badge and docket KPI placeholders with role-scoped queries.
- Docket filter counts and rows are derived from the same result set.
- Restrained-value totals use only explicit recorded VASP response amounts.
- The ACP docket widget and detailed tracker use the same `DispatchRecord` store.
- Added the 1440/full-rail, 1024–1439/drawer, and sub-1024 stacked/card behavior to touched
  screens. Addresses and hashes remain copyable while long values wrap or truncate safely.

### Stage 3 — event-driven trace and strategy projection

- Replaced timed fixture replay with a reducer driven by persisted/SSE trace events; JSON polling is
  the fallback.
- Preserved explicit provider errors and non-custody terminals.
- Added pure, integer-only strategy analysis for engine-enumerated `dominant_fund_flow` and
  `value_weighted` views.
- Added the strategy API and synchronized graph ranks, edge widths, emphasis, projected terminal,
  key findings, explanations, deterministic delta, and switch-back state.
- Added a previous-strategy ghost overlay with bounded clearing behavior.
- Extended the active Omega SVG renderer with animated relayout, fullscreen/fallback, whole/visible
  SVG export, and 2× PNG export. Sealed transfers are never rewritten.
- Added IO-only immutable retrace. Failed/unsupported child snapshots remain retained and inactive;
  successful children promote without overwriting the parent.

### Stage 4 — six-stage notice workflow

- Added provenance-bearing parameters and visibly non-authoritative statutory specimen options.
- Added complaint, FIR, graph, custody-summary, other, and methodology-annex slots with extension,
  MIME signature, size, filename, count, SHA-256, and supersession validation.
- Extended the complaint adapter for unmistakably simulated, watermarked source documents.
- Added explicit immutable version generation, dirty-state invalidation, preview-end attestation,
  persistent verification lockout, fixture-only demo verification, ACP return/regeneration,
  countersignature immutability, and field/attachment/annex/statutory diffs.
- Added immutable A4 Playwright PDFs with specimen watermark, continued structure, page numbering,
  annex handling, signature placement, extractability checks, and pypdf validation.
- ACP workflow markup is read-only; IO parameter, attachment, attestation, verification, and routing
  controls are absent rather than merely CSS-hidden.

### Stage 5 — dispatch oversight and escalation

- Added shared dispatch records while retaining legacy per-channel attempt rows.
- Added the 24-hour default SLA, per-VASP override model, frozen clocks, green/amber/red state, and
  exactly-once audited breach registration.
- Added combined filters, saved filter state, aggregates, compact/detail views, CSV/PDF exports, and
  bulk escalate/reassign/nudge operations with exact counts and per-case partial results.
- Added case-specific escalation owner chains and retained the original ACP as a watcher.
- Portal, Email, Nodal-copy, and SAHYOG remain attempts/specimens; no UI asserts external delivery
  or confirmed restraint without explicit evidence.

### Stage 6 — proof, fixture states, and handoff

- Added a separate idempotent `fixtures/workflow_states_v1.json` containing nine workflow states,
  three IOs, two ACPs, SLA green/amber/breached examples, multiple escalation levels, one explicit
  simulated restrained-amount response, a real non-custody failure reason, and a gated live-ready
  case.
- The seed is additive on a file-backed fixture database and never deletes prior snapshots,
  evidence, audit rows, attachments, or artifacts.
- Added explicit empty/warning/error treatment to the new v3 docket, strategy, notice, dispatch, and
  audit-backed workflow surfaces while preserving existing state handling on untouched legacy
  panels.
- Added A4 notice print behavior and landscape dispatch-register PDF export.
- Updated `docs/IMPLEMENTATION_STATUS.md` and runtime capability reporting using only the allowed
  capability levels.

## Verification

- Full pytest: **169 passed**.
- JavaScript syntax: `app.js`, `omega-graph.js`, `trace.js`, `notice-workflow.js`, and `notice.js`
  pass Node syntax checks.
- Python: the `app` package passes compileall.
- Responsive browser verification: **72 checks, 0 failures** across IO and ACP views at 360, 768,
  1024, 1280, 1440, and 1920 pixels. Covered docket, trace, canvas, notice, six-stage workflow, and
  dispatch tracker; checked HTTP status, body overflow, horizontally clipped active controls,
  browser console errors, and absence of IO-only panels from ACP markup.
- Notice PDF: generated through Playwright, parsed with pypdf, text-extractable, hash-addressed, and
  visually inspected from a rendered Poppler page.
- Protected SHA-256 values remain unchanged for `docs/demo_case.json`,
  `engine/data/bridge_registry.json`, and `engine/data/vasp_registry.json`.

## Capability posture

| Capability | Level |
| --- | --- |
| Scoped working context and role registry | integration-tested |
| Event-driven trace and immutable retrace | integration-tested |
| Strategy projection and Omega exports | integration-tested |
| Notice workflow and immutable PDF | integration-tested |
| Dispatch SLA/escalation oversight | integration-tested |
| Canonical audit v2 outbox | integration-tested |
| Nine-state workflow seed | fixture-tested |
| SAHYOG specimen route | fixture-tested |
| Portal, Email, Nodal-copy external delivery | unavailable |
| CCTNS/SAHYOG real re-verification | unavailable |

## Deliberate deviations and retained compatibility

- The repository already contained a fixture-only automatic notice-workflow cleanup on logout that
  is required by an existing passing regression test. It was left untouched under the root
  rulebook's no-unrequested-change rule. No new reset button, route, or shortcut was added, and the
  new v3 workflow seed itself is additive and non-destructive.
- Existing automated `TestClient` sessions and in-memory databases retain the protected historical
  docket presentation. Normal file-backed fixture sessions use the dynamic nine-state seed.
- Real government identity, government portal delivery, email delivery, nodal-copy delivery, VASP
  acknowledgement, and restraint integrations were not promoted. They require approved schemas,
  credentials, authority configuration, and independently verified evidence.
- The universal loading/empty/error-component rewrite was scoped to new/touched v3 surfaces; legacy
  panels not otherwise changed retain their pre-existing state presentation to avoid a broad UI
  refactor.
- Browser verification covers the touched fixture workflow. Credentialed live retracing and real
  external integrations were not rerun and are not claimed as v3 live verification.

## Manual demonstration script

1. Start a single Uvicorn worker in fixture mode against a file-backed SQLite database.
2. Sign in as **Insp. R. Kulkarni**. Open the docket and confirm only assigned/open records affect
   badges, filters, and KPIs.
3. Open the canonical trace. Confirm progress is already derived from recorded events; use Explain
   trace and open the canvas.
4. Switch between Dominant fund flow and Value weighted. Confirm ranks, widths, emphasis,
   explanation, key finding projection, delta, and ghost overlay change while the sealed hop data
   remains unchanged. Switch back and confirm exact view restoration.
5. Use Re-run trace and confirm the parent snapshot remains available. A failed/unsupported attempt
   must remain retained but inactive.
6. Open seeded case `NCRP/2026/MH/V3-1004`, then its notice. Enter the six-stage workflow, attach the
   simulated complaint PDF, save parameters, generate v1, scroll the actual preview to the end,
   attest, use the fixture IO verification shortcut, and route. Download the immutable PDF.
7. Sign in as **ACP S. Deshmukh**. Confirm the IO working trail and notice-authoring forms are absent.
   Review the compact docket queue and detailed dispatch tracker, then open a notice to countersign
   or return it with remarks.
8. Filter dispatches by SLA breach/escalation, inspect the canonical audit-backed owner timeline,
   and export the filtered CSV/PDF register. Treat every route as simulated unless external evidence
   explicitly says otherwise.
