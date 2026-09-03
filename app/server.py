"""HedronPosit-backed application launcher."""

from __future__ import annotations

from hedron_posit import WorkbenchConfig
from hedron_posit.runner import run_target


def run_server(*, host: str, port: int, reload: bool = False, discover: bool = False) -> None:
    """Run Data Mover through HedronPosit's supported launcher.

    ``run_target`` owns Workbench discovery, root-path normalization, environment
    handoff, cookie-path adaptation, and application wrapping. Keeping this
    adapter declarative avoids duplicating those rules in the application.
    """

    config = WorkbenchConfig(
        host=host,
        port=port,
        reload=reload,
        allow_external_bind=host not in {"127.0.0.1", "::1", "localhost"},
        app_target="app.main:app",
    )
    run_target("app.main:app", config=config, discover=discover)
