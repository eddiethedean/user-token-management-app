from __future__ import annotations

import pytest

from app.server import detect_root_path


def test_detect_root_path_accepts_workbench_absolute_uvicorn_root(monkeypatch) -> None:
    monkeypatch.setenv(
        "UVICORN_ROOT_PATH",
        "https://workbench.socom.mil/s/e886e3c9ab5a7de8990d1/p/679ea2ac/",
    )

    assert detect_root_path(8000) == "/s/e886e3c9ab5a7de8990d1/p/679ea2ac"


def test_detect_root_path_accepts_path_only_uvicorn_root(monkeypatch) -> None:
    monkeypatch.setenv("UVICORN_ROOT_PATH", "/s/session/p/8000")

    assert detect_root_path(8000) == "/s/session/p/8000"


def test_detect_root_path_is_empty_outside_workbench(monkeypatch) -> None:
    monkeypatch.delenv("UVICORN_ROOT_PATH", raising=False)
    monkeypatch.delenv("RS_SERVER_URL", raising=False)

    assert detect_root_path(8000) == ""


def test_detect_root_path_rejects_credentials_in_url(monkeypatch) -> None:
    monkeypatch.setenv(
        "UVICORN_ROOT_PATH",
        "https://user:pass@workbench.example/s/session/p/8000",
    )

    with pytest.raises(RuntimeError, match="Invalid ASGI root path"):
        detect_root_path(8000)
