from __future__ import annotations

import subprocess
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def clean_launcher_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DEMO_HOST",
        "DEMO_PORT",
        "DEMO_ROOT_PATH",
        "RS_SERVER_URL",
        "RSERVER_URL_BIN",
        "UVICORN_ROOT_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/s/session/p/8000", "/s/session/p/8000"),
        ("/s/session/p/8000/\n", "/s/session/p/8000"),
        (
            "https://workbench.example.gov/s/session/p/8000",
            "/s/session/p/8000",
        ),
    ],
)
def test_root_path_parsing(demo_start_module: ModuleType, value: str, expected: str) -> None:
    assert demo_start_module._root_path_from_output(value) == expected


@pytest.mark.parametrize("value", ["", "relative/path", "//outside.example/path"])
def test_invalid_root_paths_are_rejected(demo_start_module: ModuleType, value: str) -> None:
    with pytest.raises(RuntimeError, match="invalid application path"):
        demo_start_module._root_path_from_output(value)


def test_local_environment_has_no_root_path(demo_start_module: ModuleType) -> None:
    assert demo_start_module.workbench_root_path(8000) == ("", "")


def test_explicit_demo_root_path_wins(
    demo_start_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_ROOT_PATH", "/manual/demo")
    monkeypatch.setenv("UVICORN_ROOT_PATH", "/workbench/default")
    monkeypatch.setenv("RS_SERVER_URL", "https://workbench.example.gov")

    assert demo_start_module.workbench_root_path(8000) == (
        "/manual/demo",
        "/manual/demo",
    )


def test_default_workbench_port_uses_uvicorn_root_path(
    demo_start_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UVICORN_ROOT_PATH", "/s/session/p/8000")

    assert demo_start_module.workbench_root_path(8000) == (
        "/s/session/p/8000",
        "/s/session/p/8000",
    )


def test_nondefault_port_asks_workbench_for_its_proxy_url(
    demo_start_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RS_SERVER_URL", "https://workbench.example.gov")
    monkeypatch.setenv("RSERVER_URL_BIN", "/opt/posit/bin/rserver-url")
    run = Mock(
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="https://workbench.example.gov/s/session/p/8050\n",
        )
    )
    monkeypatch.setattr(demo_start_module.subprocess, "run", run)

    assert demo_start_module.workbench_root_path(8050) == (
        "/s/session/p/8050",
        "https://workbench.example.gov/s/session/p/8050",
    )
    run.assert_called_once_with(
        ["/opt/posit/bin/rserver-url", "-l", "8050"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_workbench_lookup_failure_has_an_actionable_error(
    demo_start_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RS_SERVER_URL", "https://workbench.example.gov")
    monkeypatch.setattr(
        demo_start_module.subprocess,
        "run",
        Mock(side_effect=OSError("missing")),
    )

    with pytest.raises(RuntimeError, match="rserver-url could not determine"):
        demo_start_module.workbench_root_path(8050)


def test_main_passes_detected_settings_to_uvicorn(
    demo_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEMO_HOST", "127.0.0.2")
    monkeypatch.setenv("DEMO_PORT", "8050")
    monkeypatch.setattr(
        demo_start_module,
        "workbench_root_path",
        Mock(return_value=("/s/session/p/8050", "https://workbench.example/p/8050")),
    )
    run = Mock()
    monkeypatch.setattr(demo_start_module.uvicorn, "run", run)

    demo_start_module.main()

    run.assert_called_once_with(
        "app:app",
        host="127.0.0.2",
        port=8050,
        root_path="/s/session/p/8050",
    )
    assert "Posit Workbench URL: https://workbench.example/p/8050" in capsys.readouterr().out
