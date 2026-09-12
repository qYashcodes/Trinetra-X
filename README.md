# TRINETRA

TRINETRA is an offline-first prototype for Indian cyber cells to trace reported crypto payments to
custody points, preserve evidence, and prepare verifiable freeze notices.

This repository is implemented from the `kit/` handoff archive as a greenfield build. The current
slice includes deterministic fixture tracing, v1 workflow screens, v2 chain contracts, evidence
hashing, audit primitives, risk checks, bridge candidates, and protocol-complete integration shells.

## Quick start

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.main
```

Open `http://127.0.0.1:8000` and use **PROTOTYPE BASED LOGIN**.

Local verification that does not require the web dependencies:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

## Data and integrity

- `docs/demo_case.json` is the only fixture source.
- Generated runtime files belong under `var/` and are ignored by git.
- Amounts are integer base units in storage and are formatted only at the edge.
- Trace snapshots use canonical JSON SHA-256 hashes.
- Audit rows are append-only and hash chained.
