"""Concrete email transports for the application email port."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from html import escape
from re import search

from app.application.email import OutboundEmail
from app.config import Settings

log = logging.getLogger(__name__)
_URL_PATTERN = r"https?://[^\s<>]+"


class ConsoleEmailTransport:
    def send(self, message: OutboundEmail) -> None:
        cc = f"\nCC: {message.cc_recipient}" if message.cc_recipient else ""
        print(
            f"\n--- EMAIL TO {message.recipient}{cc} ---\n"
            f"{message.subject}\n\n{message.body_text}\n"
        )


class SmtpEmailTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: OutboundEmail) -> None:
        email = EmailMessage()
        email["From"] = self.settings.email_from
        email["To"] = message.recipient
        if message.cc_recipient:
            email["Cc"] = message.cc_recipient
        email["Subject"] = message.subject
        email.set_content(message.body_text)
        email.add_alternative(self._html_body(message), subtype="html")
        client = self._connect()
        with client:
            if self.settings.smtp_username:
                client.login(self.settings.smtp_username, self.settings.smtp_password)
            client.send_message(email)

    def _connect(self) -> smtplib.SMTP:
        try:
            return smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20)
        except (ConnectionRefusedError, smtplib.SMTPConnectError):
            if (
                not self.settings.smtp_allow_legacy_port25_fallback
                or self.settings.smtp_username
                or self.settings.smtp_port == 25
            ):
                raise
            log.warning(
                "Primary SMTP connection failed; using configured unauthenticated port 25 fallback",
                extra={"smtp_host": self.settings.smtp_host},
            )
            return smtplib.SMTP(self.settings.smtp_host, 25, timeout=20)

    def _html_body(self, message: OutboundEmail) -> str:
        brand = escape(self.settings.app_name)
        title = escape(message.subject)
        body = escape(message.body_text).replace("\n", "<br>\n")
        match = search(_URL_PATTERN, message.body_text)
        action = ""
        if match:
            raw_url = match.group(0).rstrip(".,;)")
            action = (
                '<p style="margin:20px 0">'
                f'<a href="{escape(raw_url, quote=True)}" style="display:inline-block;padding:11px 16px;'
                "background:#6579dd;color:#fff;text-decoration:none;border-radius:8px;"
                'font-weight:700">Continue to Data Mover</a></p>'
            )
        return (
            "<!doctype html><html><body>"
            f'<p style="font-weight:700">{brand}</p><h2>{title}</h2>'
            f"<div>{body}</div>{action}</body></html>"
        )
