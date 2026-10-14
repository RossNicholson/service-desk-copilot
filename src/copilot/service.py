"""The copilot in service: reads tickets, updates them, alerts people, logs everything.

`CopilotService.check_in` is what the scheduler runs every few minutes. For each
open ticket with emails it has not seen, it runs the copilot over the thread,
sets the fields an engineer has not overridden, adds a note when the note has
changed, and sends any new chase alerts. Every open ticket is also checked for
silence against the response target for its current priority (an engineer's
override included), escalating to the team lead at twice the target.

Everything it does goes in the audit log, which is also how it remembers what it
has seen and sent across restarts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from copilot.audit import (
    AuditEvent,
    AuditLog,
    latest_ai_values,
    locked_fields,
)
from copilot.baseline import silence_alert
from copilot.chasing import escalate_silence
from copilot.directory import Directory, load_directory
from copilot.notify import Notifier, recipient
from copilot.schemas import Alert, StructuredNote, Thread, ThreadResult
from copilot.tickets import Ticket, TicketAdapter, TicketNotFound

log = logging.getLogger(__name__)


class System(Protocol):
    name: str

    def process(self, thread: Thread, now: datetime) -> ThreadResult: ...


@dataclass
class CheckInReport:
    processed: list[str] = field(default_factory=list)
    alerts: list[tuple[str, Alert]] = field(default_factory=list)


def note_text(note: StructuredNote) -> str:
    """The note as an engineer reads it in the ticket system."""
    lines = ["Service Desk Copilot note (written by AI from the client's emails)", ""]

    def section(title: str, items: list[str] | str | None) -> None:
        if isinstance(items, str):
            items = [items] if items else []
        if items:
            lines.append(f"{title}:")
            lines.extend(f"- {item}" for item in items)

    section("Reported", note.reported)
    section("Who and what is affected", note.affected)
    section("Already tried", note.tried)
    section("Asking for", note.asks)
    section("Deadline or business impact", note.deadline_or_impact)
    section("Open questions", note.open_questions)
    return "\n".join(lines)


def alert_key(alert: Alert) -> str:
    return f"{alert.kind}:{alert.message_index}:{alert.notify}"


@dataclass
class CopilotService:
    system: System
    adapter: TicketAdapter
    audit: AuditLog
    notifier: Notifier
    directory: Directory = field(default_factory=load_directory)

    # -- the scheduled check-in -------------------------------------------------------

    def check_in(self, now: datetime | None = None) -> CheckInReport:
        now = now or datetime.now(UTC)
        report = CheckInReport()
        for ticket in self.adapter.list_tickets():
            if not ticket.emails:
                continue
            events = self.audit.events(ticket.id)
            runs = [e for e in events if e.kind == "run"]
            alerts: list[Alert] = []
            if not runs or runs[-1].detail.get("emails") != len(ticket.emails):
                result = self.system.process(ticket.thread, now)
                ticket = self._apply(ticket, result, events, now)
                report.processed.append(ticket.id)
                alerts += self._live_chases(ticket, result)
            alerts += self._silence(ticket, now)
            sent = {e.detail.get("key") for e in events if e.kind == "alert"}
            for alert in alerts:
                if alert_key(alert) in sent:
                    continue
                if self._send(ticket, alert):
                    report.alerts.append((ticket.id, alert))
        return report

    def _apply(
        self, ticket: Ticket, result: ThreadResult, events: list[AuditEvent], now: datetime
    ) -> Ticket:
        locked = locked_fields(events)
        previous = latest_ai_values(events)
        changed_at = {c.field: c.message_index for c in result.field_changes}
        updates: dict[str, Any] = {}
        for name, prediction in result.fields.items():
            if name in previous and previous[name].new == prediction.value:
                continue
            self.audit.record(
                AuditEvent(
                    at=now,
                    ticket_id=ticket.id,
                    kind="field_change",
                    field=name,
                    old=ticket.fields.get(name),
                    new=prediction.value,
                    reason=prediction.reason,
                    detail={
                        "confidence": prediction.confidence,
                        "needs_review": prediction.needs_review,
                        "message_index": changed_at.get(name),
                        "applied": name not in locked,
                    },
                )
            )
            if name not in locked and ticket.fields.get(name) != prediction.value:
                updates[name] = prediction.value
        if updates:
            ticket = self.adapter.update_fields(ticket.id, updates)

        if result.notes:
            text = note_text(result.notes[-1])
            last = next((e for e in reversed(events) if e.kind == "note"), None)
            if last is None or last.new != text:
                self.adapter.add_note(ticket.id, text)
                self.audit.record(
                    AuditEvent(
                        at=now,
                        ticket_id=ticket.id,
                        kind="note",
                        new=text,
                        detail={"note": result.notes[-1].model_dump()},
                    )
                )

        usage = result.usage
        self.audit.record(
            AuditEvent(
                at=now,
                ticket_id=ticket.id,
                kind="run",
                reason=f"Read {len(ticket.emails)} emails with the {result.system} system.",
                prompt_version=usage.prompt_version,
                provider=usage.provider,
                model=usage.model,
                cost_usd=usage.cost_usd,
                detail={
                    "system": result.system,
                    "emails": len(ticket.emails),
                    "calls": usage.calls,
                    "fields": {n: p.model_dump() for n, p in result.fields.items()},
                    "chase_assessments": [a.model_dump() for a in result.chase_assessments],
                },
            )
        )
        return ticket

    def _live_chases(self, ticket: Ticket, result: ThreadResult) -> list[Alert]:
        """Chase alerts the desk has not already answered with a reply."""
        support = self.directory.support_address
        out = []
        for alert in result.alerts:
            if alert.kind != "chase":
                continue
            later = ticket.emails[alert.message_index + 1 :]
            if not any(e.from_address == support for e in later):
                out.append(alert)
        return out

    def _silence(self, ticket: Ticket, now: datetime) -> list[Alert]:
        value = ticket.fields.get("priority")
        silence = silence_alert(ticket.thread, value, now, self.directory)
        if silence is None:
            return []
        target = self.directory.response_targets_minutes[str(value)]
        escalated = escalate_silence(silence, now, target)
        # The engineer hears first; the escalation is a second alert, not a replacement.
        return [silence] if escalated.notify == "engineer" else [silence, escalated]

    def _send(self, ticket: Ticket, alert: Alert) -> bool:
        to = recipient(ticket, alert, self.directory)
        try:
            self.notifier.send(ticket, alert, to)
        except Exception as error:  # a failed alert is retried at the next check-in
            log.warning("Alert for ticket %s not sent: %s", ticket.number, error)
            return False
        self.audit.record(
            AuditEvent(
                ticket_id=ticket.id,
                kind="alert",
                new=alert.kind,
                reason=alert.reason,
                detail={
                    "key": alert_key(alert),
                    "alert": alert.model_dump(mode="json"),
                    "to": {"name": to.name, "role": to.role},
                },
            )
        )
        return True

    # -- engineers stay in control --------------------------------------------------

    def override(self, ticket_id: str, name: str, value: Any, engineer: str) -> Ticket:
        """An engineer sets a field. The copilot leaves it alone until it is reverted."""
        current = self._ticket(ticket_id)
        ticket = self.adapter.update_fields(ticket_id, {name: value})
        self.audit.record(
            AuditEvent(
                ticket_id=ticket_id,
                kind="override",
                actor=engineer,
                field=name,
                old=current.fields.get(name),
                new=value,
            )
        )
        return ticket

    def revert(self, ticket_id: str, name: str, engineer: str) -> Ticket:
        """Undo an override: the field goes back to the copilot's latest value."""
        events = self.audit.events(ticket_id)
        if name not in locked_fields(events):
            raise LookupError("That field has not been overridden.")
        ai = latest_ai_values(events).get(name)
        value = ai.new if ai else None
        current = self._ticket(ticket_id)
        ticket = self.adapter.update_fields(ticket_id, {name: value})
        self.audit.record(
            AuditEvent(
                ticket_id=ticket_id,
                kind="revert",
                actor=engineer,
                field=name,
                old=current.fields.get(name),
                new=value,
            )
        )
        return ticket

    def _ticket(self, ticket_id: str) -> Ticket:
        ticket = next((t for t in self.adapter.list_tickets(False) if t.id == ticket_id), None)
        if ticket is None:
            raise TicketNotFound(ticket_id)
        return ticket
