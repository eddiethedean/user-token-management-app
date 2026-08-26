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


class _FakeSmtp:
    sent = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def starttls(self, *, context):
        self.starttls_called = True

    def login(self, username, password):
        return None

    def send_message(self, message):
        self.sent.append(message)


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


def test_smtp_delivery_is_multipart_with_branded_html(access_app, monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.gov")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    get_settings.cache_clear()
    settings = get_settings()
    _FakeSmtp.sent.clear()
    with SessionLocal() as db:
        queue_email(
            db,
            "user@example.gov",
            "Invitation to Data Mover",
            "Accept your invitation:\nhttp://testserver/invitations/accept?token=secret",
        )
        db.commit()
        with patch("app.services.mailer.smtplib.SMTP", _FakeSmtp):
            assert deliver_pending(db, settings) == 1

    assert len(_FakeSmtp.sent) == 1
    message = _FakeSmtp.sent[0]
    assert message.get_content_type() == "multipart/alternative"
    alternatives = list(message.iter_parts())
    assert [part.get_content_type() for part in alternatives] == ["text/plain", "text/html"]
    assert "Accept your invitation" in alternatives[0].get_content()
    html = alternatives[1].get_content()
    assert "Data Mover" in html
    assert "Continue to Data Mover" in html
    assert "token=secret" in html
