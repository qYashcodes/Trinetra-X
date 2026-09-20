from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright


WIDTHS = (360, 768, 1024, 1280, 1440, 1920)


@dataclass
class Result:
    path: str
    width: int
    status: int
    scroll_width: int
    client_width: int
    clipped_controls: list[str]
    console_errors: list[str]


def wait_for_stable_page(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(450)


def discover_paths(page: Page, role: str) -> list[str]:
    page.goto("http://127.0.0.1:8013/login")
    page.locator(f'form[action="/auth/prototype"] input[value="{role}"]').locator("..").locator("button").click()
    wait_for_stable_page(page)
    page.goto("http://127.0.0.1:8013/docket")
    wait_for_stable_page(page)
    hrefs = page.locator("a[href]").evaluate_all("els => els.map(el => el.getAttribute('href'))")

    paths = ["/docket", "/traces", "/dispatch-tracker"]
    patterns = (
        re.compile(r"^/traces/\d+$"),
        re.compile(r"^/cases/\d+/canvas(?:\?snapshot=\d+)?$"),
        re.compile(r"^/notices/\d+$"),
    )
    for pattern in patterns:
        match = next((href for href in hrefs if href and pattern.match(href)), None)
        if match:
            paths.append(match)
            if pattern.pattern.startswith("^/notices/"):
                paths.append(f"{match}/workflow")
    notice_case = page.locator("a.docket-ack", has_text="V3-1002" if role == "supervisor" else "V3-1004")
    if notice_case.count():
        case_path = notice_case.first.get_attribute("href")
        if case_path:
            page.goto(f"http://127.0.0.1:8013{case_path}")
            wait_for_stable_page(page)
            page.goto("http://127.0.0.1:8013/notices")
            wait_for_stable_page(page)
            notice_path = urlparse(page.url).path
            if re.match(r"^/notices/\d+$", notice_path):
                paths.extend((notice_path, f"{notice_path}/workflow"))
    return list(dict.fromkeys(paths))


def inspect(page: Page, path: str, width: int, role: str) -> Result:
    errors: list[str] = []

    def on_console(message) -> None:
        if message.type == "error":
            errors.append(message.text)

    page.on("console", on_console)
    page.set_viewport_size({"width": width, "height": 900})
    response = page.goto(f"http://127.0.0.1:8013{path}", wait_until="domcontentloaded")
    wait_for_stable_page(page)
    metrics = page.evaluate(
        r"""
        () => {
          const root = document.documentElement;
          const controls = [...document.querySelectorAll('a,button,input,select,textarea')];
          const clipped = controls.filter((element) => {
            const style = getComputedStyle(element);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (element.closest('[data-nav-drawer]') && !document.body.classList.contains('nav-drawer-open')) return false;
            const rect = element.getBoundingClientRect();
            if (!rect.width || !rect.height) return false;
            return rect.left < -1 || rect.right > root.clientWidth + 1;
          }).map((element) => {
            const label = element.getAttribute('aria-label') || element.textContent || element.name || element.id || element.tagName;
            const rect = element.getBoundingClientRect();
            const parent = element.parentElement?.getBoundingClientRect();
            const chain = [];
            let node = element.parentElement;
            while (node && chain.length < 6) {
              const bounds = node.getBoundingClientRect();
              chain.push(`${node.tagName.toLowerCase()}.${node.className || '-'}:${Math.round(bounds.left)}..${Math.round(bounds.right)}`);
              node = node.parentElement;
            }
            return `${label.trim().replace(/\s+/g, ' ').slice(0, 60)} [${Math.round(rect.left)}..${Math.round(rect.right)}; ${chain.join(' > ')}]`;
          });
          return {
            scrollWidth: root.scrollWidth,
            clientWidth: root.clientWidth,
            clipped,
          };
        }
        """
    )
    if role == "supervisor":
        if path == "/docket" and page.locator('[data-panel-id="working-trail"]').count():
            errors.append("ACP markup contains the IO-only working trail.")
        if path.endswith("/workflow") and page.locator('[data-parameter-form], [data-panel-id="notice-authoring"]').count():
            errors.append("ACP markup contains IO-only notice authoring controls.")
    page.remove_listener("console", on_console)
    return Result(
        path=path,
        width=width,
        status=response.status if response else 0,
        scroll_width=metrics["scrollWidth"],
        client_width=metrics["clientWidth"],
        clipped_controls=metrics["clipped"],
        console_errors=errors,
    )


def main() -> int:
    role = sys.argv[1] if len(sys.argv) > 1 else "io"
    if role not in {"io", "supervisor"}:
        raise SystemExit("Role must be 'io' or 'supervisor'.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        paths = discover_paths(page, role)
        results = [inspect(page, path, width, role) for path in paths for width in WIDTHS]
        browser.close()

    failures = [
        result
        for result in results
        if result.status >= 400
        or result.scroll_width > result.client_width + 1
        or result.clipped_controls
        or result.console_errors
    ]
    report = {
        "role": role,
        "paths": paths,
        "widths": WIDTHS,
        "checks": len(results),
        "failures": [asdict(result) for result in failures],
    }
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
