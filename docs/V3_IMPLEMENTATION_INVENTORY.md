# TRINETRA v3 implementation inventory

This inventory is the Stage 0 companion to `IMPLEMENTATION_PLAN.md`. It records the surfaces that
must remain regression-safe while v3 is added.

## Routes and role exposure

- Public: login and integration-pending government sign-in links.
- IO: complaint intake, gated live intake, traces, mutable canvas controls, pre-notice review,
  six-stage notice authoring, dispatch recording, risk check, audit and own sessions.
- ACP (`supervisor`): assigned-case docket, shared evidence/canvas/finding views, countersignature,
  dispatch oversight, escalation ownership and audit timelines.
- Admin: existing internal access is preserved and is not represented as ACP authority.
- Shared scoped routes: trace status/events/explanations, strategy projection, navigation state,
  workspace event stream, evidence exports, notice version history and dispatch exports.

## Counters and presentation data

- Navigation badges come from scoped persisted case, trace and notice queries; New case has no
  badge.
- The dynamic docket derives its five KPIs and every filter count from the same visible-case set.
- Restrained value includes only explicit `VaspResponse.amount_restrained_base` values.
- The protected canonical demonstration keeps its historical presentation rows in automated
  compatibility sessions; the normal fixture workspace uses the separate v1 workflow seed.

## Data-bearing panels and states

- Docket: KPI strip, filter result set, working trail and ACP queue.
- Trace: persisted event reducer, provider/backoff state, hop log and terminal boundary.
- Canvas: graph, selection details, strategy explanation, ghost comparison and parked branches.
- Finding/notice: evidence review, parameters, attachments, preview, verification, versions and
  ACP review.
- Dispatch: aggregates, combined filters, shared records, SLA, escalation and exports.
- Audit/risk/integrations/sessions: existing empty/error behavior is preserved; v3 additions use
  explicit empty and failure states rather than fixture fallback.
