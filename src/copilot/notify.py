"""Alerts to people: the assigned engineer, or the team lead on escalation.

`TeamsNotifier` posts an Adaptive Card to a Microsoft Teams channel through a
Workflows webhook (``TEAMS_WEBHOOK_URL``; in Teams, add the "Post to a channel
when a webhook request is received" workflow to the channel and copy its URL).
The card names who it is for. The URL is a secret: anyone holding it can post to
the channel, so it is a `SecretStr` and never logged.

Without a webhook, `LogNotifier` keeps alerts in memory so the screens still
show them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx
from pydantic import SecretStr

from copilot.directory import Directory
from copilot.schemas import Alert
from copilot.tickets import Ticket


@dataclass
class Recipient:
    name: str
    role: str  # "engineer", "team" or "team lead"


class Notifier(Protocol):
    def send(self, ticket: Ticket, alert: Alert, to: Recipient) -> None: ...


def recipient(ticket: Ticket, alert: Alert, directory: Directory) -> Recipient:
    """Who an alert is for, from the ticket's owner and assignment group."""
    team = next(
        (t for t in directory.teams if t.name == ticket.fields.get("assignment_group")), None
    )
    if team is None:
        team = next(t for t in directory.teams if t.key == "service-desk")
    if alert.notify == "team_lead":
        lead = next((e for e in directory.engineers if e.id == team.lead), None)
        return Recipient(lead.name if lead else f"{team.name} lead", "team lead")
    owner = next(
        (
            e
            for e in directory.engineers
            if ticket.owner and e.email.lower() == ticket.owner.lower()
        ),
        None,
    )
    if owner:
        return Recipient(owner.name, "engineer")
    return Recipient(team.name, "team")


def card(ticket: Ticket, alert: Alert, to: Recipient, link: str | None = None) -> dict:
    """The Teams message: an Adaptive Card with the ticket, the reason and who it is for."""
    heading = "Client chasing" if alert.kind == "chase" else "No response yet"
    if alert.notify == "team_lead":
        heading = f"Escalation: {heading.lower()}"
    body = [
        {"type": "TextBlock", "text": heading, "weight": "Bolder", "size": "Medium"},
        {"type": "TextBlock", "text": f"#{ticket.number} {ticket.title}", "wrap": True},
        {"type": "TextBlock", "text": alert.reason, "wrap": True, "isSubtle": True},
        {
            "type": "FactSet",
            "facts": [
                {"title": "For", "value": f"{to.name} ({to.role})"},
                {"title": "Priority", "value": str(ticket.fields.get("priority") or "unknown")},
                {"title": "Client", "value": str(ticket.fields.get("client_company") or "unknown")},
            ],
        },
    ]
    content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if link:
        content["actions"] = [{"type": "Action.OpenUrl", "title": "Open ticket", "url": link}]
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": content}
        ],
    }


class TeamsNotifier:
    def __init__(
        self,
        webhook_url: SecretStr,
        public_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self._url = webhook_url
        self.public_url = public_url.rstrip("/") if public_url else None
        self.http = httpx.Client(timeout=15, transport=transport)

    def send(self, ticket: Ticket, alert: Alert, to: Recipient) -> None:
        link = f"{self.public_url}/#ticket-{ticket.id}" if self.public_url else None
        response = self.http.post(self._url.get_secret_value(), json=card(ticket, alert, to, link))
        if response.status_code >= 400:
            # Say what failed without echoing the URL, which is the secret.
            raise RuntimeError(f"Teams webhook answered {response.status_code}")


@dataclass
class LogNotifier:
    sent: list[tuple[Ticket, Alert, Recipient]] = field(default_factory=list)

    def send(self, ticket: Ticket, alert: Alert, to: Recipient) -> None:
        self.sent.append((ticket, alert, to))
