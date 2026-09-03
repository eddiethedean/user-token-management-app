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


def test_workbench_migrate_loads_env_and_runs_both_schema_commands(
    tmp_path: Path, monkeypatch
) -> None:
    # Keep the parent environment in live mode while explicitly selecting demo
    # mode for the subprocess; ambient deployment variables must not leak in.
    monkeypatch.setenv("DATA_MOVER_MODE", "real")
    calls = tmp_path / "calls.jsonl"
    fake_python = tmp_path / "python"
    _executable(
        fake_python,
        """#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ['CALLS'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
""",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("# test environment\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        DATA_MOVER_ENV_FILE=str(env_file),
        PYTHON_BIN=str(fake_python),
        CALLS=str(calls),
        DATA_MOVER_MODE="demo",
    )

    subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "run-workbench.sh"), "migrate"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )

    assert calls.read_text(encoding="utf-8").splitlines() == [
        '["-m", "app", "migrate"]',
        '["-m", "app", "schema-status"]',
    ]


def test_workbench_web_uses_current_rserver_url(tmp_path: Path) -> None:
    call = tmp_path / "call.json"
    fake_python = tmp_path / "python"
    _executable(
        fake_python,
        """#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ['CALL'], 'w', encoding='utf-8') as stream:
    json.dump({
        'argv': sys.argv[1:],
        'public_base_url': os.environ.get('PUBLIC_BASE_URL'),
        'hedron_public_base_url': os.environ.get('HEDRON_WORKBENCH_PUBLIC_BASE_URL'),
        'uvicorn_root_path': os.environ.get('UVICORN_ROOT_PATH'),
    }, stream)
""",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "rserver-url",
        "#!/usr/bin/env sh\nprintf '%s\\n' 'https://workbench.example/s/session/p/8765/'\n",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=development\nDATA_MOVER_MODE=real\nPUBLIC_BASE_URL=http://127.0.0.1:8765\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        DATA_MOVER_ENV_FILE=str(env_file),
        PYTHON_BIN=str(fake_python),
        CALL=str(call),
        PATH=f"{fake_bin}{os.pathsep}{env['PATH']}",
        UVICORN_ROOT_PATH="https://workbench.example/s/old-session/p/8765",
    )

    subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "run-workbench.sh"), "web"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(call.read_text(encoding="utf-8"))
    assert result == {
        "argv": [
            "-m",
            "app",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--discover",
        ],
        "public_base_url": "https://workbench.example/s/session/p/8765",
        "hedron_public_base_url": "https://workbench.example/s/session/p/8765",
        "uvicorn_root_path": None,
    }


def test_workbench_rejects_external_worker_roles(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\nDATA_MOVER_MODE=demo\n", encoding="utf-8")
    fake_python = tmp_path / "python"
    _executable(fake_python, "#!/usr/bin/env sh\nexit 0\n")
    env = os.environ.copy()
    env.update(DATA_MOVER_ENV_FILE=str(env_file), PYTHON_BIN=str(fake_python))

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "run-workbench.sh"), "worker"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Usage:" in result.stderr
