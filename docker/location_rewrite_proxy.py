#!/usr/bin/env python3
"""ASGI reverse proxy that reproduces Workbench's Location rewrite bug.

Path-absolute Locations (``/s/…/login``) get prefixed with ``/proxy/<port>``, which is
what SOCOM Workbench did to Access Registry redirects. Scheme-absolute Locations
(``https://…/s/…/login``) pass through unchanged.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

UPSTREAM = os.environ.get("REWRITE_UPSTREAM", "http://app:8000").rstrip("/")
PROXY_PREFIX = os.environ.get("REWRITE_PROXY_PREFIX", "/proxy/8000").rstrip("/")
LISTEN_HOST = os.environ.get("REWRITE_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("REWRITE_PORT", "8788"))


def _rewrite_location(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return candidate
    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.netloc:
        # Scheme-absolute — Workbench leaves these alone.
        return candidate
    if candidate.startswith(PROXY_PREFIX):
        return candidate
    if candidate.startswith("/"):
        return f"{PROXY_PREFIX}{candidate}"
    return candidate


async def proxy(request: Request) -> Response:
    target = f"{UPSTREAM}{request.url.path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.casefold() not in {"host", "content-length"}
    }
    body = await request.body()

    async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
        upstream = await client.request(
            request.method,
            target,
            headers=headers,
            content=body,
        )

    response_headers = []
    for key, value in upstream.headers.multi_items():
        lowered = key.casefold()
        if lowered in {"content-length", "transfer-encoding", "connection"}:
            continue
        if lowered == "location":
            value = _rewrite_location(value)
        response_headers.append((key, value))

    response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
    # Preserve every Set-Cookie; dict(headers) would collapse duplicates and drop
    # access/refresh tokens after login.
    for key, value in response_headers:
        if key.casefold() == "set-cookie":
            response.headers.append("set-cookie", value)
        else:
            response.headers[key] = value
    return response


app = Starlette(
    routes=[
        Route(
            "/{path:path}",
            endpoint=proxy,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        ),
        Route(
            "/",
            endpoint=proxy,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        ),
    ]
)


def main() -> None:
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="info")


if __name__ == "__main__":
    main()
