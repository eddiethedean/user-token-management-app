from __future__ import annotations

from hedron_posit.middleware import WorkbenchPathMiddleware
from hedron_posit.resolve import resolve_deployment

from app.server import _workbench_public_base_from_environment, run_server

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


def test_run_server_preserves_full_workbench_root_path_origin(monkeypatch) -> None:
    _clear_workbench_public_base_environment(monkeypatch)
    monkeypatch.setenv("RS_SERVER_URL", "http://127.0.0.1:8787/")
    monkeypatch.setenv("UVICORN_ROOT_PATH", _WORKBENCH_URL)
    captured = {}

    def fake_run_target(target, *, config) -> None:
        captured["config"] = config

    monkeypatch.setattr("app.server.run_target", fake_run_target)

    run_server(host="127.0.0.1", port=8000, reload=True)

    config = captured["config"]
    assert config.public_base_url == _WORKBENCH_URL
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
