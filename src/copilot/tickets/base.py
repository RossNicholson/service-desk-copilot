"""What every ticket system adapter provides."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from copilot.schemas import FIELD_NAMES, Email, Thread

# The ticket fields the copilot proposes and keeps up to date.
COPILOT_FIELDS = FIELD_NAMES

Value = str | int | None


class TicketNotFound(LookupError):
    pass


class Ticket(BaseModel):
    """A ticket as the copilot sees it: the emails, the copilot's fields and its notes."""

    id: str
    number: str
    title: str
    state: str
    owner: str | None = None  # the assigned engineer's email address, if any
    customer: str | None = None
    fields: dict[str, Value] = {}
    emails: list[Email] = []
    notes: list[str] = []

    @property
    def is_open(self) -> bool:
        return self.state not in ("closed", "merged", "removed")

    @property
    def thread(self) -> Thread:
        return Thread(thread_id=self.id, messages=self.emails)


class TicketAdapter(Protocol):
    def create_ticket(self, email: Email) -> Ticket:
        """Open a ticket from a client's first email."""
        ...

    def update_fields(self, ticket_id: str, fields: dict[str, Value]) -> Ticket:
        """Set some of the copilot's fields (any of `COPILOT_FIELDS`)."""
        ...

    def add_note(self, ticket_id: str, text: str) -> None:
        """Add an internal note, which clients never see."""
        ...

    def list_tickets(self, open_only: bool = True) -> list[Ticket]:
        """Tickets with their emails, oldest first."""
        ...

    def set_state(self, ticket_id: str, state: str) -> Ticket:
        """Close ("closed") or reopen ("open") a ticket. Only for a client confirming
        that an AI suggestion fixed their issue, or an engineer reopening it."""
        ...


STATES = ("open", "closed")


def check_state(state: str) -> None:
    if state not in STATES:
        raise ValueError(f"State must be one of {', '.join(STATES)}")


def check_fields(fields: dict[str, Value]) -> None:
    unknown = set(fields) - set(COPILOT_FIELDS)
    if unknown:
        raise ValueError(f"Not a copilot field: {', '.join(sorted(unknown))}")
