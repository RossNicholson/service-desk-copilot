"""Tickets kept in memory, for tests and the offline demo."""

from __future__ import annotations

from dataclasses import dataclass, field

from copilot.baseline import PREFIX
from copilot.schemas import Email
from copilot.tickets.base import Ticket, TicketNotFound, Value, check_fields, check_state


@dataclass
class MemoryAdapter:
    tickets: dict[str, Ticket] = field(default_factory=dict)
    first_number: int = 260001

    def create_ticket(self, email: Email) -> Ticket:
        number = self.first_number + len(self.tickets)
        ticket = Ticket(
            id=str(len(self.tickets) + 1),
            number=str(number),
            title=PREFIX.sub("", email.subject).strip(),
            state="new",
            customer=email.from_address,
            emails=[email],
        )
        self.tickets[ticket.id] = ticket
        return ticket.model_copy(deep=True)

    def update_fields(self, ticket_id: str, fields: dict[str, Value]) -> Ticket:
        check_fields(fields)
        ticket = self._get(ticket_id)
        ticket.fields.update(fields)
        return ticket.model_copy(deep=True)

    def add_note(self, ticket_id: str, text: str) -> None:
        self._get(ticket_id).notes.append(text)

    def list_tickets(self, open_only: bool = True) -> list[Ticket]:
        return [
            t.model_copy(deep=True) for t in self.tickets.values() if t.is_open or not open_only
        ]

    def set_state(self, ticket_id: str, state: str) -> Ticket:
        check_state(state)
        ticket = self._get(ticket_id)
        ticket.state = state
        return ticket.model_copy(deep=True)

    # Not part of the interface: what the ticket system does on its own.

    def receive(self, ticket_id: str, email: Email, support: str | None = None) -> None:
        """A follow-up email, or a reply from the desk, arriving on a ticket."""
        ticket = self._get(ticket_id)
        ticket.emails.append(email)
        if support and email.from_address == support:
            if ticket.state == "new":
                ticket.state = "open"
        elif ticket.state == "closed":
            ticket.state = "open"  # as Zammad does when a client writes again

    def assign(self, ticket_id: str, owner: str | None) -> None:
        self._get(ticket_id).owner = owner

    def _get(self, ticket_id: str) -> Ticket:
        try:
            return self.tickets[ticket_id]
        except KeyError:
            raise TicketNotFound(ticket_id) from None
