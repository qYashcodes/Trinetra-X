from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_presence_controller_node_scenarios() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the browser-controller unit scenarios.")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests" / "js" / "test_presence_controller.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_presence_bootstrap_wires_visibility_shield_and_pagehide_cleanup() -> None:
    source = (ROOT / "app" / "static" / "presence-detection.js").read_text(
        encoding="utf-8"
    )
    assert 'document.addEventListener("visibilitychange"' in source
    assert 'window.addEventListener("trinetra:session-shield-change"' in source
    assert 'window.addEventListener("pagehide"' in source
    assert "void monitor.dispose()" in source
    assert "http://" not in source
    assert "https://" not in source


def test_session_shield_exports_only_obscure_and_lock_state() -> None:
    source = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "window.trinetraSessionShield = Object.freeze" in source
    assert 'obscure: (customMessage, source = "external")' in source
    assert "isLocked: () => locked" in source
    exported = source.split("window.trinetraSessionShield = Object.freeze", 1)[1].split(
        "});", 1
    )[0]
    assert "hideShield" not in exported
