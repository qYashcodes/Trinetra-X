# 07 · UI specification

Visual target: `design/screenshots/*.png` (primary), `design/mockups/0X-*.dc.html` (open via
`python -m http.server` in that folder), `design/reference/login-final.png` (overrides the login mockup).
Mockups are drawn at 1620×972; build fluid for 1366×768 → 1920×1080.

## 1. Principles

- **The graph is the hero.** On the canvas it is never demoted to a card in a grid; everything else is rails, drawers, and strips around it.
- **Government-system realism.** State Emblem, Maharashtra Police letterhead, NCRP feed pill, CCTNS/Parichay buttons: presented as the deployed system would look. The disclaimer line stays on every screen: *"Prototype built for Smart India Hackathon 2026 by Team Trinetra. Not an official Government of India website."*
- **Data typography.** Addresses, hashes, amounts, timestamps in Noto Sans Mono; middle-truncated (`TGh3c9…SsQ7h`) with click-to-copy of the full value; left-aligned, never centred. Amounts: two decimals + asset (`17,880.00 USDT`). Times: 24 h + `IST`.
- **Copy.** Sentence case, active verbs, one name per action across the flow ("Dispatch notice" → "Notice dispatched"). Errors say what happened and what to do; they don't apologise. Empty states invite the next action. Never "fraudster"/"scammer" (CLAUDE.md rule 9).
- **Motion** only in response to the officer (panel open, hop appended, selection). The live trace's appended rows are the one orchestrated moment. Respect `prefers-reduced-motion`.
- **Quality floor.** Visible keyboard focus (`--focus`), logical tab order, WCAG 2.1 AA contrast, all sizes in `rem` so A- / A / A+ scales the whole UI, `print.css` for notices.

## 2. Tokens

```css
--bg:#f5f7fa; --bg-canvas:#eef1f6; --surface:#ffffff; --border:#d9dee5; --border-soft:#e3e8ef;
--ink:#111a2b; --ink-2:#16233d; --muted:#5c6880; --muted-2:#8a94a6;
--navy:#0b2e6f; --navy-2:#12325e; --link:#17427f; --focus:#2563eb;
--high:#16a34a; --med:#c98a12; --alert:#dc2626; --slate:#5c6880;
--saffron:#ff9933; --india-green:#138808;   /* tricolour hairline, top of nav rail */
font-family:'Noto Sans',sans-serif;  --mono:'Noto Sans Mono',monospace;
```
Band chips: high = `--high`, medium = `--med`, low = `--slate`, alert = `--alert`. Trinetra mark recoloured to `--navy`.

## 3. Shared chrome (every authenticated screen)

- **Nav rail** (from `NavRailLight.dc.html`, minus dark-console link). Groups: *Cases* — New case `/cases/new`, Case docket `/docket` · *Investigations* — Active traces `/traces`, Investigator canvas (current case) · *Outputs* — Custody findings `/findings`, Freeze notices `/notices` · *Tools* — Risk check `/risk-check`. Counts are computed (open cases, running traces, notices awaiting acknowledgement).
- **Header:** exact-match search (address / 64-hex txid / NCRP ack; shows "No exact match" otherwise), NCRP feed pill from `ComplaintSource.health()`, IST clock (honours `DEMO_CLOCK`), officer block "Insp. R. Kulkarni · PIS 74821 · Cyber Cell, Pune City" → View my activity log, Sign out. A- / A / A+.
- **Footer:** case / chain / last updated, health dot from `/healthz` ("All systems operational" only if true), data-mode indicator (docs/10 D-05), disclaimer.

## 4. Cut from the mockups (don't build)

Security key / WebAuthn · TOTP and redirect/credentials/attach-case steps · dark console link ·
हिंदी toggle · Network/Case canvas views · header "Intelligence"/"Reports" · canvas "Key insights" and
"Alerts" tabs · "Expand onward", "Mark as custody point" · Share / Export dropdown (the PDFs are the
export) · ⌘K fuzzy search (exact match instead) · any hardcoded dispatch failure.

---

## 00 · Login — `/login`

Target: `design/reference/login-final.png`.
- **Sign in with CCTNS** and **Sign in with Parichay (NIC)** are plain links to `CCTNS_LOGIN_URL` / `PARICHAY_LOGIN_URL`, opened per `SSO_LINK_TARGET` (default new tab so the pitch never loses the TRINETRA tab). No TRINETRA-hosted imitation of either form, ever.
- **PROTOTYPE BASED LOGIN** (below both, visually secondary but clear) → `POST /auth/prototype` → signs in the IO → lands on the Case Docket.
- `/auth/callback/{provider}` renders "Integration pending: TRINETRA is not yet registered with this identity provider" while `SSO_CALLBACK_ENABLED=false`.
- Supervisor for the countersign demo: second browser/incognito, `POST /auth/prototype?role=supervisor` (button on an unlinked page `/auth/prototype/supervisor`).
- Keep the restricted-system warning text verbatim from the design.

## 01 · Case intake — `/cases/new`
Screenshots `01a`, `01b`.
- Ack input validated with `^(NCRP\/\d{4}\/[A-Z]{2}\/\d{7})$|^[A-Z0-9]{8,14}$`.
- **Ingest case** → `ComplaintSource.fetch()`. Eight particulars render in order as parsed: jurisdiction, filed, victim payment time, amount, recipient address, chain & asset, payment hash, complainant contact. (Sequential reveal of the returned record, ~120 ms stagger; not a fake timer.)
- "Referred to this desk today" from `referrals_today()`; clicking a row fills the input.
- Trace parameters card, read-only in v1: depth 5, window 8 h, strategy dominant fund flow, value floor "≥ 2% (438.80 USDT)" computed.
- **Start trace** disabled until "I reviewed the imported particulars" is ticked; creates case + snapshot, audits, redirects to Live Trace.
- `detect_chain` on the recipient: EVM/BTC → designed "Chain detected, adapter in progress" panel replaces Start trace.
- Unknown ack → "No complaint with this acknowledgement number on the NCRP feed. Check the number or ingest from the docket."

## 02 · Live trace — `/traces/{snapshot_id}`
Screenshots `02a` (running), `02b` (complete).
- **Left:** trace request (seed, chain, params incl. value floor) and "Data sources queried", driven by `source` events.
- **Centre:** headline, % and elapsed, hop table (hop, address, value carried, share, time at hop, classification + band) appended per `hop` event; current `work` line; dust aggregated as a quiet line per hop.
- **Right:** "Findings so far" (`stats`), "Custody candidates" (`candidate`) ranked by band.
- **Bottom bar** on `terminal`: one-sentence outcome + **Open canvas** + **View custody finding**.
- **Restart** → confirm dialog → new snapshot version; old one and its finding become superseded (banner on old views).
- Reloading replays stored events instantly. `error` → inline panel with Retry; never a blank page.
- Percent = heuristic (`visited / address_budget` blended with depth), labelled as progress, not certainty.

## 03 · Investigator canvas — `/cases/{id}/canvas?snapshot=`
Screenshot `03`; secondary reference `canvas-layout-reference.png`.
- Cytoscape + dagre, `rankDir:'LR'`. Dominant path left→right; VASP entity node at the end joined by a dashed "attribution link". Parked branches hang below their source hop, collapsed into "+N parked branches · value · share" (expand on click).
- Node card: hop badge, class-coloured circle icon, label, truncated address, amount, share, time. Edge labels: amount, share, "n txs · m min".
- Toggles: values, percentages, timestamps, parked branches. Zoom +/−/fit; ←/→ steps hops. Legend row.
- **Header strip:** case ref, status chip, reported / at rest / retained %, chain, asset, hops, transfers, started. **Strategy strip** (read-only): strategy, value threshold, stop-at.
- **Right panel** (selected node): title, chip, subtitle, full address + Copy; tabs **Overview** (fingerprint signals with pass/fail and value, "Why TRINETRA classified this…" from `classify()`), **Transactions (n)** (txid, time, amount, direction, explorer link), **Attribution** (entity, FIU-IND registration and status, rung + basis, registry revision), **Notes**. Buttons: Review custody finding (→ Verdict), View in explorer, Add note.
- **Bottom tabs:** Trace timeline (cards synced with selection), Fund summary (reported, credited to custody, parked A/B, dust, fees if any; must reconcile to the reported amount).
- **One Alpine store** holds the selection; graph, timeline and panel all read and write it.
- Hot wallet and deposit address are always different nodes and addresses.
- Superseded snapshot → banner "A newer trace exists" + link.

## 04 · Custody finding (verdict) — `/findings/{id}`
Screenshot `04`.
- Band chip + headline sentence from the terminal.
- KPIs: amount at rest (credited), hops to custody, time to custody.
- Mini path of the largest surviving share (H0…H4).
- "What the finding rests on": deposit-address attribution (rung + signals), unbroken value chain (each hop's txids), peel-chain remainder (parked branches, "off the dominant flow"), corroborating dockets (`linked_cases`).
- Custodian of record: entity, FIU-IND registration, chain/asset, notice channel, deposit address + copy.
- **Pre-notice review** — four checks (`demo_case.json → finding_review_checks`). "Prepare freeze notice" locked with "n checks remaining" until all four ticked; ticks saved + audited.
- **Terminal variants** (same frame, different headline and primary action):

| Kind | Headline | Primary action |
|---|---|---|
| `vasp_deposit` | Funds credited to an account at {entity} | Prepare freeze notice |
| `vasp_hot_wallet` | Funds reached {entity}'s pooled wallet | Prepare notice to identify the crediting account |
| `unregistered_vasp` | Funds reached {entity}, not registered with FIU-IND | Record finding; recovery probability low, no Indian legal hook |
| `stationary_funds` (alert styling) | Funds are live at an address with no custodian. Escalate now | Draft issuer-freeze request (Tether) |
| `mixer` | Trail terminates at a mixing service | Close with reason; pivot to mule bank accounts; evidence timestamped |
| `bridge` | Funds left TRON via a bridge | Record exit chain, amount, time; continuation matching not available in v1 |
| `trail_dissipated` | Remaining value fragmented below the value floor | Review parked branches; restart with value-weighted strategy |
| `depth_exhausted` | Depth cap reached before a custody point | Restart with higher depth (v1: show params, restart) |
| `unsupported_chain` | {family} address detected; adapter in progress | Record and route to manual analysis |
| `error` | The trace stopped: {reason} | Restart |

## 05 · Freeze notice — `/notices/{id}`
Screenshot `05`.
- **Left:** A4 preview (794×1123 CSS px) scaled to fit (ResizeObserver) rendered from `notice_document.html`, **the same template the PDF uses**. Letterhead (State Emblem, GOVERNMENT OF MAHARASHTRA · POLICE DEPARTMENT, office, address, email, notice no., date), body, particulars table, path summary with txids, requests (restrain, disclose KYC, preserve logs), signature, countersignature block, enclosures, copy to.
- **Top bar:** status chip (Draft · Awaiting countersignature · Countersigned · Dispatched), snapshot id, Download PDF, Print (`print.css`).
- **Right rail:** dispatch channels (checkboxes; targets from entity), response deadline 24 h / 72 h / 7 days (body text updates live), attachments (trace report PDF with verification appendix, graph exhibit, complaint extract), countersignature.
- **Countersignature** when amount ≥ `COUNTERSIGN_THRESHOLD_USDT`: "Request countersignature" → supervisor queue; supervisor signs from their own session; server rejects self-signing and non-supervisors (show the rule, not a generic error).
- **Dispatch** → confirm → PDF generated (watermark if configured) and hashed → one `dispatch` row per channel → statuses Queued → Sent, awaiting acknowledgement / Failed, retry required (+ Retry). Case → `notice_out`. PDF SHA-256 shown in full with copy.
- **Record custodian response** (new, required for rung 4 and docket values): received time, reference, KYC disclosed y/n, amount restrained, optional account reference hint → case `restraint_confirmed`; dispatch rows → acknowledged.

## 06 · Case docket — `/docket`
Screenshot `06`.
- KPI strip (computed): open dockets, value under trace, restrained to date (sum of `vasp_response.amount_restrained`), awaiting custodian, no custody found.
- Stage chips with counts: All · Trace running · Notice out · Restraint confirmed · Needs attention · Closed / no custody · Linked cases.
- Search: ack number or any address on the case (exact or prefix, server-side).
- Table: acknowledgement, amount reported, traced to, recovered / at rest (bar), stage chip, age. Row → the right screen for its stage.
- Needs attention (derived): acknowledgement overdue; trace stalled > 24 h. Bottom line "N cases need action…" computed.
- Linked cases via `linked_cases()`: after the main trace, `MH/0081902` shows linked to `MH/0084213` (shared H4).

## 07 · Risk check — `/risk-check`
Screenshots `07a`, `07b`.
- TRON address input, Run check, "Try" chips with the three example addresses.
- Result: band chip, headline, score 0–100, explanation, fact strip (first seen, inbound total, attribution, dockets), signal list, dockets naming the address (right rail), recent checks at this desk, "How this result may be used" box verbatim.
- Score (v1, deterministic, `services/risk.py`): docket hit on a terminal address +45 · restraint in force +20 · cluster with prior confirmed links +15 · classifier band high +15 / medium +8 · advisory list hit +30 · short lifetime +5 · cap 100. Bands ≥ 70 alert, 40–69 medium, < 40 low.
- No record → "Absence of a record is not clearance." The check reads records only; nothing is sent anywhere.

## Graph exhibit (for the report PDF and verdict)

Server-generated SVG from the snapshot (`services/exhibit.py`): same LR layout logic (fixed column per hop), same colours, mono labels, txids under each edge. Deterministic, so the same snapshot always yields the same exhibit. Don't rely on `cy.png()` for evidence: HTML-overlay node labels aren't painted into Cytoscape's canvas export (docs/10 D-02).
