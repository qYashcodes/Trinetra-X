from __future__ import annotations

import hashlib
from pathlib import Path
import re

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from app.main import app


ASSET_ROOT = Path(__file__).resolve().parents[1] / "app" / "static" / "vendor" / "mediapipe"

EXPECTED_SHA256 = {
    "vision_bundle.mjs": (
        "D885630C297C0B20B1FE86096CB06291C4C8080876F27852E724F24AC603713F"
    ),
    "blaze_face_short_range.tflite": (
        "B4578F35940BF5A1A655214A1CCE5CAB13EBA73C1297CD78E1A04C2380B0152F"
    ),
    "wasm/vision_wasm_internal.js": (
        "E170EE67DD4E16C1A6FCD8840A206687E5A59B22C20E4A902BC445B095454D73"
    ),
    "wasm/vision_wasm_internal.wasm": (
        "8DA277A733926EACD0474B8704B36742D6EC3231C57A860C5B889DFF8F1DF886"
    ),
    "wasm/vision_wasm_nosimd_internal.js": (
        "E81D715A3D42CC3373602EB2F7AFF795D164934DB680E32496B65DAB537F9658"
    ),
    "wasm/vision_wasm_nosimd_internal.wasm": (
        "A28483CD42E74E855BF5EBDB6B40D9B66A5B49E35E95020BC97669E6822A3192"
    ),
}


def test_mediapipe_presence_assets_are_a_pinned_matched_set() -> None:
    for relative_path, expected_hash in EXPECTED_SHA256.items():
        asset = ASSET_ROOT / relative_path
        assert asset.is_file(), f"Missing MediaPipe runtime asset: {relative_path}"
        assert hashlib.sha256(asset.read_bytes()).hexdigest().upper() == expected_hash


def test_default_fileset_resolver_loader_pairs_are_present() -> None:
    assert (ASSET_ROOT / "wasm" / "vision_wasm_internal.js").is_file()
    assert (ASSET_ROOT / "wasm" / "vision_wasm_internal.wasm").is_file()
    assert (ASSET_ROOT / "wasm" / "vision_wasm_nosimd_internal.js").is_file()
    assert (ASSET_ROOT / "wasm" / "vision_wasm_nosimd_internal.wasm").is_file()


def test_mediapipe_runtime_and_model_license_notices_are_distributed() -> None:
    license_text = (ASSET_ROOT / "LICENSE.apache-2.0.txt").read_text(encoding="utf-8")
    notices = (ASSET_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in license_text
    assert "@mediapipe/tasks-vision` version `1.0.1" in notices
    assert "BlazeFace short-range float16 model" in notices


def test_real_mediapipe_detector_initializes_from_fastapi_same_origin_assets() -> None:
    entry_html = """
    <!doctype html><meta charset="utf-8">
    <script type="module">
      import { FaceDetector, FilesetResolver } from
        "/static/vendor/mediapipe/vision_bundle.mjs";
      try {
        const fileset = await FilesetResolver.forVisionTasks(
          "/static/vendor/mediapipe/wasm"
        );
        const detector = await FaceDetector.createFromOptions(fileset, {
          baseOptions: {
            modelAssetPath:
              "/static/vendor/mediapipe/blaze_face_short_range.tflite",
            delegate: "CPU",
          },
          runningMode: "VIDEO",
        });
        detector.close();
        window.presenceSmoke = { ok: true };
      } catch (error) {
        window.presenceSmoke = { ok: false, error: String(error?.stack || error) };
      }
    </script>
    """
    requests: list[str] = []
    unexpected: list[str] = []
    with TestClient(app) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def serve_from_app(route) -> None:
            url = route.request.url
            requests.append(url)
            path = re.sub(r"^http://localhost(?::\d+)?", "", url).split("?", 1)[0]
            if path == "/presence-smoke":
                route.fulfill(
                    status=200,
                    headers={
                        "content-type": "text/html",
                        "content-security-policy": "connect-src 'self'",
                    },
                    body=entry_html,
                )
                return
            if path.startswith("/static/vendor/mediapipe/"):
                response = client.get(path)
                route.fulfill(
                    status=response.status_code,
                    headers={
                        "content-type": response.headers.get(
                            "content-type", "application/octet-stream"
                        )
                    },
                    body=response.content,
                )
                return
            unexpected.append(url)
            route.abort()

        page.route("**/*", serve_from_app)
        page.goto("http://localhost/presence-smoke")
        page.wait_for_function("window.presenceSmoke !== undefined", timeout=30_000)
        result = page.evaluate("window.presenceSmoke")
        browser.close()

    assert result == {"ok": True}, result
    assert unexpected == []
    assert requests
    assert all(url.startswith("http://localhost/") for url in requests)
