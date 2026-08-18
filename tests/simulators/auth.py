"""Shared Bearer-token middleware for provider simulators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_OPENAPI_PATHS = {"/docs", "/openapi.json", "/redoc"}


class BearerTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str, unauthorized: dict):
        super().__init__(app)
        self.token = token
        self.unauthorized = unauthorized

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in _OPENAPI_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if header != f"Bearer {self.token}":
            body = self.unauthorized.get("body", {"error": "unauthorized"})
            status = int(self.unauthorized.get("status_code", 401))
            return JSONResponse(body, status_code=status)
        return await call_next(request)
