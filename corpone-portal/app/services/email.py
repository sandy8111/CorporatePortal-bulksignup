"""Outbound email: console (writes .eml to disk), SMTP, or AWS SES."""
import logging
import os
import smtplib
import uuid
from email.message import EmailMessage

import boto3

from app.config import get_settings

log = logging.getLogger(__name__)


def build_message(to: str, subject: str, text: str, html: str | None = None,
                  attachments: list[tuple[str, bytes, str]] | None = None) -> EmailMessage:
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for filename, data, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def send(msg: EmailMessage) -> None:
    settings = get_settings()
    if settings.email_backend == "ses":
        boto3.client("ses", region_name=settings.aws_region).send_raw_email(
            Source=settings.email_from,
            Destinations=[msg["To"]],
            RawMessage={"Data": msg.as_bytes()},
        )
    elif settings.email_backend == "smtp":
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    else:
        os.makedirs(settings.outbox_dir, exist_ok=True)
        path = os.path.join(settings.outbox_dir, f"{uuid.uuid4().hex}.eml")
        with open(path, "wb") as fh:
            fh.write(msg.as_bytes())
        log.info("Email to %s written to %s", msg["To"], path)
