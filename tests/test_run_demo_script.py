from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_demo_with_discovery(tmp_path: Path, discovered_url: str) -> tuple[str, list[dict]]:
    calls_path = tmp_path / "python-calls.jsonl"
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
    }) + "\\n")
""",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rserver_url = fake_bin / "rserver-url"
    _executable(
        rserver_url,
        f"#!/usr/bin/env sh\nprintf '%s\\n' '{discovered_url}'\n",
    )

    env = os.environ.copy()
    for name in ("DEMO_PUBLIC_URL", "HEDRON_WORKBENCH_PUBLIC_BASE_URL"):
        env.pop(name, None)
    env.update(
        {
            "DEMO_TEST_CALLS": str(calls_path),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "PYTHON_BIN": str(fake_python),
        }
    )
    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "run-demo.sh")],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    return result.stdout, calls


def test_demo_launcher_uses_discovered_workbench_url_for_bound_port(tmp_path: Path) -> None:
    base = "https://workbench.example.mil/s/session-token/p/port-8765/"
    stdout, calls = _run_demo_with_discovery(tmp_path, base)

    expected = base.rstrip("/")
    assert f"Data Mover demo (open this Workbench URL): {expected}/login" in stdout
    assert calls[-1]["argv"] == [
        "-m",
        "app",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    ]
    assert calls[-1]["public_base_url"] == expected
    assert calls[-1]["workbench_public_base_url"] == expected
