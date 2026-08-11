from __future__ import annotations

import tomllib
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parents[1]


def test_project_targets_connect_python_version() -> None:
    configuration = tomllib.loads((DEMO_ROOT / "pyproject.toml").read_text())

    assert configuration["project"]["requires-python"] == ">=3.11,<3.12"


def test_runtime_requirements_do_not_include_development_tools() -> None:
    requirements = {
        line.strip()
        for line in (DEMO_ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert requirements == {"fastapi>=0.115,<1", "uvicorn[standard]>=0.30,<1"}
    assert not any(
        "pytest" in requirement or "httpx" in requirement for requirement in requirements
    )
