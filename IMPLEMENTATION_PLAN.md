# TRINETRA v3 - Regression-safe implementation plan

Approved implementation reference for the v3 handoff. `AGENTS.md`, current code, and the
truthfulness rules remain controlling where any archived document conflicts.

## Locked decisions

- Preserve canonical fixture behaviour, attribution arithmetic, provider normalisation, and all
  existing working flows. The pre-change baseline is 160 passing tests.
- Every handoff reference to ASP means ACP. Keep `supervisor` as the stored ACP role and do not
  repurpose the internal `admin` role.
- Strategy switching recomputes presentation and derived analysis over the same sealed hops. An
  explicit IO retrace creates a child snapshot and becomes active only after successful completion.
- Statutory choices are non-authoritative specimen labels pending legal review.
- Preserve Portal, Email, and Nodal-copy; add SAHYOG only as a clearly simulated/specimen route.
- SLA defaults to 24 hours with per-VASP overrides. Escalation ownership is case-specific; the
  original supervising ACP remains a watcher.
- Do not implement destructive in-app demo reset. Demo seeding is deterministic and additive.

## Stages

1. **Foundations:** validated working context and navigation, stable officer profiles and case
   assignments, role/panel registry, backward-compatible v2 audit events with an SQLite outbox,
   and additive notice/dispatch workflow tables.
2. **Structural UI:** real role-scoped counters and docket queries, ACP compact dispatch status,
   custody-header alignment, and responsive layouts without body-level overflow.
3. **Trace and canvas:** real-event-only status reducer, engine-enumerated strategy projections,
   animated Omega SVG layout/weight/path changes, comparison overlay, explanation and key-finding
   updates, fullscreen, export, and immutable retrace snapshots.
4. **Freeze notice:** six-stage autosaved authoring, typed immutable attachments, explicitly
   simulated complaint retrieval, generated/versioned drafts, manual attestation, reusable-login
   re-verification modal, ACP review, truthful dispatch routes, and immutable verified PDFs.
5. **ACP dispatch oversight:** one shared dispatch store for compact/detail views, filters, saved
   views, SLA clocks, aggregates, audited bulk actions, case-specific escalation ownership and
   watcher updates, and audit-backed timelines.
6. **Proof and handoff:** loading/empty/error states, separate nine-case workflow seed data,
   print styles, complete responsive and regression runs, capability reporting, and
   `IMPLEMENTATION_REPORT.md`.

## Non-negotiable gates

- Run the full suite after every meaningful stage; do not weaken passing tests.
- Assert byte-identical canonical fixture output and integer/zero/timestamp invariants.
- Exercise touched screens at 360, 768, 1024, 1280, 1440 and 1920 pixels with no horizontal page
  overflow or console errors.
- Keep simulated integrations unmistakably labelled and never equate dispatch with restraint.
- Never delete evidence, trace snapshots, audit events, exports, attachments, or superseded
  workflow versions.
