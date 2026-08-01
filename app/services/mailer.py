import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import EmailOutbox, utcnow


def queue_email(db: Session, recipient: str, subject: str, body_text: str) -> EmailOutbox:
    message = EmailOutbox(recipient=recipient, subject=subject, body_text=body_text)
    db.add(message)
    return message


def deliver_pending(db: Session, settings: Settings, *, limit: int = 20) -> int:
    messages = db.scalars(
        select(EmailOutbox)
        .where(EmailOutbox.sent_at.is_(None), EmailOutbox.failed_at.is_(None))
        .order_by(EmailOutbox.created_at)
        .limit(limit)
    ).all()
    delivered = 0
    for message in messages:
        message.attempts += 1
        try:
            if settings.email_backend == "console":
                print(f"\n--- EMAIL TO {message.recipient} ---\n{message.subject}\n\n{message.body_text}\n")
            else:
                _send_smtp(message, settings)
            message.sent_at = utcnow()
            delivered += 1
        except (OSError, smtplib.SMTPException) as exc:
            message.last_error = str(exc)[:2000]
            if message.attempts >= 5:
                message.failed_at = utcnow()
    db.commit()
    return delivered


def _send_smtp(message: EmailOutbox, settings: Settings) -> None:
    email = EmailMessage()
    email["From"] = settings.email_from
    email["To"] = message.recipient
    email["Subject"] = message.subject
    email.set_content(message.body_text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
        if settings.smtp_starttls:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(email)

