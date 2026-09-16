from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DOC = PROJECT_ROOT / "docs" / "deploy.md"


def test_deployment_guide_has_only_workbench_and_connect_paths() -> None:
    deploy = DEPLOY_DOC.read_text(encoding="utf-8")

    assert "## Workbench" in deploy
    assert "## Connect" in deploy
    assert "## Operational Workbench deployment" not in deploy
    assert "SQLite Connect" not in deploy
    assert "connect-sqlite-demo.md" not in deploy


def test_connect_deployment_is_postgres_only() -> None:
    deploy = DEPLOY_DOC.read_text(encoding="utf-8")

    assert "Connect** for the persistent PostgreSQL-backed application" in deploy
    assert "PostgreSQL `DATABASE_URL`" in deploy
    assert "sqlite:///" not in deploy


def test_connect_deployment_uses_native_rsconnect_commands() -> None:
    deploy = DEPLOY_DOC.read_text(encoding="utf-8")

    assert "rsconnect add" in deploy
    assert "rsconnect deploy fastapi" in deploy
    assert "deploy-connect.sh" not in deploy
    assert "DATA_MOVER_SOURCE_DIR" not in deploy
    assert "DATA_MOVER_ENV_FILE" not in deploy
    assert "make demo" not in deploy
    assert "fake connectors" not in deploy
    assert "DATA_MOVER_MODE=real" in deploy
    assert "postgresql+psycopg://" in deploy
    assert "scripts/run-workbench.sh web" in deploy


def test_connect_deployment_has_direct_publish_safeguards() -> None:
    deploy = DEPLOY_DOC.read_text(encoding="utf-8")

    for setting in (
        "APP_ENV",
        "DATABASE_URL",
        "JWT_SECRET",
        "SESSION_PEPPER",
        "CSRF_SECRET",
        "API_TOKEN_ENCRYPTION_KEYS",
        "PIPELINE_SPOOL_ROOT",
        "PIPELINE_ALLOWED_HTTPS_HOSTS",
    ):
        assert f"-E {setting}" in deploy

    for excluded in (".env", ".venv", "**/*.db", "tests", "demo-app"):
        assert f"--exclude '{excluded}'" in deploy

    assert "deployment/spool/.keep" in deploy
    assert "No separate worker, email service, janitor, cookie proxy, or Nginx rule" in deploy
