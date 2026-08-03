import os
import time
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Browser, expect

ADMIN_EMAIL = "browser.admin@example.gov"
ADMIN_PASSWORD = "Browser-Harbor-73!Signal"
ACCESS_COOKIE = "access_registry_access"
REFRESH_COOKIE = "access_registry_refresh"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_BROWSER_E2E") != "1",
        reason="set RUN_BROWSER_E2E=1 to run live Playwright proxy tests",
    ),
]


def assert_cookie_cannot_restore_session(
    browser: Browser, base_url: str, prefix: str, cookie: dict
) -> None:
    replay_context = browser.new_context(ignore_https_errors=True)
    try:
        replay_context.add_cookies(
            [
                {
                    key: cookie[key]
                    for key in (
                        "name",
                        "value",
                        "domain",
                        "path",
                        "expires",
                        "httpOnly",
                        "secure",
                        "sameSite",
                    )
                }
            ]
        )
        response = replay_context.request.get(
            f"{base_url}/security",
            headers={"Accept": "text/html"},
            max_redirects=0,
        )
        assert response.status == 303
        assert response.headers["location"].startswith(f"{prefix}/login?next=")
        assert "//" not in response.headers["location"]
        remaining = {item["name"] for item in replay_context.cookies(base_url)}
        assert ACCESS_COOKIE not in remaining
        assert REFRESH_COOKIE not in remaining
    finally:
        replay_context.close()


def test_login_htmx_refresh_cookie_and_logout_journey(browser: Browser, live_proxy) -> None:
    base_url = live_proxy["base_url"]
    prefix = live_proxy["prefix"]
    context = browser.new_context(ignore_https_errors=True)
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
        assert page.locator("base").get_attribute("href") == f"{prefix}/"
        assert page.evaluate("typeof window.htmx") == "object"
        assert page.evaluate("window.htmx.version") == "2.0.10"
        assert page.locator("script[src]").evaluate_all(
            "elements => elements.map(element => element.src)"
        ) == [
            f"{base_url}/assets/htmx.min.js?v=2.0.10",
            f"{base_url}/assets/app.js?v=20260803-1",
        ]
        assert page.request.get(f"{base_url}/assets/app.css").status == 200
        htmx_asset = page.request.get(f"{base_url}/assets/htmx.min.js?v=2.0.10")
        assert htmx_asset.status == 200
        assert htmx_asset.headers["content-type"] == "text/javascript; charset=utf-8"
        assert htmx_asset.headers["x-content-type-options"] == "nosniff"

        page.locator("#email").fill(ADMIN_EMAIL)
        page.locator("#password").fill(ADMIN_PASSWORD)
        page.get_by_role("button", name="Show password").click()
        assert page.locator("#password").get_attribute("type") == "text"
        page.get_by_role("button", name="Hide password").click()
        assert page.locator("#password").get_attribute("type") == "password"
        page.get_by_role("button", name="Sign in securely").click()
        page.wait_for_url(f"{base_url}/profile")
        expect(page.get_by_role("heading", name="Your information")).to_be_visible()

        issued_at = time.time()
        cookies = {cookie["name"]: cookie for cookie in context.cookies(base_url)}
        assert set(cookies) >= {ACCESS_COOKIE, REFRESH_COOKIE}
        if live_proxy["worker_cookie"]:
            assert cookies["connect.workerid"]["path"] == prefix
            assert cookies["connect.workerid"]["httpOnly"] is True
            assert cookies["connect.workerid"]["secure"] is True
            assert cookies["connect.workerid"]["sameSite"] == "Lax"
        for name in (ACCESS_COOKIE, REFRESH_COOKIE):
            assert cookies[name]["domain"] == "127.0.0.1"
            assert cookies[name]["path"] == prefix
            assert cookies[name]["httpOnly"] is True
            assert cookies[name]["secure"] is True
            assert cookies[name]["sameSite"] == "Lax"
        assert 8 * 60 < cookies[ACCESS_COOKIE]["expires"] - issued_at <= 11 * 60
        assert 7 * 3600 < cookies[REFRESH_COOKIE]["expires"] - issued_at <= 8 * 3600 + 60
        assert ACCESS_COOKIE not in page.evaluate("document.cookie")
        assert REFRESH_COOKIE not in page.evaluate("document.cookie")
        assert "connect.workerid" not in page.evaluate("document.cookie")

        expected_origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
        outside_url = f"{expected_origin}/outside-cookie-scope"
        assert console_errors == []
        with page.expect_request(lambda request: request.url == outside_url) as outside_request:
            outside_status = page.evaluate(
                "url => fetch(url).then(response => response.status)", outside_url
            )
        assert outside_status == 404
        outside_cookie_header = outside_request.value.all_headers().get("cookie", "")
        assert ACCESS_COOKIE not in outside_cookie_header
        assert REFRESH_COOKIE not in outside_cookie_header
        assert "connect.workerid" not in outside_cookie_header
        assert all("404 (Not Found)" in message for message in console_errors)
        console_errors.clear()

        page.locator("#organization").fill(f"Posit profile: {live_proxy['profile']}")
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

        original_access = cookies[ACCESS_COOKIE]
        original_refresh = cookies[REFRESH_COOKIE]
        context.clear_cookies(name=ACCESS_COOKIE)
        page.goto(f"{base_url}/security")
        expect(page.get_by_role("heading", name="Security", exact=True)).to_be_visible()
        refreshed = {cookie["name"]: cookie for cookie in context.cookies(base_url)}
        assert refreshed[ACCESS_COOKIE]["path"] == prefix
        assert refreshed[ACCESS_COOKIE]["secure"] is True
        assert refreshed[ACCESS_COOKIE]["value"] != original_access["value"]
        assert refreshed[REFRESH_COOKIE]["value"] != original_refresh["value"]
        assert_cookie_cannot_restore_session(browser, base_url, prefix, original_refresh)

        # Reusing the consumed token must revoke its replacement family and force the
        # current browser back through authentication.
        page.goto(f"{base_url}/security")
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        assert page.url.startswith(f"{base_url}/login?next=")
        page.locator("#email").fill(ADMIN_EMAIL)
        page.locator("#password").fill(ADMIN_PASSWORD)
        page.get_by_role("button", name="Sign in securely").click()
        page.wait_for_url(f"{base_url}/security")
        active = {cookie["name"]: cookie for cookie in context.cookies(base_url)}
        active_access = active[ACCESS_COOKIE]
        active_refresh = active[REFRESH_COOKIE]

        page.get_by_role("button", name="Sign out").click()
        page.wait_for_url(f"{base_url}/login")
        remaining = {cookie["name"] for cookie in context.cookies(base_url)}
        assert ACCESS_COOKIE not in remaining
        assert REFRESH_COOKIE not in remaining
        if live_proxy["worker_cookie"]:
            assert "connect.workerid" in remaining
        assert_cookie_cannot_restore_session(browser, base_url, prefix, active_access)
        assert_cookie_cannot_restore_session(browser, base_url, prefix, active_refresh)
        assert requested_origins == {expected_origin}
        assert page_errors == []
        assert console_errors == []
    finally:
        context.close()
