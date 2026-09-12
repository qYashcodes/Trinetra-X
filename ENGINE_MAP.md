# ENGINE_MAP

This is a greenfield build because the repository did not contain the built `engine/` described by
`kit/`.

The application imports tracing behavior through `app/engine_bridge.py`. The first implementation is
fixture-complete and deterministic for the canonical TRON case, with v2 family contracts for EVM and
Bitcoin. Live adapters are isolated under `engine/adapters/` and fail closed without credentials.

## Implemented bridge surface

- `detect_chain`
- `resolve_chain_activity`
- `trace`
- `classify`
- `resolve_vasp`
- `linked_cases`
- `explorer_url`
- `engine_mode`

## Version tags

- `engine-v1.0`: fixture-complete TRON contract implemented in bridge-compatible form.
- `demo-v1`: eight-screen workflow implemented against deterministic demo data.
- `trinetra-v2`: pending full live-provider calibration and registry review.
