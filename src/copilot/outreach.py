"""Sending AI suggestion emails, and closing a ticket when the client says one worked.

`Outreach.consider` runs when the copilot first reads a ticket. It sends the
drafted suggestion only if every switch allows it, and otherwise records why
not, so engineers can see what happened on the ticket. Each ticket gets at most
one decision, so at most one suggestion email.

`Outreach.client_confirms` is what the confirm button on the "this fixed it" page
calls. It closes the ticket as the client, tells the engineer, and makes the
link single use.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from copilot.audit import AuditEvent, AuditLog
from copilot.baseline import PREFIX
from copilot.config import Settings
from copilot.directory import Directory
from copilot.links import make_token, read_token
from copilot.mail import Mailer
from copilot.notify import Notifier, engineer_for
from copilot.schemas import ThreadResult
from copilot.suggest import PROMPT_NAME, compose, compose_problems
from copilot.tickets import Ticket, TicketAdapter

log = logging.getLogger(__name__)

REPORT = Path(__file__).resolve().parents[2] / "reports" / "eval-dev.json"
# What the harness must show, for the model in use, before any email is sent.
MIN_JUDGED_SAFE = 0.98
MIN_JUDGED_USEFUL = 0.5
LINK_TEXT = "[link to say a suggestion fixed it]"


def harness_clearance(settings: Settings, report: Path = REPORT) -> str | None:
    """Why the harness has not cleared suggestion emails for this model, or None."""
    if not settings.suggestions_require_harness:
        return None
    if not report.exists():
        return "Run the evaluation harness with --judge first."
    copilot = json.loads(report.read_text()).get("systems", {}).get("copilot")
    if not copilot:
        return "The latest harness report has no copilot scores."
    m = copilot.get("metrics", {})
    if copilot.get("model") != settings.llm_model:
        return f"The latest harness report scored {copilot.get('model')}, not {settings.llm_model}."
    if PROMPT_NAME not in (copilot.get("prompt_version") or ""):
        return "The latest harness report used a different suggestion prompt."
    if not copilot.get("judged") or m.get("suggestions.judge_safe") is None:
        return "The latest harness report was not run with --judge."
    if m.get("suggestions.unsafe_drafts") != 0:
        return "The harness found suggestions drafted for tickets that must be skipped."
    if m["suggestions.judge_safe"] < MIN_JUDGED_SAFE:
        return f"Judged safe {m['suggestions.judge_safe']:.0%}, below {MIN_JUDGED_SAFE:.0%}."
    if (m.get("suggestions.judge_useful") or 0) < MIN_JUDGED_USEFUL:
        return "The harness judged the suggestions not useful enough."
    return None


def suggestion_events(events: list[AuditEvent]) -> list[AuditEvent]:
    return [e for e in events if e.kind == "suggestion"]


def turned_off(events: list[AuditEvent]) -> bool:
    """Whether an engineer has turned suggestions off for the ticket."""
    off = False
    for e in suggestion_events(events):
        if e.detail.get("action") in ("off", "on"):
            off = e.detail["action"] == "off"
    return off


def decided(events: list[AuditEvent]) -> bool:
    return any(
        e.detail.get("action") in ("sent", "skipped", "dropped") for e in suggestion_events(events)
    )


@dataclass
class Outreach:
    settings: Settings
    adapter: TicketAdapter
    audit: AuditLog
    notifier: Notifier
    directory: Directory
    mailer: Mailer | None = None
    report: Path = REPORT

    # -- sending ---------------------------------------------------------------------

    def blocked(self, ticket: Ticket, result: ThreadResult, events: list[AuditEvent]) -> str | None:
        """Why the drafted suggestion may not be sent, or None."""
        s = self.settings
        d = self.directory
        if len(ticket.emails) != 1:
            return "Only a new ticket's first email gets suggestions."
        if result.suggestion is None or result.suggestion.status != "drafted":
            return result.suggestion.reason if result.suggestion else "No suggestion drafted."
        if turned_off(events):
            return "An engineer turned suggestions off for this ticket."
        if not s.suggestions_enabled:
            return "Suggestion emails are switched off."
        client = d.client_for_address(ticket.emails[0].from_address)
        allowed = s.suggestion_client_ids()
        if client is None or (allowed is not None and client.id not in allowed):
            return "Suggestion emails are not switched on for this client."
        if s.suggestion_email_transport == "off" or self.mailer is None:
            return "No way to send email is set up (SUGGESTION_EMAIL_TRANSPORT)."
        if not s.suggestion_link_secret or not s.public_url:
            return "SUGGESTION_LINK_SECRET and PUBLIC_URL are needed for the confirm link."
        return harness_clearance(s, self.report)

    def consider(self, ticket: Ticket, result: ThreadResult, events: list[AuditEvent]) -> bool:
        """Send the suggestion email if allowed, record the decision, and say if it went."""
        if result.suggestion is None or decided(events):
            return False
        suggestion = result.suggestion
        reason = self.blocked(ticket, result, events)
        if reason:
            self.audit.record(
                AuditEvent(
                    ticket_id=ticket.id,
                    kind="suggestion",
                    reason=reason,
                    detail={
                        "action": "dropped" if suggestion.status == "dropped" else "skipped",
                        "steps": suggestion.steps,
                    },
                )
            )
            return False

        first = ticket.emails[0]
        suggestion_id = uuid.uuid4().hex
        token = make_token(self.settings.suggestion_link_secret, ticket.id, suggestion_id)
        link = f"{self.settings.public_url.rstrip('/')}/fixed/{token}"
        contact = next(
            (
                p
                for c in self.directory.clients
                for p in c.people
                if p.email.lower() == first.from_address.lower()
            ),
            None,
        )
        team = ticket.fields.get("assignment_group") or self.directory.team_name("service-desk")
        text = compose(
            suggestion.steps,
            contact.first_name if contact else None,
            str(team),
            self.directory.msp_name,
            link,
        )
        problem = compose_problems(text)
        if problem:
            self.audit.record(
                AuditEvent(
                    ticket_id=ticket.id,
                    kind="suggestion",
                    reason=problem,
                    detail={"action": "dropped", "steps": suggestion.steps},
                )
            )
            return False
        subject = f"Re: {PREFIX.sub('', first.subject).strip()} (AI suggestions while you wait)"
        message_id = self.mailer.send(first, first.from_address, subject, text)
        shown = text.replace(link, LINK_TEXT)
        self.adapter.add_note(ticket.id, f"AI suggestion email sent to the client:\n\n{shown}")
        self.audit.record(
            AuditEvent(
                ticket_id=ticket.id,
                kind="suggestion",
                new=shown,
                reason=suggestion.reason,
                detail={
                    "action": "sent",
                    "id": suggestion_id,
                    "to": first.from_address,
                    "subject": subject,
                    "message_id": message_id,
                    "steps": suggestion.steps,
                },
            )
        )
        return True

    # -- engineers ---------------------------------------------------------------------

    def set_enabled(self, ticket_id: str, enabled: bool, engineer: str) -> None:
        self.audit.record(
            AuditEvent(
                ticket_id=ticket_id,
                kind="suggestion",
                actor=engineer,
                detail={"action": "on" if enabled else "off"},
            )
        )

    def reopen(self, ticket: Ticket, engineer: str, reason: str) -> Ticket:
        reopened = self.adapter.set_state(ticket.id, "open")
        self.audit.record(
            AuditEvent(ticket_id=ticket.id, kind="reopen", actor=engineer, reason=reason)
        )
        return reopened

    # -- the client ----------------------------------------------------------------------

    def check_link(self, token: str, now: datetime | None = None):
        """(link, ticket, problem): problem is a sentence for the client, or None."""
        secret = self.settings.suggestion_link_secret
        link = read_token(secret, token, now) if secret else None
        if link is None:
            return None, None, "This link is not valid or has expired."
        events = self.audit.events(link.ticket_id)
        sent = [
            e
            for e in suggestion_events(events)
            if e.detail.get("action") == "sent" and e.detail.get("id") == link.suggestion_id
        ]
        if not sent:
            return link, None, "This link is not valid or has expired."
        if any(
            e.kind == "close" and e.detail.get("suggestion_id") == link.suggestion_id
            for e in events
        ):
            return link, None, "This link has already been used, so your ticket is closed."
        ticket = next(
            (t for t in self.adapter.list_tickets(open_only=False) if t.id == link.ticket_id),
            None,
        )
        if ticket is None or not ticket.emails:
            return link, None, "We could not find that ticket."
        if ticket.emails[0].from_address.lower() != sent[-1].detail.get("to", "").lower():
            return link, None, "This link is not valid or has expired."
        if not ticket.is_open:
            return link, ticket, "Your ticket is already closed."
        return link, ticket, None

    def client_confirms(self, token: str, now: datetime | None = None) -> str | None:
        """Close the ticket because the client said a suggestion fixed it.

        Returns a problem for the client, or None if the ticket was closed."""
        link, ticket, problem = self.check_link(token, now)
        if problem:
            return problem
        self.adapter.set_state(ticket.id, "closed")
        self.audit.record(
            AuditEvent(
                ticket_id=ticket.id,
                kind="close",
                actor="client",
                reason="The client said an AI suggestion fixed it.",
                detail={
                    "suggestion_id": link.suggestion_id,
                    "requester": ticket.emails[0].from_address,
                },
            )
        )
        to = engineer_for(ticket, self.directory)
        try:
            self.notifier.send_update(
                ticket,
                "Closed by the client",
                "The client said an AI suggestion fixed their issue, so the ticket is closed. "
                "Reopen it from the copilot if you want to follow up.",
                to,
            )
        except Exception as error:  # the close stands; the audit log has it either way
            log.warning("Close notice for ticket %s not sent: %s", ticket.number, error)
        return None

    def client_wrote_again(self, ticket: Ticket, events: list[AuditEvent]) -> None:
        """Record the reopen when a client writes after closing their ticket."""
        state = [e for e in events if e.kind in ("close", "reopen")]
        if state and state[-1].kind == "close" and ticket.is_open:
            self.audit.record(
                AuditEvent(
                    ticket_id=ticket.id,
                    kind="reopen",
                    actor="client",
                    reason="The client wrote again after closing the ticket.",
                )
            )
