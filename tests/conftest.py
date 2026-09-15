from __future__ import annotations

import os


_LIVE_ENV_NAMES = (
    "TRINETRA_MODE",
    "TRINETRA_ENABLE_LIVE_TRON",
    "TRONGRID_API_KEY",
    "TRONSCAN_API_KEY",
    "TRINETRA_LIVE_TRON_SCHEMA_VERIFIED",
    "TRINETRA_LIVE_TRON_SMOKE_VERIFIED",
    "TRINETRA_LIVE_TRON_TRACE_VERIFIED",
)


os.environ["TRINETRA_DISABLE_DOTENV"] = "true"
for _name in _LIVE_ENV_NAMES:
    os.environ.pop(_name, None)
