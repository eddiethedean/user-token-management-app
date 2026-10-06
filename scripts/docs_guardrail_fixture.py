"""Isolated synthetic app for story screenshots; never connects to remote providers.

Run from the repository root with .venv/bin/python scripts/docs_guardrail_fixture.py.
The temporary database is removed when the server exits. See the screenshot README.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    with TemporaryDirectory(prefix="data-mover-story-capture-") as directory:
        os.environ.update(
            APP_ENV="test",
            DATA_MOVER_MODE="demo",
            PUBLIC_BASE_URL="http://127.0.0.1:8876",
            DATABASE_URL=f"sqlite:///{directory}/stories.db",
            JWT_SECRET="synthetic-story-capture-jwt-secret-32-bytes",
            SESSION_PEPPER="synthetic-story-capture-session-pepper-32",
            CSRF_SECRET="synthetic-story-capture-csrf-secret-32-bytes",
            API_TOKEN_ENCRYPTION_KEYS='{"development-v1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
            API_TOKEN_ACTIVE_KEY_ID="development-v1",
            AUTHENTICATION_MODE="local_password",
            COOKIE_SECURE="false",
            COOKIE_PATH="auto",
            ALLOWED_EMAIL_DOMAINS="example.gov",
            EMAIL_BACKEND="console",
            PASSWORD_HASH_SCHEME="pbkdf2_sha256",
            PBKDF2_ITERATIONS="100000",
            PIPELINE_BATCH_ROWS="1000",
            LOG_LEVEL="WARNING",
        )
        import uvicorn
        from sqlalchemy import select

        from app.cli import create_admin
        from app.config import get_settings
        from app.connectors.errors import ConnectorError, TransferErrorCode
        from app.connectors.fake import FakeFoundryConnector, FakePostgresConnector
        from app.database import SessionLocal
        from app.models import User
        from app.schema import upgrade_schema
        from app.services.demo import seed_demo_connections

        original_inspect = FakeFoundryConnector.inspect_object
        original_prepare = FakePostgresConnector.prepare_destination

        def markings(self, credentials, locator):
            self._validate(credentials)
            paths = getattr(locator, "file_paths", [])
            if paths == ["readiness_rollup.parquet"]:
                return ("restricted",), {}
            if paths == ["mission_orders.parquet"]:
                return (), {"unit_name": ("pii",), "score": ("sensitive",)}
            return (), {}

        def inspect(self, credentials, locator):
            schema = original_inspect(self, credentials, locator)
            table, columns = markings(self, credentials, locator)
            return replace(
                schema,
                sensitivity_markers=table,
                column_sensitivity_markers=tuple(columns.items()),
                columns=tuple(
                    replace(column, sensitivity_markers=columns.get(column.name, ()))
                    for column in schema.columns
                ),
            )

        def prepare(self, credentials, locator, schema, policy, *, run_id):
            if getattr(locator, "table", "") == "guardrail_failed":
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "Synthetic destination preparation failure.",
                    retryable=False,
                )
            return original_prepare(self, credentials, locator, schema, policy, run_id=run_id)

        # Inject markings through both emulator inspection paths, including live
        # cache refresh. Production connectors and cached payloads are untouched.
        FakeFoundryConnector.inspect_object = inspect
        FakeFoundryConnector.inspect_sensitivity_metadata = markings
        FakePostgresConnector.prepare_destination = prepare

        upgrade_schema()
        assert create_admin("docs@example.gov", "Quartz-Beacon-62!Harbor") == 0
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == "docs@example.gov"))
            assert user is not None
            seed_demo_connections(db, get_settings(), user=user)

        from app.main import app

        uvicorn.run(app, host="127.0.0.1", port=8876, log_level="warning")


if __name__ == "__main__":
    main()
