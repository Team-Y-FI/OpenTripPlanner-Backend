# app/core/mailer.py
from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from app.core.config import settings


class MailerError(RuntimeError):
    pass


class GmailSmtpMailer:
    async def send_text(self, *, to_email: str, subject: str, text: str) -> None:
        if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
            raise MailerError("SMTP_USERNAME/SMTP_PASSWORD가 설정되어 있지 않습니다.")

        msg = EmailMessage()
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(text)

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USERNAME,
                password=settings.SMTP_PASSWORD,
                start_tls=settings.SMTP_USE_TLS,   # 587 STARTTLS
                timeout=settings.SMTP_TIMEOUT,
            )
        except Exception as e:
            raise MailerError(f"SMTP send failed: {e}") from e
