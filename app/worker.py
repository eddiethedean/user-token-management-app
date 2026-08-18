"""Durable pipeline worker. Claims leased runs and executes transfers."""

from __future__ import annotations

import logging
import os
import socket
import tempfile
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import parse_snapshot
from app.connectors.registry import load_builtin_connectors
from app.connectors.tls import apply_internal_ca_fix
from app.database import SessionLocal
from app.models import PipelineRun, User
from app.schema import assert_schema_current
from app.services import pipeline_runs
from app.services.secrets import decrypt_user_credentials_for_run
from app.services.transfer_engine import execute_transfer

log = logging.getLogger(__name__)


def worker_id(settings: Settings) -> str:
    return settings.pipeline_worker_id or f"{socket.gethostname()}-{os.getpid()}"


def process_one(db: Session, settings: Settings) -> bool:
    claimed = pipeline_runs.claim_run(
        db, worker_id=worker_id(settings), lease_seconds=settings.pipeline_lease_seconds
    )
    if claimed is None:
        return False
    run, lease_token = claimed
    run_id = run.id
    user = db.get(User, run.user_id)
    if user is None:
        pipeline_runs.fail_run(
            db,
            run,
            code=TransferErrorCode.INTERNAL_ERROR,
            summary="The run owner no longer exists.",
            lease_token=lease_token,
        )
        return True
    try:
        snapshot = parse_snapshot(run.definition_snapshot_json)
        source_credentials = _credentials_for(
            db, settings, user=user, provider=snapshot.source_provider, snapshot=snapshot
        )
        destination_credentials = decrypt_user_credentials_for_run(
            db, settings, user=user, provider=snapshot.destination_provider
        )
        execute_transfer(
            db,
            run=run,
            lease_token=lease_token,
            snapshot=snapshot,
            source_credentials=source_credentials,
            destination_credentials=destination_credentials,
            settings=settings,
            cancel_requested=lambda: _cancel_flag(db, run_id),
        )
    except ConnectorError as exc:
        db.rollback()
        failed = db.get(PipelineRun, run_id)
        if failed is None:
            return True
        pipeline_runs.fail_run(
            db,
            failed,
            code=exc.code,
            summary=str(exc),
            retryable=bool(exc.retryable),
            needs_reconciliation=exc.code.value == "publish_uncertain",
            lease_token=lease_token,
        )
    except Exception:
        log.exception("pipeline run %s failed", run_id)
        db.rollback()
        failed = db.get(PipelineRun, run_id)
        if failed is not None:
            pipeline_runs.fail_run(
                db,
                failed,
                code=TransferErrorCode.INTERNAL_ERROR,
                summary="The transfer failed unexpectedly.",
                lease_token=lease_token,
            )
    return True


def _credentials_for(db, settings, *, user, provider, snapshot) -> dict[str, str]:
    if provider == "csv":
        from app.models import PipelineUpload

        upload_id = snapshot.source_upload_id or getattr(snapshot.source, "upload_id", "")
        upload = db.get(PipelineUpload, upload_id) if upload_id else None
        if upload is None or upload.user_id != user.id:
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "The CSV upload is no longer available."
            )
        if upload.checksum_sha256 != getattr(
            snapshot.source, "checksum_sha256", upload.checksum_sha256
        ):
            raise ConnectorError(
                TransferErrorCode.SCHEMA_DRIFT, "The CSV upload checksum no longer matches."
            )
        return {
            "content": upload.content.decode("utf-8") if isinstance(upload.content, bytes) else ""
        }
    return decrypt_user_credentials_for_run(db, settings, user=user, provider=provider)


def _cancel_flag(db: Session, run_id: str) -> bool:
    fresh = db.get(PipelineRun, run_id)
    return fresh is not None and fresh.cancel_requested_at is not None


def run_worker(*, once: bool = False, poll_seconds: float = 2.0) -> None:
    settings = get_settings()
    assert_schema_current()
    if settings.data_mover_mode == "real":
        if not str(settings.database_url).startswith("postgresql"):
            raise SystemExit("Real pipeline workers require a PostgreSQL application database.")
        spool = Path(settings.pipeline_spool_root)
        spool.mkdir(parents=True, exist_ok=True)
        if settings.pipeline_apply_internal_ca_fix:
            apply_internal_ca_fix()
    else:
        if not settings.pipeline_spool_root:
            settings.pipeline_spool_root = tempfile.mkdtemp(prefix="data-mover-spool-")
    load_builtin_connectors(demo=settings.is_demo_mode)
    log.info("pipeline worker %s starting mode=%s", worker_id(settings), settings.data_mover_mode)
    try:
        while True:
            with SessionLocal() as db:
                processed = process_one(db, settings)
            if once:
                break
            if not processed:
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("pipeline worker stopping")
