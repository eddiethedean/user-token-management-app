"""Unit tests for email outbox claim, delivery, retry, and dead-letter."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import EmailOutbox, utcnow
from app.services.mailer import (
    deliver_pending,
    deliver_pending_with_metrics,
    queue_email,
    retry_failed,
)


def test_console_delivery_marks_sent(access_app, monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    settings = get_settings()
    with SessionLocal() as db:
        queue_email(db, "user@example.gov", "Hello", "Body text")
        db.commit()
        metrics = deliver_pending_with_metrics(db, settings, limit=10)
        assert metrics.claimed == 1
        assert metrics.delivered == 1
        assert metrics.dead_lettered == 0
        message = db.scalar(select(EmailOutbox))
        assert message is not None
        assert message.sent_at is not None
        assert message.failed_at is None


def test_smtp_failure_defers_then_dead_letters(access_app, monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("EMAIL_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("EMAIL_RETRY_BASE_SECONDS", "1")
    get_settings.cache_clear()
    settings = get_settings()
    with SessionLocal() as db:
        queue_email(db, "user@example.gov", "Hello", "Body text")
        db.commit()

        with patch("app.services.mailer._send_smtp", side_effect=OSError("smtp down")):
            first = deliver_pending_with_metrics(db, settings, limit=10)
            assert first.claimed == 1
            assert first.delivered == 0
            assert first.deferred == 1
            assert first.dead_lettered == 0

            message = db.scalar(select(EmailOutbox))
            assert message is not None
            assert message.failed_at is None
            assert message.attempts == 1
            message.delivery_state.next_attempt_at = utcnow() - timedelta(seconds=1)
            db.commit()

            second = deliver_pending_with_metrics(db, settings, limit=10)
            assert second.dead_lettered == 1
            assert second.delivered == 0

            db.refresh(message)
            assert message.failed_at is not None


def test_retry_failed_requeues_dead_letter(access_app, monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    settings = get_settings()
    with SessionLocal() as db:
        message = queue_email(db, "user@example.gov", "Hello", "Body text")
        db.commit()
        message.failed_at = utcnow()
        message.attempts = 5
        message.last_error = "boom"
        db.commit()

        assert retry_failed(db, message_id=message.id) == 1
        db.refresh(message)
        assert message.failed_at is None
        assert message.attempts == 0

        assert deliver_pending(db, settings, limit=10) == 1
        db.refresh(message)
        assert message.sent_at is not None
