from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_active_trace_workspace_matches_reference_contract() -> None:
    with TestClient(app) as client:
        client.post("/auth/prototype", data={"role": "io"})
        client.get("/docket")

        latest = client.get("/traces", follow_redirects=False)
        assert latest.status_code == 307
        assert latest.headers["location"].startswith("/traces/")

        trace = client.get(latest.headers["location"])
        assert trace.status_code == 200
        assert 'data-trace-replay' in trace.text
        assert '/static/trace.css?v=20260921-hop-replay' in trace.text
        assert '/static/trace.js?v=20260921-hop-replay' in trace.text
        assert 'data-final-duration-seconds="10"' in trace.text
        assert 'data-final-transfers="5"' in trace.text
        assert 'data-trace-mode="fixture"' in trace.text
        assert '<div><dt>Transfers read</dt><dd class="mono" data-stat-transfers>0</dd></div>' in trace.text
        assert 'Trace under effect' in trace.text
        assert 'trace-progress-track' not in trace.text
        assert 'hop 1 of 5' not in trace.text
        assert 'Traversal closed at hop 4' not in trace.text
        assert trace.text.count('data-trace-row=') == 5
        assert 'Unattributed cluster K-88' in trace.text
        assert 'Self-custody remainder' in trace.text
        assert 'Coinsphere Global (VASP)' in trace.text
        assert 'Findings are provisional until the traversal closes.' in trace.text
        assert 'Restart this trace?' in trace.text
        assert 'Switch to dark console' not in trace.text


def test_trace_assets_are_local_and_available() -> None:
    with TestClient(app) as client:
        css = client.get("/static/trace.css")
        js = client.get("/static/trace.js")
        assert css.status_code == 200
        assert js.status_code == 200
        assert "@keyframes trace-scan" not in css.text
        assert "trace-progress-scan" not in css.text
        assert "prefers-reduced-motion" in css.text
        assert "const traceReplayDelayMs = 1400" in js.text
        assert "replayEventTypes" in js.text
        assert "isFixtureTrace" in js.text
        assert '"stats"' in js.text
        assert "formatPctBp" in js.text
        assert "eventQueue" in js.text
        assert "showModal" in js.text
