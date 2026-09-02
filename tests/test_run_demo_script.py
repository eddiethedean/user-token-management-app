from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_demo_with_discovery(
    tmp_path: Path,
    discovered_url: str,
    *,
    workbench_env: dict[str, str] | None = None,
    helper_on_path: bool = True,
) -> tuple[str, list[dict], bool]:
    calls_path = tmp_path / "python-calls.jsonl"
    discovery_calls_path = tmp_path / "rserver-url-called"
    fake_python = tmp_path / "fake-python"
    _executable(
        fake_python,
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["DEMO_TEST_CALLS"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps({
        "argv": sys.argv[1:],
        "public_base_url": os.environ.get("PUBLIC_BASE_URL"),
        "workbench_public_base_url": os.environ.get("HEDRON_WORKBENCH_PUBLIC_BASE_URL"),
        "uvicorn_root_path": os.environ.get("UVICORN_ROOT_PATH"),
        "hedron_workbench_mount": os.environ.get("HEDRON_WORKBENCH_MOUNT"),
        "fastapi_resolved_mount": os.environ.get("FASTAPI_WORKBENCH_RESOLVED_MOUNT"),
    }) + "\\n")
""",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rserver_url = fake_bin / "rserver-url"
    _executable(
        rserver_url,
        """#!/usr/bin/env sh
printf 'called\n' > "$DEMO_TEST_DISCOVERY_CALLS"
printf '%s\n' "$DEMO_TEST_DISCOVERED_URL"
""",
    )

    env = os.environ.copy()
    for name in (
        "DEMO_PUBLIC_URL",
        "HEDRON_WORKBENCH_PUBLIC_BASE_URL",
        "FASTAPI_WORKBENCH_PUBLIC_BASE_URL",
        "HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE",
        "FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE",
        "DEMO_RSERVER_URL_BIN",
    ):
        env.pop(name, None)
    env.update(
        {
            "DEMO_TEST_CALLS": str(calls_path),
            "DEMO_TEST_DISCOVERED_URL": discovered_url,
            "DEMO_TEST_DISCOVERY_CALLS": str(discovery_calls_path),
            "PYTHON_BIN": str(fake_python),
        }
    )
    if helper_on_path:
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    else:
        env["DEMO_RSERVER_URL_BIN"] = str(rserver_url)
    env.update(workbench_env or {})
    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "run-demo.sh")],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    return result.stdout, calls, discovery_calls_path.exists()


def test_demo_launcher_uses_discovered_workbench_url_for_bound_port(tmp_path: Path) -> None:
    base = "https://workbench.example.mil/s/session-token/p/port-8765/"
    stdout, calls, discovery_called = _run_demo_with_discovery(tmp_path, base)

    expected = base.rstrip("/")
    assert discovery_called is True
    assert f"Data Mover demo (open this Workbench URL): {expected}/login" in stdout
    assert calls[-1]["argv"] == [
        "-m",
        "app",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--discover",
    ]
    assert calls[-1]["public_base_url"] == expected
    assert calls[-1]["workbench_public_base_url"] == expected


def test_demo_launcher_uses_rserver_url_outside_path(tmp_path: Path) -> None:
    base = "https://workbench.example.mil/s/session-token/p/port-8765/"
    stdout, calls, discovery_called = _run_demo_with_discovery(
        tmp_path,
        base,
        helper_on_path=False,
    )

    expected = base.rstrip("/")
    assert discovery_called is True
    assert f"Data Mover demo (open this Workbench URL): {expected}/login" in stdout
    assert calls[-1]["argv"][-1] == "--discover"
    assert calls[-1]["workbench_public_base_url"] == expected


def test_demo_launcher_discards_stale_mount_after_rediscovery(tmp_path: Path) -> None:
    base = "https://workbench.example.mil/s/current-session/p/current-port/"
    stale = "https://workbench.example.mil/s/current-session/p/old-port/"
    _, calls, discovery_called = _run_demo_with_discovery(
        tmp_path,
        base,
        workbench_env={
            "RS_SERVER_URL": "http://127.0.0.1:8787/",
            "UVICORN_ROOT_PATH": stale,
            "HEDRON_WORKBENCH_MOUNT": "/s/current-session/p/old-port",
            "FASTAPI_WORKBENCH_RESOLVED_MOUNT": "/s/current-session/p/older-port",
        },
    )

    assert discovery_called is True
    assert calls[-1]["workbench_public_base_url"] == base.rstrip("/")
    assert calls[-1]["uvicorn_root_path"] is None
    assert calls[-1]["hedron_workbench_mount"] is None
    assert calls[-1]["fastapi_resolved_mount"] is None


@pytest.mark.parametrize(
    "env_name",
    (
        "HEDRON_WORKBENCH_PUBLIC_BASE_URL",
        "FASTAPI_WORKBENCH_PUBLIC_BASE_URL",
        "HEDRON_WORKBENCH_RESOLVED_PUBLIC_BASE",
        "FASTAPI_WORKBENCH_RESOLVED_PUBLIC_BASE",
    ),
)
def test_demo_launcher_prefers_upstream_workbench_handoff(tmp_path: Path, env_name: str) -> None:
    upstream = "https://workbench.example.mil/s/upstream-session/p/port-8765/"
    stdout, calls, discovery_called = _run_demo_with_discovery(
        tmp_path,
        "https://wrong.example.mil/s/fallback/p/port-8765/",
        workbench_env={env_name: upstream},
    )

    expected = upstream.rstrip("/")
    assert discovery_called is False
    assert f"Data Mover demo (open this Workbench URL): {expected}/login" in stdout
    assert calls[-1]["public_base_url"] == expected
    assert calls[-1]["workbench_public_base_url"] == expected
