"""HedronPosit-backed application launcher."""

from __future__ import annotations

from hedron_posit import WorkbenchConfig
from hedron_posit.runner import run_target


def run_server(*, host: str, port: int, reload: bool = False) -> None:
    """Discover the Posit deployment before importing and serving the app."""
    run_target(
        "app.main:app",
        config=WorkbenchConfig(
            host=host,
            port=port,
            reload=reload,
            allow_external_bind=host not in {"127.0.0.1", "::1", "localhost"},
            app_target="app.main:app",
        ),
    )
