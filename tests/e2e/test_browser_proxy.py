import os

import pytest
from playwright.sync_api import Browser, expect

ADMIN_EMAIL = "browser.admin@example.gov"
ADMIN_PASSWORD = "Browser-Harbor-73!Signal"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_BROWSER_E2E") != "1",
        reason="set RUN_BROWSER_E2E=1 to run live Playwright proxy tests",
    ),
]


def test_login_htmx_refresh_cookie_and_logout_journey(browser: Browser, live_proxy) -> None:
    base_url = live_proxy["base_url"]
    prefix = live_proxy["prefix"]
    context = browser.new_context()
    page = context.new_page()
    try:
        response = page.goto(f"{base_url}/login")
        assert response is not None and response.status == 200
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        assert page.evaluate("typeof window.htmx") == "object"
        assert page.request.get(f"{base_url}/assets/app.css").status == 200
        assert page.request.get(f"{base_url}/assets/htmx.min.js").status == 200

        page.locator("#email").fill(ADMIN_EMAIL)
        page.locator("#password").fill(ADMIN_PASSWORD)
        page.get_by_role("button", name="Sign in securely").click()
        page.wait_for_url(f"{base_url}/profile")
        expect(page.get_by_role("heading", name="Your information")).to_be_visible()

        cookies = {cookie["name"]: cookie for cookie in context.cookies(base_url)}
        assert set(cookies) >= {"access_registry_access", "access_registry_refresh"}
        for name in ("access_registry_access", "access_registry_refresh"):
            assert cookies[name]["path"] == prefix
            assert cookies[name]["httpOnly"] is True
            assert cookies[name]["sameSite"] == "Lax"

        page.locator("#organization").fill(f"Proxy mode: {live_proxy['mode']}")
        page.get_by_role("button", name="Save changes").click()
        expect(page.get_by_text("Your profile has been updated")).to_be_visible()
        assert page.url == f"{base_url}/profile"

        context.clear_cookies(name="access_registry_access")
        page.goto(f"{base_url}/security")
        expect(page.get_by_role("heading", name="Security", exact=True)).to_be_visible()
        refreshed = {cookie["name"]: cookie for cookie in context.cookies(base_url)}
        assert refreshed["access_registry_access"]["path"] == prefix

        page.get_by_role("button", name="Sign out").click()
        page.wait_for_url(f"{base_url}/login")
        remaining = {cookie["name"] for cookie in context.cookies(base_url)}
        assert "access_registry_access" not in remaining
        assert "access_registry_refresh" not in remaining
    finally:
        context.close()
