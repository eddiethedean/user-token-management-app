"""Serve a Semblance FastAPI app on loopback for connector HTTP clients."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI


def _bound_url(server: uvicorn.Server) -> str:
    for http_server in server.servers:
        for sock in http_server.sockets:
            host, port = sock.getsockname()[:2]
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            return f"http://{host}:{port}"
    raise RuntimeError("ASGI simulator did not bind a port.")


@contextmanager
def serve_asgi(app: FastAPI) -> Iterator[str]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("ASGI simulator thread exited before binding.")
        if time.monotonic() > deadline:
            raise RuntimeError("ASGI simulator failed to start.")
        time.sleep(0.01)
    try:
        yield _bound_url(server)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
