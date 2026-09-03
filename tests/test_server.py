from __future__ import annotations

import os
import sys

import pytest
from hedron_core.diagnostics import HedronError
from hedron_posit.middleware import WorkbenchPathMiddleware
from hedron_posit.resolve import resolve_deployment
from starlette._utils import get_route_path

from app.server import (
    _has_workbench_handoff,
    _prepare_workbench_environment,
    _workbench_public_base_from_environment,
    run_server,
)

_WORKBENCH_URL = "https://workbench.example.mil/s/session-token/p/proxy-token/"


def _clear_workbench_public_base_environment(monkeypatch) -> None:
    for name in (
        "HEDRON_WORKBENCH_PUBLIC_BASE_URL",
        "FASTAPI_WORKBENCH_PUBLIC_BASE_URL",
        "HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE",
        "FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE",
        "PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


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


def test_run_server_delegates_discovery_and_serving_to_hedron_posit(monkeypatch) -> None:
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    _clear_workbench_public_base_environment(monkeypatch)
    captured = {}

    def fake_run_target(target, *, config) -> None:
        captured["target"] = target
        captured["config"] = config

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8765, reload=True)

    assert captured["target"] == "app.main:app"
    config = captured["config"]
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.reload is True
    assert config.allow_external_bind is False
    assert config.public_base_url is None


def test_run_server_passes_explicit_discovery_to_supporting_hedron(monkeypatch) -> None:
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    _clear_workbench_public_base_environment(monkeypatch)
    captured = {}

    def fake_run_target(target, *, config, discover=False) -> None:
        captured["target"] = target
        captured["config"] = config
        captured["discover"] = discover

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8765, discover=True)

    assert captured["target"] == "app.main:app"
    assert captured["config"].port == 8765
    assert captured["discover"] is True


def test_run_server_keeps_current_hedron_fallback_with_explicit_handoff(monkeypatch) -> None:
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    _clear_workbench_public_base_environment(monkeypatch)
    monkeypatch.setenv("HEDRON_WORKBENCH_PUBLIC_BASE_URL", _WORKBENCH_URL)
    captured = {}

    def current_run_target(target, *, config) -> None:
        captured["target"] = target
        captured["config"] = config

    monkeypatch.setattr("app.server.run_target", current_run_target)

    run_server(host="127.0.0.1", port=8765, discover=True)

    assert captured["target"] == "app.main:app"
    assert captured["config"].port == 8765
    assert _has_workbench_handoff() is True


def test_run_server_rejects_unavailable_explicit_discovery_without_fallback(
    monkeypatch,
) -> None:
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    _clear_workbench_public_base_environment(monkeypatch)

    def current_run_target(target, *, config) -> None:
        raise AssertionError("runner must not start without discovery or a handoff")

    monkeypatch.setattr("app.server.run_target", current_run_target)

    with pytest.raises(RuntimeError, match="Explicit Workbench discovery requires"):
        run_server(host="127.0.0.1", port=8765, discover=True)


def test_run_server_preserves_full_workbench_root_path_origin(monkeypatch) -> None:
    _clear_workbench_public_base_environment(monkeypatch)
    monkeypatch.setenv("RS_SERVER_URL", "http://127.0.0.1:8787/")
    monkeypatch.setenv("UVICORN_ROOT_PATH", _WORKBENCH_URL)
    captured = {}

    def fake_run_target(target, *, config) -> None:
        captured["config"] = config
        captured["uvicorn_root_path"] = os.environ.get("UVICORN_ROOT_PATH")

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8000, reload=True)

    config = captured["config"]
    assert config.public_base_url == _WORKBENCH_URL
    assert config.mount == "/s/session-token/p/proxy-token"
    assert captured["uvicorn_root_path"] is None
    resolved = resolve_deployment(
        config,
        environ={
            "RS_SERVER_URL": "http://127.0.0.1:8787/",
            "UVICORN_ROOT_PATH": _WORKBENCH_URL,
        },
        bound_port=8000,
    )
    assert resolved.active is True
    assert resolved.external_origin == "https://workbench.example.mil"
    assert resolved.browser_mount == "/s/session-token/p/proxy-token"

    async def downstream(scope, receive, send) -> None:
        return None

    middleware = WorkbenchPathMiddleware(
        downstream,
        expected_mount=resolved.browser_mount,
        expected_origins=(resolved.external_origin,),
    )
    normalized = middleware.normalize_scope(
        {
            "type": "http",
            "path": ("https%3A//workbench.example.mil/s/session-token/p/proxy-token//"),
            "root_path": "",
            "raw_path": b"",
            "query_string": b"",
        }
    )
    assert normalized["path"] == "/s/session-token/p/proxy-token/"
    assert normalized["root_path"] == "/s/session-token/p/proxy-token"
    assert get_route_path(normalized) == "/"


def test_explicit_public_base_drops_stale_runtime_mount(monkeypatch) -> None:
    _clear_workbench_public_base_environment(monkeypatch)
    monkeypatch.setenv("RS_SERVER_URL", "http://127.0.0.1:8787/")
    monkeypatch.setenv(
        "UVICORN_ROOT_PATH",
        "https://workbench.example.mil/s/session-token/p/old-proxy-token/",
    )
    monkeypatch.setenv("HEDRON_WORKBENCH_PUBLIC_BASE_URL", _WORKBENCH_URL)
    captured = {}

    def fake_run_target(target, *, config) -> None:
        captured["target"] = target
        captured["config"] = config
        captured["uvicorn_root_path"] = os.environ.get("UVICORN_ROOT_PATH")

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8765)

    assert captured["target"] == "app.main:app"
    assert captured["config"].mount is None
    assert captured["config"].public_base_url is None
    assert captured["uvicorn_root_path"] is None
    resolved = resolve_deployment(captured["config"], environ=os.environ, bound_port=8765)
    assert resolved.browser_mount == "/s/session-token/p/proxy-token"


def test_workbench_root_path_promotion_requires_runtime_and_full_url(monkeypatch) -> None:
    _clear_workbench_public_base_environment(monkeypatch)
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    monkeypatch.setenv("UVICORN_ROOT_PATH", _WORKBENCH_URL)
    assert _workbench_public_base_from_environment() is None

    monkeypatch.setenv("RS_SERVER_URL", "http://127.0.0.1:8787/")
    monkeypatch.setenv("UVICORN_ROOT_PATH", "/s/session-token/p/proxy-token/")
    assert _workbench_public_base_from_environment() is None


def test_explicit_workbench_public_base_takes_precedence(monkeypatch) -> None:
    _clear_workbench_public_base_environment(monkeypatch)
    monkeypatch.setenv("RS_SERVER_URL", "http://127.0.0.1:8787/")
    monkeypatch.setenv("UVICORN_ROOT_PATH", _WORKBENCH_URL)
    monkeypatch.setenv(
        "HEDRON_WORKBENCH_PUBLIC_BASE_URL",
        "https://configured.example.mil/custom-mount/",
    )

    assert _workbench_public_base_from_environment() is None


def test_upstream_resolved_public_base_activates_without_app_override(monkeypatch) -> None:
    _clear_workbench_public_base_environment(monkeypatch)
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    monkeypatch.setenv("HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE", _WORKBENCH_URL)
    captured = {}

    def fake_run_target(target, *, config) -> None:
        captured["target"] = target
        captured["config"] = config

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8765)

    config = captured["config"]
    resolved = resolve_deployment(config, environ=os.environ, bound_port=8765)
    assert captured["target"] == "app.main:app"
    assert config.public_base_url is None
    assert config.mount is None
    assert resolved.active is True
    assert resolved.external_origin == "https://workbench.example.mil"
    assert resolved.browser_mount == "/s/session-token/p/proxy-token"


def test_workbench_root_path_is_validated_before_uvicorn_handoff() -> None:
    environ = {
        "RS_SERVER_URL": "http://127.0.0.1:8787/",
        "UVICORN_ROOT_PATH": f"{_WORKBENCH_URL}?unexpected=query",
    }

    with pytest.raises(HedronError, match="Unsafe Workbench public base URL"):
        _prepare_workbench_environment(environ)

    assert environ["UVICORN_ROOT_PATH"].endswith("?unexpected=query")
