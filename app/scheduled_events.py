"""A process-local midnight log probe, independent of HTTP requests."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def next_midnight(now: datetime) -> datetime:
    """Return the next calendar day's midnight in the supplied timezone."""
    return datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)


async def _run_midnight_events(zone: ZoneInfo, scheduled_at: datetime) -> None:
    while True:
        now = datetime.now(zone)
        # Timestamps account for 23/25-hour days. Recheck the wall clock each
        # minute so clock corrections do not leave a day-long sleep stale.
        remaining = scheduled_at.timestamp() - now.timestamp()
        if remaining > 0:
            await asyncio.sleep(min(remaining, 60.0))
            continue
        log.info(
            "The app ran the scheduled event. scheduled_at=%s executed_at=%s timezone=%s pid=%s",
            scheduled_at.isoformat(),
            now.isoformat(),
            zone.key,
            os.getpid(),
            extra={"event": "app.scheduled_event.ran"},
        )
        scheduled_at = next_midnight(now)


@asynccontextmanager
async def midnight_event_scheduler(timezone_name: str) -> AsyncIterator[None]:
    """Start with the app; cancel and await the timer before app shutdown."""
    zone = ZoneInfo(timezone_name)
    scheduled_at = next_midnight(datetime.now(zone))
    task = asyncio.create_task(_run_midnight_events(zone, scheduled_at), name="midnight-log-event")
    log.info(
        "Midnight scheduled event started. next_run=%s timezone=%s pid=%s",
        scheduled_at.isoformat(),
        zone.key,
        os.getpid(),
        extra={"event": "app.scheduled_event.started"},
    )
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
