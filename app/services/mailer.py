from __future__ import annotations

import logging
import re
import smtplib
import ssl
import threading
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from html import escape

from fastapi import BackgroundTasks
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import EmailDeliveryState, EmailOutbox, new_id, utcnow

log = logging.getLogger(__name__)
_URL_PATTERN = re.compile(r"https?://[^\s<>]+")
_BACKGROUND_DELIVERY_LOCK = threading.Lock()


@dataclass(frozen=True)
class DeliveryMetrics:
    claimed: int = 0
    delivered: int = 0
    deferred: int = 0
    dead_lettered: int = 0


def schedule_email_delivery(background_tasks: BackgroundTasks, settings: Settings) -> None:
    """Attach one outbox drain to a response that may have queued email."""
    if settings.app_env != "test":
        background_tasks.add_task(deliver_pending_background, settings)


def deliver_pending_background(settings: Settings, *, limit: int = 20) -> None:
    """Drain one outbox batch from a FastAPI background task."""
    from app.database import SessionLocal

    with _BACKGROUND_DELIVERY_LOCK:
        try:
            with SessionLocal() as db:
                while deliver_pending_with_metrics(db, settings, limit=limit).claimed == limit:
                    pass
        except Exception:
            # Background task failures happen after the response has been sent;
            # log them without turning a successful request into a server error.
            log.exception("Background email delivery cycle failed")


def queue_email(db: Session, recipient: str, subject: str, body_text: str) -> EmailOutbox:
    message_id = new_id()
    message = EmailOutbox(
        id=message_id,
        recipient=recipient,
        subject=subject,
        body_text=body_text,
        delivery_state=EmailDeliveryState(message_id=message_id, next_attempt_at=utcnow()),
    )
    db.add(message)
    return message


def _claim_pending(db: Session, settings: Settings, *, limit: int) -> list[tuple[str, str]]:
    now = utcnow()
    stale_before = now - timedelta(seconds=settings.email_claim_timeout_seconds)
    statement = (
        select(EmailDeliveryState)
        .join(EmailOutbox, EmailOutbox.id == EmailDeliveryState.message_id)
        .where(
            EmailOutbox.sent_at.is_(None),
            EmailOutbox.failed_at.is_(None),
            EmailDeliveryState.next_attempt_at <= now,
            or_(
                EmailDeliveryState.claim_token.is_(None),
                EmailDeliveryState.claimed_at <= stale_before,
            ),
        )
        .order_by(EmailDeliveryState.next_attempt_at, EmailDeliveryState.message_id)
        .limit(limit)
    )
    if db.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    states = list(db.scalars(statement).all())
    claims: list[tuple[str, str]] = []
    for state in states:
        claim_token = new_id()
        state.claim_token = claim_token
        state.claimed_at = now
        claims.append((state.message_id, claim_token))
    db.commit()
    return claims


def _deliver_claim(
    db: Session,
    settings: Settings,
    *,
    message_id: str,
    claim_token: str,
) -> tuple[bool, bool]:
    row = db.execute(
        select(EmailOutbox, EmailDeliveryState)
        .join(EmailDeliveryState, EmailDeliveryState.message_id == EmailOutbox.id)
        .where(
            EmailOutbox.id == message_id,
            EmailOutbox.sent_at.is_(None),
            EmailOutbox.failed_at.is_(None),
            EmailDeliveryState.claim_token == claim_token,
        )
    ).one_or_none()
    if not row:
        db.rollback()
        return False, False
    message, state = row
    message.attempts += 1
    attempt = message.attempts
    db.commit()

    try:
        if settings.email_backend == "console":
            print(
                f"\n--- EMAIL TO {message.recipient} ---\n{message.subject}\n\n{message.body_text}\n"
            )
        else:
            _send_smtp(message, settings)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        db.refresh(message)
        db.refresh(state)
        if state.claim_token != claim_token or message.sent_at or message.failed_at:
            db.rollback()
            return False, False
        message.last_error = str(exc)[:2000]
        dead_lettered = attempt >= settings.email_max_attempts
        if dead_lettered:
            message.failed_at = utcnow()
        else:
            delay = min(
                settings.email_retry_base_seconds * (2 ** (attempt - 1)),
                settings.email_retry_max_seconds,
            )
            state.next_attempt_at = utcnow() + timedelta(seconds=delay)
        state.claim_token = None
        state.claimed_at = None
        db.commit()
        log.warning(
            "Email delivery failed",
            extra={"message_id": message.id, "attempt": attempt, "dead_lettered": dead_lettered},
        )
        return False, dead_lettered

    db.refresh(message)
    db.refresh(state)
    if state.claim_token != claim_token or message.sent_at or message.failed_at:
        db.rollback()
        return False, False
    message.sent_at = utcnow()
    message.last_error = ""
    if settings.email_redact_sent_bodies:
        message.body_text = "[redacted after delivery]"
    state.claim_token = None
    state.claimed_at = None
    db.commit()
    return True, False


def deliver_pending_with_metrics(
    db: Session, settings: Settings, *, limit: int = 20
) -> DeliveryMetrics:
    claims = _claim_pending(db, settings, limit=limit)
    delivered = 0
    dead_lettered = 0
    for message_id, claim_token in claims:
        was_delivered, was_dead_lettered = _deliver_claim(
            db,
            settings,
            message_id=message_id,
            claim_token=claim_token,
        )
        delivered += int(was_delivered)
        dead_lettered += int(was_dead_lettered)
    metrics = DeliveryMetrics(
        claimed=len(claims),
        delivered=delivered,
        deferred=len(claims) - delivered - dead_lettered,
        dead_lettered=dead_lettered,
    )
    log.info(
        "Email delivery batch completed",
        extra={
            "claimed": metrics.claimed,
            "delivered": metrics.delivered,
            "deferred": metrics.deferred,
            "dead_lettered": metrics.dead_lettered,
        },
    )
    return metrics


def deliver_pending(db: Session, settings: Settings, *, limit: int = 20) -> int:
    return deliver_pending_with_metrics(db, settings, limit=limit).delivered


def retry_failed(db: Session, *, message_id: str | None = None, limit: int = 20) -> int:
    """Move dead-lettered messages back to the pending queue for an operator-requested retry."""
    statement = (
        select(EmailOutbox)
        .join(EmailDeliveryState, EmailDeliveryState.message_id == EmailOutbox.id)
        .where(EmailOutbox.failed_at.is_not(None), EmailOutbox.sent_at.is_(None))
        .order_by(EmailOutbox.failed_at, EmailOutbox.id)
        .limit(limit)
    )
    if message_id:
        statement = statement.where(EmailOutbox.id == message_id)
    if db.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    messages = list(db.scalars(statement).all())
    now = utcnow()
    for message in messages:
        message.failed_at = None
        message.last_error = ""
        message.attempts = 0
        message.delivery_state.next_attempt_at = now
        message.delivery_state.claim_token = None
        message.delivery_state.claimed_at = None
    db.commit()
    log.info("Dead-lettered email requeued", extra={"requeued": len(messages)})
    return len(messages)


def _send_smtp(message: EmailOutbox, settings: Settings) -> None:
    email = EmailMessage()
    email["From"] = settings.email_from
    email["To"] = message.recipient
    email["Subject"] = message.subject
    email.set_content(message.body_text)
    email.add_alternative(_html_body(message, settings), subtype="html")
    client, use_starttls = _connect_smtp(settings)
    with client:
        if use_starttls:
            tls_context = ssl.create_default_context(cafile=settings.smtp_ca_bundle or None)
            client.starttls(context=tls_context)
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(email)


def _connect_smtp(settings: Settings) -> tuple[smtplib.SMTP, bool]:
    try:
        return (
            smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20),
            settings.smtp_starttls,
        )
    except (ConnectionRefusedError, smtplib.SMTPConnectError):
        if (
            not settings.smtp_allow_legacy_port25_fallback
            or settings.smtp_username
            or (settings.smtp_port == 25 and not settings.smtp_starttls)
        ):
            raise
        log.warning(
            "Primary SMTP connection failed; using configured unauthenticated port 25 fallback",
            extra={"smtp_host": settings.smtp_host},
        )
        return smtplib.SMTP(settings.smtp_host, 25, timeout=20), False


def _html_body(message: EmailOutbox, settings: Settings) -> str:
    """Build the broadly compatible text+HTML shape used by the reference app."""
    brand = escape(settings.app_name)
    title = escape(message.subject)
    body = escape(message.body_text).replace("\n", "<br>\n")
    match = _URL_PATTERN.search(message.body_text)
    action = ""
    if match:
        raw_url = match.group(0).rstrip(".,;)")
        url = escape(raw_url, quote=True)
        action = (
            '<p style="margin:20px 0">'
            f'<a href="{url}" style="display:inline-block;padding:11px 16px;'
            "background:#6579dd;color:#fff;text-decoration:none;border-radius:8px;"
            'font-weight:700">Continue to Data Mover</a></p>'
        )
    return f"""<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title></head>
  <body style="margin:0;padding:0;background:#eef1f6;color:#172033;font-family:Arial,Helvetica,sans-serif">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="padding:28px 12px;background:#eef1f6">
      <tr><td align="center">
        <table role="presentation" cellpadding="0" cellspacing="0" width="560" style="width:560px;max-width:100%;background:#fff;border:1px solid #d8deea;border-radius:12px">
          <tr><td style="padding:18px 22px;border-bottom:1px solid #e5e9f1;font-weight:700;color:#25334d">{brand}</td></tr>
          <tr><td style="padding:22px;font-size:14px;line-height:1.55">
            <h1 style="margin:0 0 14px;font-size:20px;line-height:1.3;color:#172033">{title}</h1>
            <div>{body}</div>{action}
          </td></tr>
          <tr><td style="padding:14px 22px;border-top:1px solid #e5e9f1;font-size:12px;line-height:1.5;color:#667085">If you did not request this message, contact the service desk.</td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
