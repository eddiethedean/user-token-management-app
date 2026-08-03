import smtplib
from datetime import timedelta

from sqlalchemy import select

from app.cli import create_admin, main
from app.config import get_settings
from app.database import SessionLocal
from app.models import EmailDeliveryState, EmailOutbox, User, utcnow
from app.security.passwords import PasswordService
from app.services.mailer import DeliveryMetrics, deliver_pending, queue_email, retry_failed


class RecordingSMTP:
    instances = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.credentials = None
        self.messages = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def send_message(self, message) -> None:
        self.messages.append(message)


def test_smtp_delivery_uses_tls_auth_and_marks_message_sent(client, monkeypatch) -> None:
    RecordingSMTP.instances.clear()
    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", RecordingSMTP)
    smtp_settings = get_settings().model_copy(
        update={
            "email_backend": "smtp",
            "smtp_host": "relay.example.gov",
            "smtp_port": 2525,
            "smtp_starttls": True,
            "smtp_username": "registry",
            "smtp_password": "relay-password",
            "email_from": "Registry <registry@example.gov>",
            "email_redact_sent_bodies": True,
        }
    )
    with SessionLocal() as db:
        queued = queue_email(db, "recipient@example.gov", "Security notice", "Message body")
        db.commit()
        message_id = queued.id
        assert deliver_pending(db, smtp_settings) == 1

    smtp = RecordingSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.timeout) == ("relay.example.gov", 2525, 20)
    assert smtp.started_tls
    assert smtp.credentials == ("registry", "relay-password")
    assert len(smtp.messages) == 1
    assert smtp.messages[0]["To"] == "recipient@example.gov"
    assert smtp.messages[0]["From"] == "Registry <registry@example.gov>"
    assert smtp.messages[0]["Subject"] == "Security notice"
    with SessionLocal() as db:
        stored = db.get(EmailOutbox, message_id)
        assert stored is not None
        assert stored.sent_at is not None
        assert stored.attempts == 1
        assert stored.body_text == "[redacted after delivery]"


def test_mail_failure_is_retried_then_quarantined(client, monkeypatch) -> None:
    def fail_delivery(message, settings) -> None:
        raise smtplib.SMTPException("relay unavailable")

    monkeypatch.setattr("app.services.mailer._send_smtp", fail_delivery)
    smtp_settings = get_settings().model_copy(
        update={"email_backend": "smtp", "smtp_host": "relay.example.gov"}
    )
    with SessionLocal() as db:
        queued = queue_email(db, "recipient@example.gov", "Notice", "Body")
        db.commit()
        message_id = queued.id
        for attempt in range(5):
            assert deliver_pending(db, smtp_settings) == 0
            if attempt < 4:
                state = db.get(EmailDeliveryState, message_id)
                assert state is not None
                state.next_attempt_at = utcnow() - timedelta(seconds=1)
                db.commit()
        assert deliver_pending(db, smtp_settings) == 0

    with SessionLocal() as db:
        stored = db.get(EmailOutbox, message_id)
        assert stored is not None
        assert stored.attempts == 5
        assert stored.failed_at is not None
        assert stored.sent_at is None
        assert stored.last_error == "relay unavailable"

    with SessionLocal() as db:
        assert retry_failed(db, message_id=message_id) == 1
        stored = db.get(EmailOutbox, message_id)
        assert stored is not None
        assert stored.failed_at is None
        assert stored.attempts == 0
        assert stored.last_error == ""
        assert stored.delivery_state.next_attempt_at <= utcnow()


def test_console_delivery_outputs_message_and_respects_limit(client, capsys) -> None:
    with SessionLocal() as db:
        queue_email(db, "first@example.gov", "First", "First body")
        queue_email(db, "second@example.gov", "Second", "Second body")
        db.commit()
        assert deliver_pending(db, get_settings(), limit=1) == 1
        remaining = db.scalars(select(EmailOutbox).where(EmailOutbox.sent_at.is_(None))).all()
        assert len(remaining) == 1
    output = capsys.readouterr().out
    assert "EMAIL TO first@example.gov" in output
    assert "First body" in output
    assert "Second body" not in output


def test_request_handlers_only_queue_email(client) -> None:
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "admin@example.gov"},
    )
    assert response.status_code == 202
    with SessionLocal() as db:
        message = db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.recipient == "admin@example.gov")
            .order_by(EmailOutbox.created_at.desc())
        )
        assert message is not None
        assert message.sent_at is None
        assert message.failed_at is None
        assert message.attempts == 0
        assert message.delivery_state.claim_token is None


def test_create_admin_validates_confirmation_and_password(client, monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.assert_schema_current", lambda: None)
    responses = iter(["one-password-value", "different-password"])
    monkeypatch.setattr("app.cli.getpass.getpass", lambda prompt: next(responses))
    assert create_admin("new.admin@example.gov") == 2
    assert "Passwords do not match" in capsys.readouterr().err

    responses = iter(["too-short", "too-short"])
    monkeypatch.setattr("app.cli.getpass.getpass", lambda prompt: next(responses))
    assert create_admin("new.admin@example.gov") == 2
    assert "at least 15" in capsys.readouterr().err


def test_create_admin_creates_verified_administrator(client, monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.assert_schema_current", lambda: None)
    password = "Harbor-Signal-73!North"
    responses = iter([password, password])
    monkeypatch.setattr("app.cli.getpass.getpass", lambda prompt: next(responses))
    assert create_admin("new.admin@example.gov") == 0
    assert "Administrator ready" in capsys.readouterr().out
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "new.admin@example.gov"))
        assert user is not None
        assert user.is_active
        assert user.email_verified_at is not None
        assert user.role_names == ["administrator"]
        assert PasswordService(get_settings()).verify(password, user.password_hash)


def test_cli_serve_passes_arguments_to_server(monkeypatch) -> None:
    called = {}

    def fake_run_server(*, host: str, port: int, reload: bool) -> None:
        called.update(host=host, port=port, reload=reload)

    monkeypatch.setattr("app.cli.run_server", fake_run_server)
    monkeypatch.setattr(
        "app.cli.sys.argv",
        ["utm", "serve", "--host", "0.0.0.0", "--port", "8050", "--reload"],
    )
    main()
    assert called == {"host": "0.0.0.0", "port": 8050, "reload": True}


def test_cli_email_worker_once_reports_delivery_metrics(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.assert_schema_current", lambda: None)
    monkeypatch.setattr(
        "app.cli.deliver_pending_with_metrics",
        lambda db, settings, limit: DeliveryMetrics(
            claimed=3,
            delivered=1,
            deferred=1,
            dead_lettered=1,
        ),
    )
    monkeypatch.setattr("app.cli.sys.argv", ["utm", "email-worker", "--once"])
    main()
    assert "claimed=3 delivered=1 deferred=1 dead_lettered=1" in capsys.readouterr().out


def test_cli_retry_email_requeues_dead_letters(monkeypatch, capsys) -> None:
    called = {}
    monkeypatch.setattr("app.cli.assert_schema_current", lambda: None)

    def fake_retry(db, *, message_id, limit):
        called.update(message_id=message_id, limit=limit)
        return 1

    monkeypatch.setattr("app.cli.retry_failed", fake_retry)
    monkeypatch.setattr(
        "app.cli.sys.argv",
        ["utm", "retry-email", "--message-id", "message-1", "--limit", "5"],
    )
    main()
    assert called == {"message_id": "message-1", "limit": 5}
    assert "Requeued 1 message(s)." in capsys.readouterr().out
