"""Durable pipeline execution used by the Hedron app runtime."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import parse_snapshot
from app.models import PipelineRun, User
from app.services import pipeline_runs
from app.services.pipeline_state import RunConflictError
from app.services.secrets import decrypt_user_credentials_for_run
from app.services.transfer_engine import execute_transfer

log = logging.getLogger(__name__)


@dataclass
class LeaseKeeper:
    """Renew one claimed run from an independent database session."""

    settings: Settings
    run_id: str
    lease_token: str
    stopped: threading.Event = field(default_factory=threading.Event)
    lost: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"pipeline-lease-{self.run_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self.stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        from app.database import SessionLocal

        interval = max(1.0, self.settings.pipeline_lease_seconds / 3)
        while not self.stopped.wait(interval):
            try:
                with SessionLocal() as heartbeat_db:
                    renewed = pipeline_runs.renew_lease(
                        heartbeat_db,
                        run_id=self.run_id,
                        lease_token=self.lease_token,
                        lease_seconds=self.settings.pipeline_lease_seconds,
                    )
            except Exception:
                log.exception("pipeline lease heartbeat failed", extra={"run_id": self.run_id})
                self.lost.set()
                return
            if not renewed:
                self.lost.set()
                return


def worker_id(settings: Settings) -> str:
    """Return the lease owner identifier for this Hedron app process."""
    import os
    import socket

    return settings.pipeline_worker_id or f"{socket.gethostname()}-{os.getpid()}"


def process_one(
    db: Session,
    settings: Settings,
    *,
    run_id: str | None = None,
    stop_event: threading.Event | None = None,
) -> bool:
    if stop_event is not None and stop_event.is_set():
        return False
    claimed = pipeline_runs.claim_run(
        db,
        worker_id=worker_id(settings),
        lease_seconds=settings.pipeline_lease_seconds,
        run_id=run_id,
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
    keeper = LeaseKeeper(settings=settings, run_id=run_id, lease_token=lease_token)
    keeper.start()
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
            cancel_requested=lambda: (
                _cancel_flag(db, run_id) or (stop_event is not None and stop_event.is_set())
            ),
            lease_lost=keeper.lost.is_set,
        )
    except ConnectorError as exc:
        db.rollback()
        failed = db.get(PipelineRun, run_id)
        if failed is None:
            return True
        try:
            pipeline_runs.fail_run(
                db,
                failed,
                code=exc.code,
                summary=str(exc),
                retryable=bool(exc.retryable),
                needs_reconciliation=exc.code.value == "publish_uncertain",
                lease_token=lease_token,
            )
        except RunConflictError:
            log.warning(
                "pipeline lease was lost before failure could be recorded", extra={"run_id": run_id}
            )
    except RunConflictError:
        db.rollback()
        log.warning("pipeline worker stopped after losing its lease", extra={"run_id": run_id})
    except Exception:
        log.exception("pipeline run %s failed", run_id)
        db.rollback()
        failed = db.get(PipelineRun, run_id)
        if failed is not None:
            try:
                pipeline_runs.fail_run(
                    db,
                    failed,
                    code=TransferErrorCode.INTERNAL_ERROR,
                    summary="The transfer failed unexpectedly.",
                    lease_token=lease_token,
                )
            except RunConflictError:
                log.warning(
                    "pipeline lease was lost before an unexpected failure could be recorded",
                    extra={"run_id": run_id},
                )
    finally:
        keeper.stop()
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
    # ``db.get`` may return the worker's already-loaded PipelineRun from the
    # identity map. Cancellation is requested by a different transaction, so
    # read this one scalar directly from the database on every check.
    return (
        db.scalar(select(PipelineRun.cancel_requested_at).where(PipelineRun.id == run_id))
        is not None
    )
