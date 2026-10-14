"""Sending the one kind of email the copilot sends: the labelled suggestion email.

Sending is off unless ``SUGGESTION_EMAIL_TRANSPORT=smtp``. Locally the SMTP
server is Mailpit, which catches everything. For real recipients, point
``SMTP_HOST`` at a relay and set ``SMTP_USERNAME``, ``SMTP_PASSWORD`` and
``SMTP_STARTTLS``.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

from pydantic import SecretStr

from copilot.schemas import Email

HEADER = "X-Northgate-Copilot"


class Mailer(Protocol):
    def send(self, reply_to: Email, to: str, subject: str, text: str) -> str:
        """Send a reply to a client's email and return its Message-ID."""
        ...


def message(reply_to: Email, sender: str, sender_name: str, to: str, subject: str, text: str):
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    msg["In-Reply-To"] = reply_to.message_id
    msg["References"] = reply_to.message_id
    # Marks the email as written by AI for anyone filtering mail, and stops
    # Zammad and the copilot treating it as a client's email.
    msg[HEADER] = "ai-suggestion"
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text)
    return msg


@dataclass
class SmtpMailer:
    host: str
    port: int
    sender: str
    sender_name: str
    username: str | None = None
    password: SecretStr | None = None
    starttls: bool = False

    def send(self, reply_to: Email, to: str, subject: str, text: str) -> str:
        msg = message(reply_to, self.sender, self.sender_name, to, subject, text)
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password.get_secret_value())
            smtp.send_message(msg)
        return msg["Message-ID"]


@dataclass
class OutboxMailer:
    """Keeps emails instead of sending them, for tests and the demo."""

    sender: str = "support@northgate-ms.example"
    sent: list[EmailMessage] = field(default_factory=list)

    def send(self, reply_to: Email, to: str, subject: str, text: str) -> str:
        msg = message(reply_to, self.sender, "Service desk", to, subject, text)
        self.sent.append(msg)
        return msg["Message-ID"]
