"""Opt-in real-browser UI checks for the same-origin web/API stack.

Set AGENTPROOF_WEB_BASE_URL to a running local web app. Screenshots are saved
under .cache/ui-e2e and are generated only by these real browser checks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.getenv("AGENTPROOF_WEB_BASE_URL", "").rstrip("/")
AXE_FILES = list(
    (ROOT / "node_modules" / ".pnpm").glob("axe-core@*/node_modules/axe-core/axe.min.js")
)
pytestmark = pytest.mark.skipif(
    not BASE_URL, reason="set AGENTPROOF_WEB_BASE_URL to enable real UI checks"
)


@pytest.fixture
def browser_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
        yield page
        browser.close()


def assert_axe(page: Page) -> None:
    if not AXE_FILES:
        pytest.skip("axe-core is not present in the locked local pnpm store")
    page.add_script_tag(path=str(AXE_FILES[0]))
    violations = page.evaluate("""async () => {
      const results = await axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] }
      });
      return results.violations
        .filter((item) => ['critical', 'serious'].includes(item.impact))
        .map((item) => ({
          id: item.id,
          impact: item.impact,
          description: item.description,
          nodes: item.nodes.length
        }));
    }""")
    assert not violations, f"Critical/serious axe violations: {violations}"


@pytest.mark.parametrize(
    "path,heading",
    [
        ("/", "Make every test"),
        ("/login", "Sign in to AgentProof"),
        ("/register", "Start with a verified target"),
        ("/auth/forgot", "Reset your password"),
    ],
)
def test_public_routes_are_accessible_and_capture_real_screenshots(
    browser_page: Page, path: str, heading: str
) -> None:
    response = browser_page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
    assert response is not None and response.status == 200
    browser_page.get_by_role("heading", name=heading).wait_for()
    assert_axe(browser_page)
    target = ROOT / ".cache" / "ui-e2e"
    target.mkdir(parents=True, exist_ok=True)
    browser_page.screenshot(
        path=str(target / f"{path.strip('/') or 'landing'}-desktop.png"), full_page=True
    )


def test_mobile_layout_and_keyboard_focus(browser_page: Page) -> None:
    browser_page.set_viewport_size({"width": 360, "height": 800})
    browser_page.goto(f"{BASE_URL}/", wait_until="networkidle")
    assert browser_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    browser_page.keyboard.press("Tab")
    assert browser_page.locator(":focus-visible").count() == 1
    target = ROOT / ".cache" / "ui-e2e"
    target.mkdir(parents=True, exist_ok=True)
    browser_page.screenshot(path=str(target / "landing-mobile-360.png"), full_page=True)


def test_login_and_registration_reject_invalid_form_values(browser_page: Page) -> None:
    for path in ("/login", "/register"):
        browser_page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        form = browser_page.locator("form")
        assert not form.evaluate("(element) => element.checkValidity()")
        browser_page.get_by_label("Email address").fill("not-an-email")
        browser_page.get_by_label("Password").fill("short")
        assert not form.evaluate("(element) => element.checkValidity()")


def test_private_app_route_requires_a_session(browser_page: Page) -> None:
    browser_page.goto(f"{BASE_URL}/app", wait_until="networkidle")
    browser_page.get_by_role("heading", name="Sign in required").wait_for(timeout=10_000)
    assert browser_page.get_by_role("link", name="Sign in").is_visible()
