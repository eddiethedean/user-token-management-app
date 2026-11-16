"""Read-only administrator checks against the live deployment and connectors."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import APP_VERSION
from app.application.ports import RequestMetadata
from app.config import Settings
from app.connectors.base import ConnectionHealth, ConnectionTester, Credentials
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.registry import connection_tester_for
from app.database import current_session_factory
from app.db_compat import execute_dml
from app.logging_config import log_event
from app.models import LiveDiagnosticRun, User, UserSecret, utcnow
from app.schema import head_revision
from app.services.catalogs import UserCatalog
from app.services.diagnostic_guards import (
    DiagnosticAdministratorUnavailable,
    DiagnosticCredentialsStale,
)
from app.services.secrets import (
    ConnectionNotConfiguredError,
    SecretStorageError,
    decrypt_user_credentials_for_run_with_revision,
)

PROVIDER_LABELS = {
    "mss": "MSS",
    "mcscop": "MCS-COP",
    "postgres": "PostgreSQL",
    "databricks": "Databricks",
}
CATALOG_LABELS = {
    **PROVIDER_LABELS,
    "databricks": "Databricks SQL endpoint and catalog",
}
HISTORY_LIMIT = 20
RUN_LEASE_SECONDS = 300
QUEUED_RUN_TIMEOUT = timedelta(minutes=5)
_REPORT_LABELS = {
    "deployment.database": "Application database",
    "deployment.spool": "Private transfer storage",
    "deployment.schema": "Database schema",
    "mss.connection": "MSS connection",
    "mss.catalog": "MSS catalog access",
    "mcscop.connection": "MCS-COP connection",
    "mcscop.catalog": "MCS-COP catalog access",
    "postgres.connection": "PostgreSQL connection",
    "postgres.catalog": "PostgreSQL catalog access",
    "databricks.connection": "Databricks management API and compute visibility",
    "databricks.compute": "Databricks compute readiness",
    "databricks.catalog": "Databricks SQL endpoint and catalog access",
}
_REPORT_STATUSES = frozenset(
    {
        "pending",
        "running",
        "passed",
        "succeeded",
        "queued",
        "missing",
        "disabled",
        "blocked",
        "failed",
        "completed_with_failures",
        "completed_with_gaps",
        "interrupted",
        "skipped",
    }
)
_REPORT_CODES = frozenset(
    {
        "administrator_unavailable",
        "authentication_failed",
        "catalog_available",
        "check_failed",
        "compute_not_ready",
        "compute_ready",
        "connection_available",
        "connection_check_failed",
        "connection_check_required",
        "connection_test_incomplete",
        "credential_check_failed",
        "credentials_not_configured",
        "credentials_stale",
        "database_accessible",
        "database_timeout",
        "database_unavailable",
        "databricks_sql_catalog_available",
        "default_dataset_not_configured",
        "diagnostic_not_run",
        "diagnostic_worker_failed",
        "diagnostic_interrupted",
        "endpoint_blocked",
        "internal_error",
        "management_api_available",
        "permission_denied",
        "provider_disabled",
        "provider_unavailable",
        "rate_limited",
        "schema_current",
        "schema_not_current",
        "schema_revision_timeout",
        "schema_revision_unavailable",
        "source_not_found",
        "spool_available",
        "spool_cleanup_unconfirmed",
        "spool_not_configured",
        "spool_timeout",
        "spool_unavailable",
        "sql_operations_disabled",
        "tls_failed",
        "connection_timeout",
        "connection_authentication_failed",
        "connection_permission_denied",
        "connection_endpoint_blocked",
        "connection_tls_failed",
        "connection_provider_unavailable",
        "connection_invalid",
        "connection_unsupported",
        "connection_test_succeeded",
    }
) | frozenset(code.value for code in TransferErrorCode)
_REPORT_TOKEN = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")
_DIAGNOSTIC_DATABASE_TIMEOUT_SECONDS = 5
_DIAGNOSTIC_DATABASE_TIMEOUT_MS = _DIAGNOSTIC_DATABASE_TIMEOUT_SECONDS * 1_000
_DIAGNOSTIC_SPOOL_TIMEOUT_SECONDS = 5
_DIAGNOSTIC_SPOOL_CLEANUP_TIMEOUT_SECONDS = 2
_DATABASE_PROBE_SCRIPT = r"""
import json
import sys

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

request = json.load(sys.stdin)
url = make_url(request["url"])
dialect = url.get_backend_name()
connect_args = {}
if dialect == "postgresql":
    connect_args["connect_timeout"] = 3
elif dialect == "sqlite":
    connect_args["timeout"] = 5
engine = create_engine(
    url,
    connect_args=connect_args,
    poolclass=NullPool,
    pool_pre_ping=False,
)
try:
    try:
        with engine.connect() as connection:
            if dialect == "sqlite":
                connection.exec_driver_sql("PRAGMA busy_timeout = 5000")
            elif dialect == "postgresql":
                connection.exec_driver_sql("SET statement_timeout = '5000ms'")
                connection.exec_driver_sql("SET lock_timeout = '5000ms'")
            if request["operation"] == "database":
                connection.exec_driver_sql("SELECT 1").scalar_one()
                result = {"ok": True}
            else:
                revisions = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalars().all()
                result = {
                    "ok": True,
                    "revision": revisions[0] if len(revisions) == 1 else None,
                }
    except Exception as exc:
        original = getattr(exc, "orig", exc)
        sqlstate = getattr(original, "sqlstate", "") or getattr(original, "pgcode", "")
        message = str(original).casefold()
        timed_out = sqlstate in {"57014", "55P03"} or any(
            marker in message
            for marker in ("timeout", "timed out", "database is locked", "database is busy")
        )
        result = {"ok": False, "timeout": timed_out}
    print(json.dumps(result, separators=(",", ":")))
finally:
    engine.dispose()
"""
_SPOOL_PROBE_SCRIPT = r"""
import json
import sys
from pathlib import Path

request = json.load(sys.stdin)
path = Path(request["root"]) / request["name"]
payload = b"data-mover-diagnostic"
if request["operation"] == "cleanup":
    try:
        path.unlink(missing_ok=True)
        result = {"ok": not path.exists()}
    except OSError:
        result = {"ok": False}
else:
    cleanup_ok = True
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
        with path.open("rb") as stream:
            matches = stream.read() == payload
        result = {"ok": matches, "code": "spool_available" if matches else "spool_unavailable"}
    except OSError:
        result = {"ok": False, "code": "spool_unavailable"}
    finally:
        try:
            path.unlink(missing_ok=True)
            cleanup_ok = not path.exists()
        except OSError:
            cleanup_ok = False
    result["cleanup_ok"] = cleanup_ok
print(json.dumps(result, separators=(",", ":")))
"""
log = logging.getLogger(__name__)


class _DatabaseProbeTimeout(Exception):
    pass


class _DatabaseProbeUnavailable(Exception):
    pass


class _SpoolProbeUnavailable(Exception):
    pass


class _SpoolProbeLaunchFailure(_SpoolProbeUnavailable):
    pass


class _FoundryDiagnosticConnectionTester:
    def __init__(self, connector: ConnectionTester) -> None:
        self.connector = connector
        self.capabilities = connector.capabilities

    def test_connection(self, credentials: Credentials) -> ConnectionHealth:
        test = cast(
            Callable[[Credentials], ConnectionHealth] | None,
            getattr(self.connector, "test_diagnostic_connection", None),
        )
        if not callable(test):
            raise ConnectorError(
                TransferErrorCode.INTERNAL_ERROR,
                "The Foundry adapter does not support the diagnostic connection check.",
                retryable=False,
            )
        return test(credentials)


def _diagnostic_connection_tester_for(provider: str) -> ConnectionTester:
    connector = connection_tester_for(provider)
    if provider.casefold() in {"mss", "mcscop"}:
        return _FoundryDiagnosticConnectionTester(connector)
    return connector


def _test_diagnostic_connection(
    db: Session,
    *,
    run: LiveDiagnosticRun,
    user: User,
    provider: str,
    settings: Settings,
    expected_identity: tuple[str, datetime],
) -> ConnectionHealth:
    """Probe one provider without changing the saved connection's health state."""
    if _active_administrator(db, user.id) is None:
        raise DiagnosticAdministratorUnavailable()
    try:
        credentials, secret_id, revision = decrypt_user_credentials_for_run_with_revision(
            db,
            settings,
            user=user,
            provider=provider,
            request=RequestMetadata(reference_id=run.id),
            purpose="diagnostic_connection",
        )
    except ConnectionNotConfiguredError as exc:
        raise DiagnosticCredentialsStale() from exc
    if (secret_id, revision) != expected_identity:
        raise DiagnosticCredentialsStale()

    if _active_administrator(db, user.id) is None:
        raise DiagnosticAdministratorUnavailable()
    if _credential_identity(db, user.id, provider) != expected_identity:
        raise DiagnosticCredentialsStale()

    health: ConnectionHealth | None = None
    provider_error: Exception | None = None
    try:
        health = _diagnostic_connection_tester_for(provider).test_connection(credentials)
    except Exception as exc:
        provider_error = exc

    if _active_administrator(db, user.id) is None:
        raise DiagnosticAdministratorUnavailable()
    if _credential_identity(db, user.id, provider) != expected_identity:
        raise DiagnosticCredentialsStale()
    if provider_error is not None:
        raise provider_error
    if health is None:
        raise ConnectorError(
            TransferErrorCode.PROVIDER_UNAVAILABLE,
            "The provider returned no connection result.",
        )
    return health


def create_diagnostic_run(db: Session, *, user: User, settings: Settings) -> LiveDiagnosticRun:
    reconcile_stale_diagnostic_runs(db, user_id=user.id)
    active = active_diagnostic_run(db, user_id=user.id)
    if active is not None:
        return active
    planned_checks = [
        {"key": "deployment.database", "label": "Application database", "status": "pending"},
        {"key": "deployment.spool", "label": "Private transfer storage", "status": "pending"},
        {"key": "deployment.schema", "label": "Database schema", "status": "pending"},
    ]
    for provider in PROVIDER_LABELS:
        enabled = provider != "databricks" or settings.pipeline_enable_databricks
        connection_label = (
            "Databricks management API and compute visibility"
            if provider == "databricks"
            else f"{PROVIDER_LABELS[provider]} connection"
        )
        planned_checks.append(
            {
                "key": f"{provider}.connection",
                "label": connection_label,
                "status": "pending" if enabled else "disabled",
                **({} if enabled else {"code": "provider_disabled"}),
            }
        )
        if provider == "databricks":
            planned_checks.append(
                {
                    "key": "databricks.compute",
                    "label": "Databricks compute readiness",
                    "status": "pending" if enabled else "disabled",
                    **({} if enabled else {"code": "provider_disabled"}),
                }
            )
        planned_checks.append(
            {
                "key": f"{provider}.catalog",
                "label": f"{CATALOG_LABELS[provider]} access",
                "status": "pending" if enabled else "disabled",
                **({} if enabled else {"code": "provider_disabled"}),
            }
        )
    run = LiveDiagnosticRun(
        user_id=user.id,
        app_version=APP_VERSION,
        status="queued",
        mode="demo" if settings.is_demo_mode else "live",
        checks_json=json.dumps(planned_checks, separators=(",", ":")),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = active_diagnostic_run(db, user_id=user.id)
        if active is None:
            raise
        return active
    db.refresh(run)
    return run


def list_diagnostic_runs(db: Session, *, user_id: str) -> list[LiveDiagnosticRun]:
    return list(
        db.scalars(
            select(LiveDiagnosticRun)
            .where(LiveDiagnosticRun.user_id == user_id)
            .order_by(LiveDiagnosticRun.created_at.desc())
            .limit(HISTORY_LIMIT)
        ).all()
    )


def active_diagnostic_run(db: Session, *, user_id: str) -> LiveDiagnosticRun | None:
    return db.scalar(
        select(LiveDiagnosticRun)
        .where(
            LiveDiagnosticRun.user_id == user_id,
            LiveDiagnosticRun.status.in_(("queued", "running")),
        )
        .order_by(LiveDiagnosticRun.created_at.desc())
        .limit(1)
    )


def reconcile_stale_diagnostic_runs(db: Session, *, user_id: str) -> None:
    """Finalize work abandoned before its worker finished or claimed its lease."""
    now = utcnow()
    runs = db.scalars(
        select(LiveDiagnosticRun).where(
            LiveDiagnosticRun.user_id == user_id,
            LiveDiagnosticRun.status.in_(("queued", "running")),
        )
    ).all()
    for run in runs:
        queued_timed_out = (
            run.status == "queued" and run.created_at <= now - QUEUED_RUN_TIMEOUT
        )
        lease_expired = (
            run.status == "running"
            and run.lease_expires_at is not None
            and run.lease_expires_at <= now
        )
        if not queued_timed_out and not lease_expired:
            continue

        checks = json.loads(run.checks_json)
        for item in checks:
            if item.get("status") in {"pending", "running"}:
                item.update(
                    status="skipped",
                    code="diagnostic_interrupted",
                    completed_at=now.replace(tzinfo=UTC).isoformat(),
                )
        condition = [
            LiveDiagnosticRun.id == run.id,
            LiveDiagnosticRun.user_id == user_id,
            LiveDiagnosticRun.status == run.status,
            LiveDiagnosticRun.checks_json == run.checks_json,
        ]
        if run.status == "queued":
            condition.extend(
                [LiveDiagnosticRun.created_at == run.created_at]
            )
        else:
            condition.extend(
                [
                    LiveDiagnosticRun.worker_token == run.worker_token,
                    LiveDiagnosticRun.lease_expires_at == run.lease_expires_at,
                ]
            )
        result = execute_dml(
            db,
            update(LiveDiagnosticRun)
            .where(*condition)
            .values(
                checks_json=json.dumps(checks, separators=(",", ":")),
                status="interrupted",
                worker_token=None,
                lease_expires_at=None,
                finished_at=now,
            )
        )
        if result.rowcount:
            db.commit()
        else:
            db.rollback()


def owned_diagnostic_run(db: Session, *, run_id: str, user_id: str) -> LiveDiagnosticRun:
    run = db.scalar(
        select(LiveDiagnosticRun).where(
            LiveDiagnosticRun.id == run_id,
            LiveDiagnosticRun.user_id == user_id,
        )
    )
    if run is None:
        raise LookupError("Diagnostic run not found.")
    return run


def format_readiness_report(run: LiveDiagnosticRun) -> str:
    """Build a copyable report from fixed diagnostic fields and safe result codes."""
    run_reference = _safe_report_token(run.id)
    app_version = _safe_report_token(run.app_version or "unknown")
    mode = run.mode if run.mode in {"live", "demo"} else "unknown"
    status = diagnostic_run_status(run)
    status = status if status in _REPORT_STATUSES else "unknown"
    lines = [
        "Data Mover deployment readiness report",
        f"Run reference: {run_reference}",
        f"Application version: {app_version}",
        f"Mode: {mode}",
        f"Run status: {status}",
        f"Started: {_report_timestamp(run.started_at)}",
        f"Finished: {_report_timestamp(run.finished_at)}",
        "",
        "Checks:",
    ]
    checks = diagnostic_checks_for_display(run)
    counts = _diagnostic_check_counts(checks)
    result_counts = ", ".join(
        f"{count} {name}" for name, count in counts.items() if count
    )
    lines.insert(7, f"Results: {result_counts or 'no checks recorded'}")
    for item in checks:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        label = _REPORT_LABELS.get(key) if isinstance(key, str) else None
        if label is None:
            continue
        check_status = item.get("status")
        if not isinstance(check_status, str) or check_status not in _REPORT_STATUSES:
            check_status = "unknown"
        code = item.get("code")
        safe_code = code if isinstance(code, str) and code in _REPORT_CODES else "check_failed"
        lines.append(f"- {label}: {check_status} (code: {safe_code})")
        if key == "deployment.schema":
            details = item.get("details")
            if isinstance(details, dict):
                current = _safe_report_revision(details.get("current_revision"))
                expected = _safe_report_revision(details.get("expected_revision"))
                lines.append(f"  Database revision: {current}; expected: {expected}")
    return "\n".join(lines) + "\n"


def _safe_report_token(value: object) -> str:
    token = str(value)
    return token if _REPORT_TOKEN.fullmatch(token) else "unavailable"


def _safe_report_revision(value: object) -> str:
    if not isinstance(value, str) or not _REPORT_TOKEN.fullmatch(value):
        return "unavailable"
    return value


def _diagnostic_check_counts(checks: list[object]) -> dict[str, int]:
    counts = {
        "pending": 0,
        "running": 0,
        "passed": 0,
        "failed": 0,
        "missing": 0,
        "blocked": 0,
        "skipped": 0,
        "disabled": 0,
    }
    for item in checks:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if isinstance(status, str) and status in counts:
            counts[status] += 1
    return counts


def diagnostic_check_counts(
    checks_json: str,
    *,
    run_status: str | None = None,
) -> dict[str, int]:
    try:
        checks = json.loads(checks_json)
    except (TypeError, ValueError):
        checks = []
    parsed = checks if isinstance(checks, list) else []
    return _diagnostic_check_counts(_normalize_legacy_checks(parsed, run_status))


def diagnostic_checks_for_display(run: LiveDiagnosticRun) -> list[object]:
    try:
        checks = json.loads(run.checks_json)
    except (TypeError, ValueError):
        checks = []
    parsed = checks if isinstance(checks, list) else []
    return _normalize_legacy_checks(parsed, run.status)


def _normalize_legacy_checks(checks: list[object], run_status: str | None) -> list[object]:
    if run_status != "completed_with_failures":
        return checks
    normalized: list[object] = []
    for item in checks:
        if (
            isinstance(item, dict)
            and item.get("code") == "default_dataset_not_configured"
            and item.get("status") == "failed"
        ):
            item = {
                **item,
                "status": "missing" if str(item.get("key", "")).endswith(".connection") else "blocked",
            }
        normalized.append(item)
    return normalized


def diagnostic_run_status(run: LiveDiagnosticRun) -> str:
    """Return the result status, correcting older runs that treated gaps as failures."""
    if run.status != "completed_with_failures":
        return run.status
    try:
        checks = diagnostic_checks_for_display(run)
    except (TypeError, ValueError):
        return run.status
    if not isinstance(checks, list):
        return run.status

    items = [item for item in checks if isinstance(item, dict)]
    if any(
        item.get("status") == "failed"
        or item.get("code") in {"diagnostic_worker_failed", "diagnostic_not_run"}
        for item in items
    ):
        if any(item.get("code") in {"diagnostic_worker_failed", "diagnostic_not_run"} for item in items):
            return run.status
        if any(item.get("status") == "failed" for item in items):
            return run.status
    if any(item.get("status") in {"missing", "blocked", "skipped"} for item in items):
        return "completed_with_gaps"
    return run.status


def _diagnostic_run_status(checks: list[dict[str, object]]) -> str:
    if any(item.get("status") == "failed" for item in checks):
        return "completed_with_failures"
    if checks and all(item.get("status") in {"passed", "disabled"} for item in checks):
        return "succeeded"
    return "completed_with_gaps"


def _report_timestamp(value: datetime | None) -> str:
    if value is None:
        return "not available"
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat(timespec="seconds")


async def execute_diagnostic_run(runtime, run_id: str) -> None:
    """Run checks in a runtime-owned worker and persist each completed result."""
    try:
        await runtime.run_owned_sync(_execute_diagnostic_checks, run_id, runtime.execution_settings)
    except Exception as exc:
        log.error("Unable to execute diagnostic run %s: %s", run_id, type(exc).__name__)


def _execute_diagnostic_checks(run_id: str, settings: Settings) -> None:
    worker_token = _claim_diagnostic_run(run_id)
    if worker_token is None:
        return
    try:
        _run_diagnostic_checks(run_id, settings, worker_token)
    except DiagnosticLeaseLost:
        return
    except Exception as exc:
        _record_unexpected_failure(run_id, worker_token, exc)


def _claim_diagnostic_run(run_id: str) -> str | None:
    now = utcnow()
    worker_token = str(uuid.uuid4())
    with current_session_factory()() as db:
        result = execute_dml(
            db,
            update(LiveDiagnosticRun)
            .where(
                LiveDiagnosticRun.id == run_id,
                LiveDiagnosticRun.status == "queued",
            )
            .values(
                status="running",
                started_at=now,
                worker_token=worker_token,
                lease_expires_at=now + timedelta(seconds=RUN_LEASE_SECONDS),
            )
        )
        db.commit()
        return worker_token if result.rowcount == 1 else None


class DiagnosticLeaseLost(RuntimeError):
    """The worker no longer owns the diagnostic run's current lease."""


def _run_diagnostic_checks(run_id: str, settings: Settings, worker_token: str) -> None:
    with current_session_factory()() as db:
        run = db.get(LiveDiagnosticRun, run_id)
        if (
            run is None
            or run.status != "running"
            or run.worker_token != worker_token
        ):
            return
        user = _active_administrator(db, run.user_id)
        if user is None:
            _block_pending_checks(db, run, "administrator_unavailable")
            _finish_run(db, run, "failed")
            return
        try:
            _start_check(db, run, "deployment.database")
            _run_database_probe(db, "database")
        except Exception as exc:
            db.rollback()
            code = _database_error_code(exc)
            _record_check(db, run, "deployment.database", "failed", code)
        else:
            _record_check(db, run, "deployment.database", "passed", "database_accessible")
        _check_spool(db, run, settings)
        _check_schema(db, run)
        for provider in PROVIDER_LABELS:
            if provider == "databricks" and not settings.pipeline_enable_databricks:
                continue
            user = _active_administrator(db, run.user_id)
            if user is None:
                _block_pending_checks(db, run, "administrator_unavailable")
                break
            secret = db.scalar(
                select(UserSecret).where(
                    UserSecret.user_id == run.user_id,
                    UserSecret.provider == provider,
                )
            )
            if secret is None:
                _record_check(
                    db, run, f"{provider}.connection", "missing", "credentials_not_configured"
                )
                _record_check(
                    db, run, f"{provider}.catalog", "blocked", "connection_check_required"
                )
                if provider == "databricks":
                    _record_check(
                        db, run, "databricks.compute", "blocked", "connection_check_required"
                    )
                continue
            tested_identity = (secret.id, secret.updated_at)
            try:
                _start_check(db, run, f"{provider}.connection")
                checked = _test_diagnostic_connection(
                    db,
                    run=run,
                    user=user,
                    provider=provider,
                    settings=settings,
                    expected_identity=tested_identity,
                )
            except DiagnosticAdministratorUnavailable:
                db.rollback()
                _record_check(
                    db,
                    run,
                    f"{provider}.connection",
                    "blocked",
                    "administrator_unavailable",
                )
                _block_pending_checks(db, run, "administrator_unavailable")
                break
            except DiagnosticCredentialsStale:
                db.rollback()
                _record_check(db, run, f"{provider}.connection", "blocked", "credentials_stale")
                if provider == "databricks":
                    _record_check(db, run, "databricks.compute", "blocked", "credentials_stale")
                _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                continue
            except (SecretStorageError, ConnectorError, ValueError) as exc:
                db.rollback()
                safe_code = _connection_error_code(exc)
                connection_status = (
                    "missing" if safe_code == "default_dataset_not_configured" else "failed"
                )
                _record_check(
                    db, run, f"{provider}.connection", connection_status, safe_code
                )
                if provider == "databricks":
                    _record_check(
                        db, run, "databricks.compute", "blocked", "connection_check_failed"
                    )
                catalog_status = (
                    "failed"
                    if provider in {"mss", "mcscop"}
                    and safe_code == "source_not_found"
                    else "blocked"
                )
                _record_check(
                    db,
                    run,
                    f"{provider}.catalog",
                    catalog_status,
                    safe_code
                    if catalog_status == "failed"
                    else "connection_check_required"
                    if safe_code == "default_dataset_not_configured"
                    else "connection_check_failed",
                )
                continue
            if checked.status != "connected":
                safe_code = "connection_test_incomplete"
                _record_check(
                    db,
                    run,
                    f"{provider}.connection",
                    "failed",
                    safe_code,
                )
                if provider == "databricks":
                    _record_check(
                        db, run, "databricks.compute", "blocked", "connection_check_failed"
                    )
                catalog_status = (
                    "failed"
                    if provider in {"mss", "mcscop"}
                    and safe_code in {"default_dataset_not_configured", "source_not_found"}
                    else "blocked"
                )
                _record_check(
                    db,
                    run,
                    f"{provider}.catalog",
                    catalog_status,
                    safe_code if catalog_status == "failed" else "connection_check_failed",
                )
                continue
            connection_code = (
                "management_api_available" if provider == "databricks" else "connection_available"
            )
            _record_check(db, run, f"{provider}.connection", "passed", connection_code)
            if provider in {"mss", "mcscop"}:
                if _active_administrator(db, run.user_id) is None:
                    _record_check(
                        db,
                        run,
                        f"{provider}.catalog",
                        "blocked",
                        "administrator_unavailable",
                    )
                    _block_pending_checks(db, run, "administrator_unavailable")
                    break
                if _credential_identity(db, run.user_id, provider) != tested_identity:
                    _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                    continue
                _record_check(db, run, f"{provider}.catalog", "passed", "catalog_available")
                continue
            if provider == "databricks":
                _start_check(db, run, "databricks.compute")
                if checked.runtime_status in {"RUNNING", "RESIZING"}:
                    _record_check(db, run, "databricks.compute", "passed", "compute_ready")
                else:
                    _record_check(db, run, "databricks.compute", "blocked", "compute_not_ready")
                    _record_check(db, run, f"{provider}.catalog", "blocked", "compute_not_ready")
                    continue
                if not settings.pipeline_databricks_startup_contract_verified:
                    _record_check(
                        db,
                        run,
                        f"{provider}.catalog",
                        "blocked",
                        "sql_operations_disabled",
                    )
                    continue
            user = _active_administrator(db, run.user_id)
            if user is None:
                _record_check(
                    db, run, f"{provider}.catalog", "blocked", "administrator_unavailable"
                )
                _block_pending_checks(db, run, "administrator_unavailable")
                break
            if _credential_identity(db, user.id, provider) != tested_identity:
                _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                continue
            try:
                _start_check(db, run, f"{provider}.catalog")
                catalog_user = _active_administrator(db, run.user_id)
                if catalog_user is None:
                    _record_check(
                        db,
                        run,
                        f"{provider}.catalog",
                        "blocked",
                        "administrator_unavailable",
                    )
                    _block_pending_checks(db, run, "administrator_unavailable")
                    break
                catalog = UserCatalog(
                    db,
                    settings,
                    catalog_user,
                    credential_resolver=_diagnostic_catalog_credential_resolver(
                        db, settings, catalog_user, provider, run.id, tested_identity
                    ),
                    cache_enabled=False,
                    allow_compute_start=False,
                    require_administrator=True,
                    expected_credential_identity=tested_identity,
                )
                namespaces = catalog.list_namespaces(provider)
                if _active_administrator(db, run.user_id) is None:
                    _record_check(
                        db,
                        run,
                        f"{provider}.catalog",
                        "blocked",
                        "administrator_unavailable",
                    )
                    _block_pending_checks(db, run, "administrator_unavailable")
                    break
                if _credential_identity(db, user.id, provider) != tested_identity:
                    _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                    continue
                if namespaces:
                    catalog_user = _active_administrator(db, run.user_id)
                    if catalog_user is None:
                        _record_check(
                            db,
                            run,
                            f"{provider}.catalog",
                            "blocked",
                            "administrator_unavailable",
                        )
                        _block_pending_checks(db, run, "administrator_unavailable")
                        break
                    if _credential_identity(db, catalog_user.id, provider) != tested_identity:
                        _record_check(
                            db, run, f"{provider}.catalog", "blocked", "credentials_stale"
                        )
                        continue
                    catalog.list_objects(
                        provider,
                        namespaces[0].name,
                        diagnostic_probe=True,
                    )
                    if _active_administrator(db, run.user_id) is None:
                        _record_check(
                            db,
                            run,
                            f"{provider}.catalog",
                            "blocked",
                            "administrator_unavailable",
                        )
                        _block_pending_checks(db, run, "administrator_unavailable")
                        break
                    if _credential_identity(db, user.id, provider) != tested_identity:
                        _record_check(
                            db, run, f"{provider}.catalog", "blocked", "credentials_stale"
                        )
                        continue
                db.commit()
            except DiagnosticAdministratorUnavailable:
                db.rollback()
                _record_check(
                    db, run, f"{provider}.catalog", "blocked", "administrator_unavailable"
                )
                _block_pending_checks(db, run, "administrator_unavailable")
                break
            except DiagnosticCredentialsStale:
                db.rollback()
                _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                continue
            except ConnectorError as exc:
                db.rollback()
                if _active_administrator(db, run.user_id) is None:
                    _record_check(
                        db,
                        run,
                        f"{provider}.catalog",
                        "blocked",
                        "administrator_unavailable",
                    )
                    _block_pending_checks(db, run, "administrator_unavailable")
                    break
                if _credential_identity(db, run.user_id, provider) != tested_identity:
                    _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                    continue
                if exc.code == TransferErrorCode.COMPUTE_NOT_READY:
                    _record_check(db, run, f"{provider}.catalog", "blocked", "compute_not_ready")
                else:
                    code = _safe_error_code(exc)
                    _record_check(db, run, f"{provider}.catalog", "failed", code)
                    _log_catalog_failure(run, provider, code, type(exc).__name__)
            except (SecretStorageError, ValueError) as exc:
                db.rollback()
                if _active_administrator(db, run.user_id) is None:
                    _record_check(
                        db,
                        run,
                        f"{provider}.catalog",
                        "blocked",
                        "administrator_unavailable",
                    )
                    _block_pending_checks(db, run, "administrator_unavailable")
                    break
                if _credential_identity(db, run.user_id, provider) != tested_identity:
                    _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                    continue
                code = _safe_error_code(exc)
                _record_check(db, run, f"{provider}.catalog", "failed", code)
                _log_catalog_failure(run, provider, code, type(exc).__name__)
            except Exception:
                db.rollback()
                if _active_administrator(db, run.user_id) is None:
                    _record_check(
                        db,
                        run,
                        f"{provider}.catalog",
                        "blocked",
                        "administrator_unavailable",
                    )
                    _block_pending_checks(db, run, "administrator_unavailable")
                    break
                if _credential_identity(db, run.user_id, provider) != tested_identity:
                    _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                    continue
                _record_check(db, run, f"{provider}.catalog", "failed", "provider_unavailable")
                _log_catalog_failure(run, provider, "provider_unavailable", "Exception")
            else:
                if _active_administrator(db, run.user_id) is None:
                    _record_check(
                        db,
                        run,
                        f"{provider}.catalog",
                        "blocked",
                        "administrator_unavailable",
                    )
                    _block_pending_checks(db, run, "administrator_unavailable")
                    break
                if _credential_identity(db, run.user_id, provider) != tested_identity:
                    _record_check(db, run, f"{provider}.catalog", "blocked", "credentials_stale")
                    continue
                catalog_code = (
                    "databricks_sql_catalog_available"
                    if provider == "databricks"
                    else "catalog_available"
                )
                _record_check(db, run, f"{provider}.catalog", "passed", catalog_code)
        checks = json.loads(run.checks_json)
        status = _diagnostic_run_status(checks)
        _finish_run(db, run, status)


def _check_spool(db: Session, run: LiveDiagnosticRun, settings: Settings) -> None:
    _start_check(db, run, "deployment.spool")
    if not settings.pipeline_spool_root:
        _record_check(db, run, "deployment.spool", "skipped", "spool_not_configured")
        return
    test_name = f"data-mover-diagnostic-{uuid.uuid4().hex}.tmp"
    try:
        result = _run_spool_process(
            settings.pipeline_spool_root,
            test_name,
            operation="probe",
            timeout=_DIAGNOSTIC_SPOOL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        cleanup_confirmed = _cleanup_spool_probe(settings.pipeline_spool_root, test_name)
        code = "spool_timeout" if cleanup_confirmed else "spool_cleanup_unconfirmed"
        _record_check(db, run, "deployment.spool", "failed", code)
        return
    except _SpoolProbeLaunchFailure:
        _record_check(db, run, "deployment.spool", "failed", "spool_unavailable")
        return
    except _SpoolProbeUnavailable:
        cleanup_confirmed = _cleanup_spool_probe(settings.pipeline_spool_root, test_name)
        code = "spool_unavailable" if cleanup_confirmed else "spool_cleanup_unconfirmed"
        _record_check(db, run, "deployment.spool", "failed", code)
        return

    if result.get("cleanup_ok") is not True:
        if not _cleanup_spool_probe(settings.pipeline_spool_root, test_name):
            _record_check(
                db, run, "deployment.spool", "failed", "spool_cleanup_unconfirmed"
            )
            return
        _record_check(db, run, "deployment.spool", "failed", "spool_unavailable")
        return
    if result.get("ok") is True:
        _record_check(db, run, "deployment.spool", "passed", "spool_available")
    else:
        _record_check(db, run, "deployment.spool", "failed", "spool_unavailable")


def _run_spool_process(
    root: str,
    name: str,
    *,
    operation: str,
    timeout: int,
) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _SPOOL_PROBE_SCRIPT],
            input=json.dumps(
                {"root": root, "name": name, "operation": operation},
                separators=(",", ":"),
            ),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except OSError as exc:
        raise _SpoolProbeLaunchFailure from exc
    if completed.returncode != 0:
        raise _SpoolProbeUnavailable
    try:
        result = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _SpoolProbeUnavailable from exc
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise _SpoolProbeUnavailable
    return result


def _cleanup_spool_probe(root: str, name: str) -> bool:
    try:
        result = _run_spool_process(
            root,
            name,
            operation="cleanup",
            timeout=_DIAGNOSTIC_SPOOL_CLEANUP_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, _SpoolProbeUnavailable):
        return False
    return result.get("ok") is True


def _check_schema(db: Session, run: LiveDiagnosticRun) -> None:
    _start_check(db, run, "deployment.schema")
    current = None
    expected = None
    code = "schema_revision_unavailable"
    try:
        expected = head_revision()
        current = _run_database_probe(db, "schema")
        ready = current == expected
    except _DatabaseProbeTimeout:
        db.rollback()
        code = "schema_revision_timeout"
        ready = False
    except Exception as exc:
        db.rollback()
        code = (
            "schema_revision_timeout"
            if _database_error_code(exc) == "database_timeout"
            else "schema_revision_unavailable"
        )
        ready = False
    _record_check(
        db,
        run,
        "deployment.schema",
        "passed" if ready else "failed",
        "schema_current" if ready else "schema_not_current" if current is not None else code,
        details={
            "current_revision": current or "unavailable",
            "expected_revision": expected or "unavailable",
        },
    )


def _run_database_probe(db: Session, operation: str) -> str | None:
    """Run application database SQL in an isolated, wall-clock-bounded process."""
    bind = db.get_bind()
    url = bind.url if isinstance(bind, Engine) else bind.engine.url
    if url.get_backend_name() == "sqlite" and (
        not url.database or url.database == ":memory:" or url.query.get("mode") == "memory"
    ):
        return _run_in_memory_database_probe(db, operation)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _DATABASE_PROBE_SCRIPT],
            input=json.dumps(
                {
                    "url": url.render_as_string(hide_password=False),
                    "operation": operation,
                },
                separators=(",", ":"),
            ),
            capture_output=True,
            text=True,
            timeout=_DIAGNOSTIC_DATABASE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _DatabaseProbeTimeout from exc
    except OSError as exc:
        raise _DatabaseProbeUnavailable from exc
    if completed.returncode != 0:
        raise _DatabaseProbeUnavailable
    try:
        result = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _DatabaseProbeUnavailable from exc
    if isinstance(result, dict) and result.get("timeout") is True:
        raise _DatabaseProbeTimeout
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise _DatabaseProbeUnavailable
    revision = result.get("revision")
    if revision is None:
        return None
    return str(revision)


def _run_in_memory_database_probe(db: Session, operation: str) -> str | None:
    """Preserve the caller connection's busy timeout for in-memory test databases."""
    previous_timeout = int(db.execute(text("PRAGMA busy_timeout")).scalar_one())
    db.execute(text(f"PRAGMA busy_timeout = {_DIAGNOSTIC_DATABASE_TIMEOUT_MS}"))
    try:
        if operation == "database":
            db.execute(text("SELECT 1")).scalar_one()
            return None
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        return str(revision[0]) if len(revision) == 1 else None
    finally:
        db.execute(text(f"PRAGMA busy_timeout = {previous_timeout}"))


def _database_error_code(error: Exception) -> str:
    if isinstance(error, _DatabaseProbeTimeout):
        return "database_timeout"
    if isinstance(error, _DatabaseProbeUnavailable):
        return "database_unavailable"
    original = getattr(error, "orig", error)
    sqlstate = getattr(original, "sqlstate", "") or getattr(original, "pgcode", "")
    if sqlstate == "57014":
        return "database_timeout"
    message = str(original).casefold()
    if any(
        marker in message
        for marker in ("timeout", "timed out", "database is locked", "database is busy")
    ):
        return "database_timeout"
    return "database_unavailable"


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, ConnectionNotConfiguredError):
        return "credentials_not_configured"
    if isinstance(error, ConnectorError):
        value = str(error.code)
        return value if value.replace("_", "").isalnum() else "provider_unavailable"
    if isinstance(error, SecretStorageError):
        return "credential_check_failed"
    return "provider_unavailable"


def _connection_error_code(error: Exception) -> str:
    diagnostic_code = getattr(error, "diagnostic_code", None)
    if diagnostic_code in {"default_dataset_not_configured", "source_not_found"}:
        return diagnostic_code
    return _safe_error_code(error)


def _record_check(
    db: Session,
    run: LiveDiagnosticRun,
    key: str,
    status: str,
    code: str,
    *,
    details: dict[str, str] | None = None,
) -> None:
    _renew_diagnostic_lease(db, run)
    checks = json.loads(run.checks_json)
    for item in checks:
        if item["key"] == key:
            item.update(status=status, code=code, completed_at=datetime.now(UTC).isoformat())
            if details:
                item["details"] = details
            break
    run.checks_json = json.dumps(checks, separators=(",", ":"))
    db.commit()


def _start_check(db: Session, run: LiveDiagnosticRun, key: str) -> None:
    _renew_diagnostic_lease(db, run)
    checks = json.loads(run.checks_json)
    for item in checks:
        if item["key"] == key and item["status"] == "pending":
            item["status"] = "running"
            item["started_at"] = datetime.now(UTC).isoformat()
            break
    run.checks_json = json.dumps(checks, separators=(",", ":"))
    db.commit()


def _renew_diagnostic_lease(db: Session, run: LiveDiagnosticRun) -> None:
    worker_token = run.worker_token
    now = utcnow()
    if not worker_token:
        raise DiagnosticLeaseLost()
    result = execute_dml(
        db,
        update(LiveDiagnosticRun)
        .where(
            LiveDiagnosticRun.id == run.id,
            LiveDiagnosticRun.status == "running",
            LiveDiagnosticRun.worker_token == worker_token,
            LiveDiagnosticRun.lease_expires_at > now,
        )
        .values(lease_expires_at=now + timedelta(seconds=RUN_LEASE_SECONDS))
    )
    if result.rowcount != 1:
        db.rollback()
        raise DiagnosticLeaseLost()
    db.refresh(run, attribute_names=["checks_json"])


def _record_unexpected_failure(run_id: str, worker_token: str, error: Exception) -> None:
    try:
        with current_session_factory()() as db:
            run = db.get(LiveDiagnosticRun, run_id)
            if (
                run is None
                or run.status != "running"
                or run.worker_token != worker_token
                or run.lease_expires_at is None
                or run.lease_expires_at <= utcnow()
            ):
                return
            checks = json.loads(run.checks_json)
            for item in checks:
                if item["status"] == "running":
                    item.update(
                        status="failed",
                        code="diagnostic_worker_failed",
                        completed_at=datetime.now(UTC).isoformat(),
                    )
                elif item["status"] == "pending":
                    item.update(
                        status="skipped",
                        code="diagnostic_not_run",
                        completed_at=datetime.now(UTC).isoformat(),
                    )
            now = utcnow()
            result = execute_dml(
                db,
                update(LiveDiagnosticRun)
                .where(
                    LiveDiagnosticRun.id == run_id,
                    LiveDiagnosticRun.status == "running",
                    LiveDiagnosticRun.worker_token == worker_token,
                    LiveDiagnosticRun.lease_expires_at > now,
                    LiveDiagnosticRun.checks_json == run.checks_json,
                )
                .values(
                    checks_json=json.dumps(checks, separators=(",", ":")),
                    status="completed_with_failures",
                    worker_token=None,
                    lease_expires_at=None,
                    finished_at=now,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                return
            user_id = run.user_id
            db.commit()
        log_event(
            log,
            "pipeline.run.unexpected_failure",
            outcome="failed",
            reference_id=run_id,
            run_id=run_id,
            user_id=user_id,
            operation="live_diagnostics",
            exception_type=type(error).__name__,
            traceback="Diagnostic worker failed while executing saved checks.",
        )
    except Exception:
        log.error("Unable to finalize diagnostic run %s after worker failure", run_id)


def _active_administrator(db: Session, user_id: str) -> User | None:
    user = db.scalar(
        select(User)
        .options(selectinload(User.roles))
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active or "administrator" not in user.role_names:
        return None
    return user


def _diagnostic_catalog_credential_resolver(
    db: Session,
    settings: Settings,
    user: User,
    provider: str,
    run_id: str,
    expected_identity: tuple[str, datetime],
):
    expected_provider = provider.casefold()

    def resolve(*, user_id: str, provider: str, purpose: str) -> dict[str, str]:
        if user_id != user.id or provider.casefold() != expected_provider:
            raise DiagnosticCredentialsStale()
        if _active_administrator(db, user_id) is None:
            raise DiagnosticAdministratorUnavailable()
        try:
            credentials, secret_id, revision = decrypt_user_credentials_for_run_with_revision(
                db,
                settings,
                user=user,
                provider=expected_provider,
                request=RequestMetadata(reference_id=run_id),
                purpose="diagnostic_catalog",
            )
        except ConnectionNotConfiguredError as exc:
            raise DiagnosticCredentialsStale() from exc
        if (secret_id, revision) != expected_identity:
            raise DiagnosticCredentialsStale()
        return credentials

    return resolve


def _credential_identity(db: Session, user_id: str, provider: str) -> tuple[str, datetime] | None:
    row = db.execute(
        select(UserSecret.id, UserSecret.updated_at).where(
            UserSecret.user_id == user_id,
            UserSecret.provider == provider,
        )
    ).one_or_none()
    return (row.id, row.updated_at) if row is not None else None


def _block_pending_checks(db: Session, run: LiveDiagnosticRun, code: str) -> None:
    _renew_diagnostic_lease(db, run)
    checks = json.loads(run.checks_json)
    for item in checks:
        if item["status"] == "pending":
            item.update(
                status="blocked",
                code=code,
                completed_at=datetime.now(UTC).isoformat(),
            )
    run.checks_json = json.dumps(checks, separators=(",", ":"))
    db.commit()


def _log_catalog_failure(
    run: LiveDiagnosticRun, provider: str, error_code: str, exception_type: str
) -> None:
    log_event(
        log,
        "diagnostic.check.failed",
        outcome="failed",
        reference_id=run.id,
        run_id=run.id,
        user_id=run.user_id,
        provider=provider,
        operation="catalog_check",
        stage="catalog",
        error_code=error_code,
        exception_type=exception_type,
    )


def _finish_run(db: Session, run: LiveDiagnosticRun, status: str) -> None:
    _renew_diagnostic_lease(db, run)
    run.status = status
    run.finished_at = utcnow()
    run.worker_token = None
    run.lease_expires_at = None
    db.commit()
