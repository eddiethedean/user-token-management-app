import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from access_registry.config import Settings
from access_registry.models import EmailDeliveryState, EmailOutbox, new_id, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryMetrics:
    claimed: int = 0
    delivered: int = 0
    deferred: int = 0
    dead_lettered: int = 0


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
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
        if settings.smtp_starttls:
            tls_context = ssl.create_default_context(cafile=settings.smtp_ca_bundle or None)
            client.starttls(context=tls_context)
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(email)
