# TODO_ENGINE

The greenfield bridge avoids TODO stubs for the fixture path. Remaining live work is deliberately
adapter-scoped:

- Verify the gated TronGrid/TRON bounded multi-hop trace slice against credentialed production
  response samples, rate-limit behavior, schema drift, and repeated live smoke traces before using
  it operationally.
- Add production live TRON worker orchestration, live rate-limit dashboards and custody-candidate
  gates backed by provider evidence; bounded synchronous traversal, offline worker cycles,
  retry/backoff state and provider-backoff coverage telemetry are implemented.
- Add Etherscan V2 token transfer pagination per configured chain.
- Add Blockstream Esplora outspend traversal for live Bitcoin traces.
- Populate the reviewed VASP and bridge registries from authoritative sources.
- Complete leakage-aware classifier calibration with documented metrics.
