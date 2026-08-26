from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _requirements(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_connect_requirements_match_project_runtime_dependencies() -> None:
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert (
        _requirements(PROJECT_ROOT / "requirements.txt") == configuration["project"]["dependencies"]
    )


def test_connect_requirements_exclude_development_tools() -> None:
    requirements = _requirements(PROJECT_ROOT / "requirements.txt")

    assert not any(
        package in requirement.casefold()
        for requirement in requirements
        for package in ("basedpyright", "pytest", "ruff", "rsconnect")
    )


def test_project_requirements_do_not_set_upper_version_caps() -> None:
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    requirements = [
        *configuration["project"]["dependencies"],
        *configuration["project"]["optional-dependencies"]["dev"],
    ]

    for requirement in requirements:
        if requirement.startswith("hedron"):
            assert "<" in requirement
            continue
        assert "<" not in requirement


def test_workbench_compose_preserves_default_password_literal() -> None:
    compose = (PROJECT_ROOT / "docker/compose.workbench.yml").read_text()

    assert "PWB_TESTUSER_PASSWD: ${PWB_TESTUSER_PASSWD:-Xk9#mQ2$$vL8!nR4p}" in compose
