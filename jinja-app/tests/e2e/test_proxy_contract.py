import json

import pytest
from posit_proxy import (
    normalize_prefix,
    normalize_public_origin,
    platform_request_headers,
    sanitize_request_headers,
)


def test_connect_profile_replaces_spoofed_platform_headers() -> None:
    incoming = [
        ("Accept", "text/html"),
        ("Host", "attacker.example"),
        ("X-Forwarded-For", "203.0.113.44"),
        ("X-RSC-Request", "https://attacker.example/admin"),
        ("RStudio-Connect-Credentials", '{"user":"administrator"}'),
    ]
    config = {
        "platform": "connect",
        "prefix": "/content/42",
        "public_origin": "https://connect.example.gov",
        "connect_base": "absolute",
    }

    headers = sanitize_request_headers(incoming)
    headers.update(
        platform_request_headers(
            config,
            client_ip="127.0.0.1",
            path="/content/42/profile",
            query="tab=sessions",
        )
    )

    assert headers["Accept"] == "text/html"
    assert headers["Host"] == "connect.example.gov"
    assert headers["X-Forwarded-For"] == "127.0.0.1"
    assert headers["X-Forwarded-Proto"] == "https"
    assert headers["X-Forwarded-Port"] == "443"
    assert headers["X-Forwarded-Prefix"] == "/content/42"
    assert headers["X-RSC-Request"] == (
        "https://connect.example.gov/content/42/profile?tab=sessions"
    )
    assert headers["RStudio-Connect-App-Base-URL"] == ("https://connect.example.gov/content/42")
    assert json.loads(headers["RStudio-Connect-Credentials"])["user"] == "browser.viewer"


def test_workbench_profile_supplies_external_root_and_forwarded_authority() -> None:
    headers = platform_request_headers(
        {
            "platform": "workbench",
            "prefix": "/rstudio/s/session/p/30507931",
            "public_origin": "https://gateway.example.gov:8443",
        },
        client_ip="127.0.0.1",
        path="/rstudio/s/session/p/30507931/login",
    )

    assert headers["Host"] == "gateway.example.gov:8443"
    assert headers["X-Forwarded-Port"] == "8443"
    assert headers["X-RStudio-Root-Path"] == "/rstudio/s/session/p/30507931"
    assert "RStudio-Connect-App-Base-URL" not in headers
    assert "X-RSC-Request" not in headers


@pytest.mark.parametrize("value", ["content/42", "//attacker.example/path", ""])
def test_proxy_rejects_invalid_prefixes(value: str) -> None:
    with pytest.raises(ValueError, match="absolute path"):
        normalize_prefix(value)


@pytest.mark.parametrize(
    "value",
    [
        "connect.example.gov",
        "ftp://connect.example.gov",
        "https://connect.example.gov/prefix",
        "https://connect.example.gov?query=yes",
    ],
)
def test_proxy_rejects_invalid_public_origins(value: str) -> None:
    with pytest.raises(ValueError, match=r"HTTP\(S\) scheme and authority"):
        normalize_public_origin(value)
