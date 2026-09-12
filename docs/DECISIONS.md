# TRINETRA locked implementation decisions

- PDF renderer: Playwright Chromium for HTML-to-PDF; pypdf for metadata and final inspection.
- UI partials: Jinja2 pages with narrow HTMX-style server partials where useful.
- State: SQLite WAL with one Uvicorn worker and bounded background trace threads.
- Data mode: visible fixture/live indicator on authenticated screens.
- Evidence exhibits: server-generated deterministic SVG.
- Tunnel default: cloudflared for demos, with all blockchain calls remaining server-side.
- Theme: light only.
- v2 chains: TRON, Ethereum, BNB Smart Chain, Polygon, Base, Arbitrum, and Bitcoin mainnet.
- SSO: external CCTNS/Parichay links until OIDC metadata and onboarding exist.
- Dispatch: mock channels until legal copy and real credentials are approved.
