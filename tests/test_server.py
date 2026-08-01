from subprocess import CompletedProcess

import pytest

from app.server import detect_root_path


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
