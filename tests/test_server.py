from subprocess import CompletedProcess

import pytest

from app.server import _normalize_root_path, detect_root_path, run_server


def test_root_path_is_empty_outside_workbench(monkeypatch) -> None:
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    monkeypatch.delenv("RS_SERVER_URL", raising=False)
    assert detect_root_path(8000) == ""


def test_explicit_workbench_root_path_wins(monkeypatch) -> None:
    monkeypatch.setenv("UVICORN_ROOT_PATH", "/s/session/p/8000/")
    monkeypatch.setenv("RS_SERVER_URL", "https://workbench.example")
    assert detect_root_path(8000) == "/s/session/p/8000"


def test_custom_workbench_port_uses_rserver_url(monkeypatch) -> None:
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    monkeypatch.setenv("RS_SERVER_URL", "https://workbench.example")

    def fake_run(command, **options):
        assert command[-2:] == ["-l", "8050"]
        assert "shell" not in options
        return CompletedProcess(command, 0, stdout="/s/session/p/8050\n", stderr="")

    monkeypatch.setattr("app.server.subprocess.run", fake_run)
    assert detect_root_path(8050) == "/s/session/p/8050"


def test_invalid_workbench_root_path_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("UVICORN_ROOT_PATH", "https://attacker.example/path")
    with pytest.raises(RuntimeError, match="Invalid ASGI root path"):
        detect_root_path(8000)


def test_root_slash_normalizes_to_empty() -> None:
    assert _normalize_root_path(" / ") == ""


def test_workbench_command_failure_has_actionable_error(monkeypatch) -> None:
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    monkeypatch.setenv("RS_SERVER_URL", "https://workbench.example")

    def fail(*args, **kwargs):
        raise OSError("missing binary")

    monkeypatch.setattr("app.server.subprocess.run", fail)
    with pytest.raises(RuntimeError, match="Set UVICORN_ROOT_PATH"):
        detect_root_path(8050)


def test_run_server_forwards_detected_root_path(monkeypatch) -> None:
    called = {}
    monkeypatch.setattr("app.server.detect_root_path", lambda port: f"/p/{port}")

    def fake_uvicorn_run(application, **options):
        called.update(application=application, **options)

    monkeypatch.setattr("app.server.uvicorn.run", fake_uvicorn_run)
    run_server(host="127.0.0.1", port=8050, reload=True)
    assert called == {
        "application": "app.main:app",
        "host": "127.0.0.1",
        "port": 8050,
        "reload": True,
        "root_path": "/p/8050",
    }
