import os
from urllib.parse import urlparse

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
    page_errors: list[str] = []
    console_errors: list[str] = []
    requested_origins: set[str] = set()
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.on(
        "request",
        lambda request: requested_origins.add(
            f"{urlparse(request.url).scheme}://{urlparse(request.url).netloc}"
        ),
    )
    try:
        response = page.goto(f"{base_url}/login")
        assert response is not None and response.status == 200
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        assert page.evaluate("typeof window.htmx") == "object"
        assert page.evaluate("window.htmx.version") == "2.0.10"
        assert page.locator("script[src]").count() == 1
        assert page.locator("script[src]").evaluate("element => element.src") == (
            f"{base_url}/assets/htmx.min.js?v=2.0.10"
        )
        assert page.request.get(f"{base_url}/assets/app.css").status == 200
        htmx_asset = page.request.get(f"{base_url}/assets/htmx.min.js?v=2.0.10")
        assert htmx_asset.status == 200
        assert htmx_asset.headers["content-type"] == "text/javascript; charset=utf-8"
        assert htmx_asset.headers["x-content-type-options"] == "nosniff"

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
        with page.expect_request(
            lambda request: request.url == f"{base_url}/profile"
        ) as request_info:
            with page.expect_response(
                lambda response: response.url == f"{base_url}/profile"
            ) as response_info:
                page.get_by_role("button", name="Save changes").click()
        assert request_info.value.method == "POST"
        assert request_info.value.headers["hx-request"] == "true"
        assert response_info.value.status == 200
        expect(page.get_by_text("Your profile has been updated")).to_be_visible()
        assert page.url == f"{base_url}/profile"
        assert page.evaluate("window.htmx.version") == "2.0.10"

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
        expected_origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
        assert requested_origins == {expected_origin}
        assert page_errors == []
        assert console_errors == []
    finally:
        context.close()
