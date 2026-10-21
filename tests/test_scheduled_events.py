from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app import scheduled_events
from app.config import Settings


@pytest.mark.parametrize(
    ("start", "hours"),
    [
        ("2026-10-21T00:00:00", 24),
        ("2026-03-08T00:00:00", 23),
        ("2026-11-01T00:00:00", 25),
        ("2026-12-31T00:00:00", 24),
    ],
)
def test_next_midnight_follows_local_calendar_across_dst_and_year_end(start, hours):
    now = datetime.fromisoformat(start).replace(tzinfo=ZoneInfo("America/New_York"))
    target = scheduled_events.next_midnight(now)
    assert target.date() == now.date() + timedelta(days=1)
    assert target.hour == target.minute == target.second == 0
    assert target.timestamp() - now.timestamp() == hours * 3600


def test_timezone_setting_rejects_unknown_zones(monkeypatch):
    monkeypatch.setenv("SCHEDULED_EVENT_TIMEZONE", "invalid/timezone")
    with pytest.raises(ValidationError, match="installed IANA timezone"):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    monkeypatch.setenv("SCHEDULED_EVENT_TIMEZONE", "UTC")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.scheduled_event_timezone == "UTC"


def test_timer_logs_once_when_due_without_http_requests(monkeypatch, caplog):
    zone = ZoneInfo("America/New_York")
    current = datetime(2026, 10, 21, 23, 59, 59, tzinfo=zone)
    sleeps = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(current.timestamp(), tz)

    async def advance(seconds):
        nonlocal current
        sleeps.append(seconds)
        if len(sleeps) == 1:
            current += timedelta(seconds=seconds)
        else:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduled_events, "datetime", Clock)
    monkeypatch.setattr(scheduled_events.asyncio, "sleep", advance)
    caplog.set_level(logging.INFO, logger=scheduled_events.__name__)
    target = datetime(2026, 10, 22, tzinfo=zone)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduled_events._run_midnight_events(zone, target))
    records = [r for r in caplog.records if getattr(r, "event", "") == "app.scheduled_event.ran"]
    assert len(records) == 1
    assert "The app ran the scheduled event." in records[0].getMessage()
    assert "scheduled_at=2026-10-22T00:00:00-04:00" in records[0].getMessage()
    assert "executed_at=2026-10-22T00:00:00-04:00" in records[0].getMessage()
    assert "pid=" in records[0].getMessage()
    assert sleeps == [1.0, 60.0]


def test_app_lifespan_starts_and_cancels_timer_without_requests(access_app, monkeypatch, caplog):
    from app.main import lifespan

    fired = asyncio.Event()
    original_next_midnight = scheduled_events.next_midnight
    original_info = scheduled_events.log.info
    first = True

    def near_midnight(now):
        nonlocal first
        if first:
            first = False
            return now + timedelta(milliseconds=10)
        return original_next_midnight(now)

    def observe(message, *args, **kwargs):
        original_info(message, *args, **kwargs)
        if kwargs.get("extra", {}).get("event") == "app.scheduled_event.ran":
            fired.set()

    monkeypatch.setattr(scheduled_events, "next_midnight", near_midnight)
    monkeypatch.setattr(scheduled_events.log, "info", observe)
    caplog.set_level(logging.INFO, logger=scheduled_events.__name__)

    async def scenario():
        async with lifespan(access_app):
            await asyncio.wait_for(fired.wait(), timeout=2)
            timer = next(t for t in asyncio.all_tasks() if t.get_name() == "midnight-log-event")
            assert not timer.done()
        assert timer.cancelled()

    asyncio.run(scenario())
    records = [
        r for r in caplog.records if getattr(r, "event", "") == "app.scheduled_event.started"
    ]
    assert len(records) == 1
    assert "next_run=" in records[0].getMessage()
    assert sum(getattr(r, "event", "") == "app.scheduled_event.ran" for r in caplog.records) == 1
