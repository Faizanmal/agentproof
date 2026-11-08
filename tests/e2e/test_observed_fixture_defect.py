"""Real Chromium journey against the repository-owned SQLite fixture app."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import Page, sync_playwright

FIXTURE_URL = os.getenv("AGENTPROOF_FIXTURE_URL", "").rstrip("/")
CREDENTIALS_FILE = Path(
    os.getenv("FIXTURE_CREDENTIALS_FILE", ".cache/agentproof-demo/credentials.json")
)
pytestmark = pytest.mark.skipif(
    not FIXTURE_URL or not CREDENTIALS_FILE.is_file(),
    reason="run make demo-seed and set AGENTPROOF_FIXTURE_URL to enable the owned browser test",
)


def test_seeded_dashboard_bug_fails_then_fixed_fixture_passes(tmp_path: Path) -> None:
    credentials = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    admin_headers = {"X-Fixture-Admin": credentials["admin_token"]}
    run_id = str(uuid4())
    label = f"AgentProof-E2E-{run_id}-subscription"
    browser_requests: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("request", lambda request: browser_requests.append(request.url))
        for mode, expected_observed, expected_verdict in (
            ("on", "3", "FAIL"),
            ("off", "2", "PASS"),
        ):
            admin = playwright.request.new_context(extra_http_headers=admin_headers)
            mode_response = admin.post(
                f"{FIXTURE_URL}/__agentproof_fixture__/admin/mode", params={"mode": mode}
            )
            assert mode_response.ok
            page.context.clear_cookies()
            page.goto(f"{FIXTURE_URL}/demo/login", wait_until="domcontentloaded")
            page.get_by_label("Email").fill(credentials["email"])
            page.get_by_label("Password").fill(credentials["password"])
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("heading", name="Dashboard").wait_for()
            page.get_by_role("link", name="View subscriptions").click()
            page.get_by_label("Subscription label").fill(label)
            page.get_by_role("button", name="Create subscription").click()
            row = page.locator('[data-testid="subscription-row"]').filter(has_text=label)
            row.get_by_label("Billing interval").select_option("yearly")
            row.get_by_role("button", name="Save interval").click()
            assert row.get_by_test_id("latest-subscription-interval").inner_text() == "yearly"
            row.get_by_role("button", name="Cancel synthetic subscription").click()
            assert row.get_by_test_id("latest-subscription-status").inner_text() == "Cancelled"
            page.get_by_role("link", name="Dashboard").click()
            observed = page.get_by_test_id("active-count-label").inner_text()
            screenshot_dir = Path(".cache/agentproof-demo/evidence")
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_dir / f"{mode}-{run_id}.png"), full_page=True)
            objective = "Active subscriptions: 2"
            actual_verdict = "PASS" if observed == objective else "FAIL"
            assert observed == f"Active subscriptions: {expected_observed}"
            assert actual_verdict == expected_verdict
            reset = admin.post(
                f"{FIXTURE_URL}/__agentproof_fixture__/admin/reset", params={"run_id": run_id}
            )
            assert reset.ok and reset.json()["synthetic_rows_deleted"] == 1
            admin.dispose()
        assert not any("169.254.169.254" in url for url in browser_requests)
        browser.close()


def test_adversarial_text_is_data_and_metadata_link_is_never_requested() -> None:
    credentials = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page: Page = browser.new_page()
        requests: list[str] = []
        page.on("request", lambda request: requests.append(request.url))
        page.goto(f"{FIXTURE_URL}/demo/login", wait_until="domcontentloaded")
        page.get_by_label("Email").fill(credentials["email"])
        page.get_by_label("Password").fill(credentials["password"])
        page.get_by_role("button", name="Sign in").click()
        page.get_by_role("link", name="Inspect the quoted page").click()
        assert "169.254.169.254" in page.get_by_role("main").inner_text()
        assert not any("169.254.169.254" in url for url in requests)
        browser.close()
