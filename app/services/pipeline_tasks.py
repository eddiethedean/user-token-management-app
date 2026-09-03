"""In-process pipeline execution and retention tasks."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from fastapi import BackgroundTasks

from app.config import Settings

log = logging.getLogger(__name__)
_BACKGROUND_PIPELINE_LOCK = threading.Lock()


def schedule_pipeline_run(
    background_tasks: BackgroundTasks,
    settings: Settings,
    run_id: str,
    stop_event: threading.Event | None = None,
) -> None:
    """Attach a queued transfer to the response that created it."""
    if settings.app_env != "test":
        background_tasks.add_task(process_pipeline_run_background, settings, run_id, stop_event)


def _try_acquire_pipeline_lock() -> bool:
    """Avoid consuming a Starlette thread while another in-app task is running."""
    acquired = _BACKGROUND_PIPELINE_LOCK.acquire(blocking=False)
    if not acquired:
        log.debug("Background pipeline task deferred to the in-process supervisor")
    return acquired


def process_pipeline_run_background(
    settings: Settings,
    run_id: str,
    stop_event: threading.Event | None = None,
) -> None:
    """Claim and execute one run after its enqueue response has been sent."""
    from app.database import SessionLocal, sqlite_worker_lock
    from app.worker import process_one

    if not _try_acquire_pipeline_lock():
        return
    try:
        with sqlite_worker_lock(settings.database_url, "pipeline"):
            with SessionLocal() as db:
                process_one(db, settings, run_id=run_id, stop_event=stop_event)
    except Exception:
        # Background task failures happen after the response has been sent;
        # the durable run row and event feed contain the operator-visible state.
        log.exception("Background pipeline run failed", extra={"run_id": run_id})
    finally:
        _BACKGROUND_PIPELINE_LOCK.release()


def process_pending_pipeline_run_background(
    settings: Settings, stop_event: threading.Event | None = None
) -> bool:
    """Recover one queued or expired run from the in-process supervisor."""
    from app.database import SessionLocal, sqlite_worker_lock
    from app.worker import process_one

    if stop_event is not None and stop_event.is_set():
        return False
    with _BACKGROUND_PIPELINE_LOCK:
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            with sqlite_worker_lock(settings.database_url, "pipeline"):
                with SessionLocal() as db:
                    return process_one(db, settings, stop_event=stop_event)
        except Exception:
            log.exception("Background pipeline recovery cycle failed")
            return False


def run_pipeline_janitor_background(settings: Settings) -> None:
    """Run retention cleanup without requiring an external janitor service."""
    from app.database import SessionLocal, sqlite_worker_lock
    from app.services.pipeline_runs import janitor

    with _BACKGROUND_PIPELINE_LOCK:
        try:
            with sqlite_worker_lock(settings.database_url, "pipeline"):
                with SessionLocal() as db:
                    counts = janitor(db, settings)
            log.info("Background pipeline janitor completed", extra=counts)
        except Exception:
            log.exception("Background pipeline janitor failed")


async def run_background_runtime(settings: Settings, stop_event: threading.Event) -> None:
    """Supervise queued transfers and periodic retention inside the web process."""
    next_janitor = 0.0
    while not stop_event.is_set():
        await asyncio.to_thread(process_pending_pipeline_run_background, settings, stop_event)
        if stop_event.is_set():
            break
        now = time.monotonic()
        if now >= next_janitor:
            await asyncio.to_thread(run_pipeline_janitor_background, settings)
            next_janitor = now + settings.pipeline_janitor_interval_seconds
        await asyncio.to_thread(stop_event.wait, settings.pipeline_background_poll_seconds)
