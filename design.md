# TRINETRA — Design Handoff

Crypto-forensics case management prototype for a virtual asset tracing desk (Smart India Hackathon 2026 concept, not an official Government of India product). Reference for recreating in a production codebase — not for shipping the HTML as-is.

## Screens

| # | File | Purpose |
|---|------|---------|
| — | `-official- -login screen- Auth.dc.html` | Sign in |
| 1 | `-official- -first screen- Case Intake.dc.html` | New complaint intake |
| 2 | `-official- -second screen- Live Trace.dc.html` | Active fund-flow traces in progress |
| 3 | `-official- -third screen- Investigator Canvas v2.dc.html` | Interactive on-chain trace graph + hop detail panel |
| 4 | `-official- -fourth screen- Verdict.dc.html` | Custody findings / verdict summary |
| 5 | `-official- -fifth screen- Freeze Notice.dc.html` | Printable A4 government freeze notice document |
| 6 | `-official- -sixth screen- Case Docket.dc.html` | List of all open/closed cases |
| 7 | `-official- -seventh screen- Risk Check.dc.html` | Address/entity risk lookup tool |
| shared | `NavRailLight.dc.html` | Left navigation rail, imported into every post-login screen |

## Layout

- Fixed canvas per screen: `1620×972` (Investigator Canvas) — other screens follow the same fixed app-shell width/height rather than fluid reflow.
- Shell = `NavRailLight` (240px, fixed) + main content (`flex:1`), stacked in a column with a `42px` footer bar.
- Main content padding: `14px 16px 0`, gap `12px` between stacked panels.
- Panels: white cards, `1px solid #e3e8ef` border, `10px` border-radius.
- Investigator Canvas graph row scrolls horizontally/vertically (`overflow:auto`) inside its card when nav rail + side panels compress it; legend row wraps (`flex-wrap:wrap`) to avoid overflow.
- Right detail panel (Investigator Canvas): fixed `344px`, own internal scroll.
- Freeze Notice is the exception: rendered via `doc-page.js` scaled to A4, not the app-shell grid — letterhead, numbered sections, signature block, government notice formatting.

## Design tokens

**Colors**
- Navy (primary/brand, buttons, active states): `#12325e`, hover `#0b1e39`, link `#17427f` / `#0b2e6f`, link hover `#0e2b56`/`#082657`
- Green (success, verified, VASP): `#16a34a`, text `#15803d`, chip bg `#eefaf2`, chip border `#bfe6cf`
- Amber (caution, peel chain, probable): `#c98a12`, text `#9a6b06`, chip bg `#fdf6e8`/`#fdf3e0`, chip border `#ecd9a8`
- Red (mixer/risk): `#dc2626`
- Purple (bridge): `#7c3aed`
- Ink (primary text): `#111a2b`
- Slate (secondary text): `#5c6880` / `#3c4a61`
- Muted text: `#8a94a6`
- Borders: `#e3e8ef` (standard), `#eef1f6` (hairline), `#cfd7e3` (button border), `#dde3ec`
- Backgrounds: page `#eef1f6`/`#f5f7fa`, card `#ffffff`, subtle fill `#f1f4f8`/`#f7f9fc`, selected row `#e6efff`/`#eef4fd`/`#f7faff`
- Focus ring: `#2563eb`

**Typography**
- Primary: Noto Sans (400/500/600/700)
- Monospace (addresses, hashes): Noto Sans Mono (400/500/600)
- Devanagari accent (logo mark): Noto Sans Devanagari (500/600)
- Sizes in use: 21px (page title), 19px (panel title), 16px (section heading), 14–15px (body/buttons/nav), 13–13.5px (secondary/labels), 12–12.5px (captions), 9.5–11px (micro badges)
- Weights: 700 headings/logo, 600 emphasis/active, 500 medium, 400 default

**Spacing / radius**
- Card radius: `10px`; buttons/inputs `6–8px`; pill/chip `20px`
- Card padding: `14–18px`; nav item min-height `44px` (touch target)
- Standard gaps: `8–16px`

**Borders / shadows**
- 1px solid borders throughout, no drop shadows except toggle knob (`0 1px 2px rgba(17,26,43,0.25)`) and selected-node focus ring (`0 0 0 4px rgba(37,99,235,0.16)`)

## Navigation rail (`NavRailLight`)

- 240px fixed width, white background, right border `#e3e8ef`
- Top: 4px tricolor strip (saffron `#ff9933` / white / green `#138808`)
- Header: circular Devanagari monogram "त्रि" + "TRINETRA" wordmark + subtitle
- Grouped links: Cases (New case, Case docket), Investigations (Active traces, Investigator canvas), Outputs (Custody findings, Freeze notices), Tools (Risk check)
- Active item: bg `#e6efff`, text `#111a2b`, 3px left border `#12325e`; inactive: text `#5c6880`
- Each item ≥44px tall, optional count badge in Noto Sans Mono
- Footer: officer name, unit, prototype disclaimer text
- Prop: `active` (enum of screen keys) drives highlighting; `officerName` (text)

## Investigator Canvas specifics

- Case header card: case ref, "Trace complete" status chip (green dot), reported vs. at-rest amount, stat strip (chain/asset/hops/transfers/started)
- Trace strategy bar: strategy selector button, value-threshold and stop-condition readouts
- Graph card: toggle row (Show values / Show parked branches), horizontally-scrollable node/edge grid (address/cluster/VASP node types, colored by kind: blue address, slate cluster, amber VASP, green verified VASP), parked-branch expandable sub-nodes, minimap + zoom controls, wrapping legend row
- Bottom card: tabbed (Trace timeline / Fund summary / Key insights / Alerts), horizontal timeline cards per hop with badge/amount/time/delta
- Right detail panel (344px): hop stepper, title + status chip, truncated address with copy button, tabs (Overview/Transactions/Attribution/Notes), key-value rows, two full-width navy CTA buttons stacked — **"View transactions"** then **"Review custody findings"** (links to Verdict screen) — both same navy `#12325e` fill, white text, `8px` radius, hover `#0b1e39`, then "Why flagged" checklist, 2×2 action grid, entity info block

## Assets

No raster imagery; `image-slot.js` available for user-supplied photos/documents but not currently placed. All icons are inline text glyphs/Unicode (→ ← ✕ ▾ ⊕ ↗ ⚑ ✎), no icon font or SVG icon set.

## Files in this bundle

All screens listed in the table above, plus `NavRailLight.dc.html` (shared nav), `image-slot.js`, `doc-page.js` (A4 document shell used by Freeze Notice), `support.js` (DC runtime, do not modify).
