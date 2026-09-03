from __future__ import annotations

import os
import sys

import pytest
from hedron_core.diagnostics import HedronError
from hedron_posit import WorkbenchConfig, WorkbenchMode, export_hedron_state, resolve_deployment
from hedron_posit.urls import mounted_redirect

from app.server import run_server


def test_serve_cli_forwards_explicit_discovery(monkeypatch) -> None:
    import app.cli

    captured = {}

    def fake_run_server(*, host, port, reload, discover) -> None:
        captured.update(host=host, port=port, reload=reload, discover=discover)

    monkeypatch.setattr(app.cli, "run_server", fake_run_server)
    monkeypatch.setattr(
        sys,
        "argv",
        ["access-registry", "serve", "--host", "127.0.0.1", "--port", "8765", "--discover"],
    )

    app.cli.main()

    assert captured == {
        "host": "127.0.0.1",
        "port": 8765,
        "reload": False,
        "discover": True,
    }


def test_run_server_delegates_all_workbench_behavior_to_hedron_posit(monkeypatch) -> None:
    captured = {}

    def fake_run_target(target, *, config, discover=False) -> None:
        captured.update(target=target, config=config, discover=discover)

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8765, reload=True, discover=True)

    assert captured["target"] == "app.main:app"
    assert captured["discover"] is True
    config = captured["config"]
    assert isinstance(config, WorkbenchConfig)
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.reload is True
    assert config.allow_external_bind is False
    assert config.app_target == "app.main:app"


def test_run_server_drops_stale_runtime_root_for_explicit_handoff(monkeypatch) -> None:
    captured = {}
    monkeypatch.setenv(
        "HEDRON_WORKBENCH_PUBLIC_BASE_URL", "https://workbench.example/s/session/p/8765"
    )
    stale_root = "https://workbench.example/s/stale/p/8765"
    monkeypatch.setenv("UVICORN_ROOT_PATH", stale_root)

    def fake_run_target(target, *, config, discover=False) -> None:
        captured.update(target=target, config=config, discover=discover)
        captured["uvicorn_root_path"] = os.environ.get("UVICORN_ROOT_PATH")
        captured["resolved_mount"] = resolve_deployment(
            config, environ=os.environ, bound_port=8765
        ).browser_mount

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8765)

    assert captured["config"].mount is None
    assert captured["config"].public_base_url is None
    assert captured["discover"] is False
    assert captured["uvicorn_root_path"] is None
    assert captured["resolved_mount"] == "/s/session/p/8765"
    assert os.environ["UVICORN_ROOT_PATH"] == stale_root


def test_run_server_keeps_path_only_runtime_root(monkeypatch) -> None:
    captured = {}
    monkeypatch.setenv("HEDRON_WORKBENCH_PUBLIC_BASE_URL", "https://workbench.example")
    runtime_root = "/s/session/p/8765"
    monkeypatch.setenv("UVICORN_ROOT_PATH", runtime_root)

    def fake_run_target(target, *, config, discover=False) -> None:
        captured["uvicorn_root_path"] = os.environ.get("UVICORN_ROOT_PATH")
        captured["resolved_mount"] = resolve_deployment(
            config,
            environ={**os.environ, "RS_SERVER_URL": "http://127.0.0.1:8787/"},
            bound_port=8765,
        ).browser_mount

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8765)

    assert captured == {"uvicorn_root_path": runtime_root, "resolved_mount": runtime_root}


def test_hedron_resolves_full_workbench_url_and_exports_mount() -> None:
    config = WorkbenchConfig(host="127.0.0.1", port=8765, app_target="app.main:app")
    resolved = resolve_deployment(
        config,
        environ={
            "RS_SERVER_URL": "http://127.0.0.1:8787/",
            "UVICORN_ROOT_PATH": "https://workbench.example/s/session/p/8765/",
        },
        bound_port=8765,
    )

    assert resolved.active is True
    assert resolved.mode is WorkbenchMode.AUTO
    assert resolved.external_origin == "https://workbench.example"
    assert resolved.browser_mount == "/s/session/p/8765"

    environ = {}
    export_hedron_state(resolved, environ=environ)
    assert environ["HEDRON_ROOT_PATH"] == "/s/session/p/8765"
    assert environ["FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE"] == (
        "https://workbench.example/s/session/p/8765"
    )


def test_hedron_rejects_conflicting_explicit_and_runtime_mounts() -> None:
    with pytest.raises(HedronError, match="Conflicting Workbench mount and origin"):
        resolve_deployment(
            WorkbenchConfig(host="127.0.0.1", port=8765),
            environ={
                "RS_SERVER_URL": "http://127.0.0.1:8787/",
                "HEDRON_WORKBENCH_PUBLIC_BASE_URL": "https://workbench.example/s/session/p/new",
                "UVICORN_ROOT_PATH": "https://workbench.example/s/session/p/old",
            },
            bound_port=8765,
        )


def test_hedron_mounted_redirect_never_uses_relative_parent_depth() -> None:
    response = mounted_redirect("/pipeline", mount="/s/session/p/8765")

    assert response.status_code == 303
    assert response.headers["location"] == "/s/session/p/8765/pipeline"
