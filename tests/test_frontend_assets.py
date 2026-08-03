import hashlib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTMX_PATH = PROJECT_ROOT / "app" / "static" / "htmx.min.js"
HTMX_VERSION = "2.0.10"
HTMX_SHA256 = "71ea67185bfa8c98c39d31717c6fce5d852370fcdfd129db4543774d3145c0de"
APP_JS_PATH = PROJECT_ROOT / "app" / "static" / "app.js"
APP_JS_VERSION = "20260803-1"
APP_JS_SHA256 = "99d3f54014fb2d407fb9b252eeea9b479ff68f9fe4e711617c372e3fe5679625"


class ScriptSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        source = dict(attrs).get("src")
        if source:
            self.sources.append(source)


def script_sources(html: str) -> list[str]:
    parser = ScriptSourceParser()
    parser.feed(html)
    return parser.sources


def test_vendored_htmx_has_the_reviewed_release_digest() -> None:
    """Fail loudly if the executable dependency changes without review."""
    contents = HTMX_PATH.read_bytes()

    assert len(contents) == 51_238
    assert hashlib.sha256(contents).hexdigest() == HTMX_SHA256


def test_local_application_script_has_reviewed_digest() -> None:
    contents = APP_JS_PATH.read_bytes()
    assert len(contents) == 468
    assert hashlib.sha256(contents).hexdigest() == APP_JS_SHA256


def test_every_page_uses_pinned_same_origin_scripts(client) -> None:
    expected_sources = [
        f"assets/htmx.min.js?v={HTMX_VERSION}",
        f"assets/app.js?v={APP_JS_VERSION}",
    ]

    for route in ("/login", "/register", "/password/forgot"):
        response = client.get(route)
        assert response.status_code == 200
        sources = script_sources(response.text)

        assert sources == expected_sources
        assert '<meta name="htmx-config" content=\'{"includeIndicatorStyles":false}\'>' in (
            response.text
        )
        assert urlparse(sources[0]).scheme == ""
        assert urlparse(sources[0]).netloc == ""
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "'unsafe-inline'" not in response.headers["content-security-policy"]
        assert "'unsafe-eval'" not in response.headers["content-security-policy"]


def test_relative_htmx_source_resolves_inside_each_supported_mount(client) -> None:
    mount_paths = (
        "/",
        "/content/access-registry/",
        "/s/7f42/session/p/8000/",
    )

    for mount_path in mount_paths:
        response = client.get(
            "/login",
            headers={"rstudio-connect-app-base-url": mount_path.rstrip("/")},
        )
        assert response.status_code == 200
        source = script_sources(response.text)[0]
        external_page = f"https://host.example.gov{mount_path}login"

        assert urljoin(external_page, source) == (
            f"https://host.example.gov{mount_path}assets/htmx.min.js?v={HTMX_VERSION}"
        )


def test_versioned_htmx_response_is_exact_and_nosniff(client) -> None:
    response = client.get(f"/assets/htmx.min.js?v={HTMX_VERSION}")
    expected = HTMX_PATH.read_bytes()

    assert response.status_code == 200
    assert response.content == expected
    assert hashlib.sha256(response.content).hexdigest() == HTMX_SHA256
    assert response.headers["content-type"] == "text/javascript; charset=utf-8"
    assert response.headers["content-length"] == str(len(expected))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"]

    head = client.head(f"/assets/htmx.min.js?v={HTMX_VERSION}")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(expected))
    assert head.headers["etag"] == response.headers["etag"]


def test_htmx_asset_supports_conditional_and_range_requests(client) -> None:
    initial = client.get(f"/assets/htmx.min.js?v={HTMX_VERSION}")

    not_modified = client.get(
        f"/assets/htmx.min.js?v={HTMX_VERSION}",
        headers={"If-None-Match": initial.headers["etag"]},
    )
    assert not_modified.status_code == 304
    assert not_modified.content == b""

    partial = client.get(
        f"/assets/htmx.min.js?v={HTMX_VERSION}",
        headers={"Range": "bytes=0-31"},
    )
    assert partial.status_code == 206
    assert partial.content == HTMX_PATH.read_bytes()[:32]
    assert partial.headers["content-range"] == f"bytes 0-31/{HTMX_PATH.stat().st_size}"


def test_static_mount_has_no_fallback_or_path_traversal(client) -> None:
    assert client.get("/assets/not-present.js").status_code == 404
    assert client.get("/assets/").status_code == 404

    for path in (
        "/assets/%2e%2e/config.py",
        "/assets/%2e%2e%2fconfig.py",
        "/assets/%252e%252e%252fconfig.py",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert b"jwt_secret" not in response.content.lower()
