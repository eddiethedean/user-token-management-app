from __future__ import annotations

import tomllib
from pathlib import Path

from app import APP_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def test_calendar_release_versions_are_recorded_in_order() -> None:
    configuration = tomllib.loads(_read("pyproject.toml"))
    changelog = _read("CHANGELOG.md")

    assert configuration["project"]["version"] == APP_VERSION
    positions = [
        changelog.index(f"## [{version}]") for version in (APP_VERSION, "140926.1", "140926.0")
    ]
    assert positions == sorted(positions)
    assert "Product releases use `DDMMYY.X`" in changelog
    assert "Semantic Versioning" not in changelog


def test_current_docs_describe_capability_routes_and_repeatable_demo_seeding() -> None:
    maintained_docs = (
        "README.md",
        "DATA_MOVER_README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "docs/architecture.md",
        "docs/troubleshooting.md",
    )
    content = "\n".join(_read(name) for name in maintained_docs)

    assert "Route compatibility is\ncapability-driven" in content
    assert "recognized legacy demo bundles" in content
    assert "revalidates stale current demo bundles" in content
    assert "does not overwrite existing bundles by default" not in content
    assert "the exact source/destination pair is approved" not in content


def test_configuration_reference_lists_less_common_settings() -> None:
    configuration = _read("docs/configuration.md")

    for setting in (
        "APP_NAME",
        "DB_POOL_SIZE",
        "RATE_LIMIT_LOGIN_PER_SOURCE",
        "EMAIL_MAX_ATTEMPTS",
        "PASSWORD_HASH_SCHEME",
        "PIPELINE_MAX_RUN_SECONDS",
        "PIPELINE_RUN_RETENTION_DAYS",
    ):
        assert setting in configuration
