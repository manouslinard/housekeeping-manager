"""Plain SMTP email (Gmail works with an App Password). Used to send cleaners their Telegram link."""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger(__name__)


class MailError(Exception):
    pass


class Mailer:
    def __init__(self, settings: Settings):
        self.s = settings

    @property
    def configured(self) -> bool:
        return bool(self.s.smtp_host and self.s.smtp_user and self.s.smtp_password)

    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        if not self.configured:
            raise MailError("Email is not configured (SMTP_USER / SMTP_PASSWORD in .env)")
        msg = EmailMessage()
        msg["From"] = self.s.mail_from or self.s.smtp_user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            await asyncio.to_thread(self._send, msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"Email failed: {type(exc).__name__}: {exc}") from exc
        log.info("email sent to %s: %s", to, subject)

    def _send(self, msg: EmailMessage) -> None:
        s = self.s
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20) as c:
                c.login(s.smtp_user, s.smtp_password)
                c.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as c:
                c.starttls()
                c.login(s.smtp_user, s.smtp_password)
                c.send_message(msg)
