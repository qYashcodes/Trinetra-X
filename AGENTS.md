# TRINETRA root rulebook

The `kit/` directory is the handoff archive. Build and maintain the runnable product in the
repository root.

Hard rules:

- Browser assets are vendored under `app/static/`; templates must not reference CDNs.
- Amounts are integer base units internally. Format for display only.
- Timestamps are UTC epoch milliseconds internally and display in IST.
- Generated runtime state belongs under `var/`.
- Never write UI, notices, logs, or tests that assert guilt. Use custody, attribution, account, and
  investigative-aid language.
- A deposit address and a hot wallet are different legal objects.
- Government SSO and SAHYOG submission remain integration-pending unless real provider metadata,
  approved schemas, and credentials are configured.
