"""Sending email (server mode): sign-up confirmation, password reset, login links and reminders.

CVMAKER_MAIL picks how: "console" (development: printed and written to data/outbox/), "smtp", "resend" or
"postmark". Messages are plain text plus a simple HTML version.
"""

import html
import json
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import parseaddr

import httpx

from . import settings


class MailError(RuntimeError):
    pass


def _html(text: str) -> str:
    body = html.escape(text).replace("\n", "<br>")
    for word in body.split():
        if word.startswith(("http://", "https://")):
            body = body.replace(word, f'<a href="{word}">{word}</a>', 1)
    return f'<div style="font-family:system-ui,sans-serif;font-size:15px;line-height:1.5;color:#222">{body}</div>'


def send(to: str, subject: str, text: str) -> None:
    backend = settings.MAIL_BACKEND
    if backend == "console":
        outbox = settings.DATA_ROOT / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        record = {"to": to, "subject": subject, "text": text, "at": time.time()}
        (outbox / f"{int(time.time() * 1000)}.json").write_text(json.dumps(record, indent=2))
        print(f"--- email to {to}: {subject}\n{text}\n---")
        return
    if backend == "smtp":
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.MAIL_FROM, to, subject
        msg.set_content(text)
        msg.add_alternative(_html(text), subtype="html")
        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                if settings.SMTP_USER:
                    s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                s.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            raise MailError(f"Could not send email: {e}") from e
        return
    if backend == "resend":
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={"from": settings.MAIL_FROM, "to": [to], "subject": subject, "text": text, "html": _html(text)},
            timeout=20,
        )
    elif backend == "postmark":
        r = httpx.post(
            "https://api.postmarkapp.com/email",
            headers={"X-Postmark-Server-Token": settings.POSTMARK_TOKEN, "Accept": "application/json"},
            json={"From": settings.MAIL_FROM, "To": to, "Subject": subject, "TextBody": text, "HtmlBody": _html(text),
                  "MessageStream": "outbound"},
            timeout=20,
        )
    else:
        raise MailError(f"Unknown CVMAKER_MAIL backend {backend!r}")
    if r.status_code >= 300:
        raise MailError(f"Could not send email ({backend} answered {r.status_code}: {r.text[:200]})")


def link(path: str) -> str:
    return f"{settings.PUBLIC_URL}{path}"


def sender_name() -> str:
    return parseaddr(settings.MAIL_FROM)[0] or "CVMaker"
