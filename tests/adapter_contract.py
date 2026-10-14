"""The shared test suite every ticket system adapter must pass.

Subclass `AdapterContract` with a fixture called `adapter`, as
`tests/test_adapters.py` does for the memory adapter, a fake Zammad and (with
`-m compose`) the real Zammad in the compose stack.
"""

import uuid
from datetime import UTC, datetime
from email.utils import format_datetime

import pytest

from copilot.schemas import Email
from copilot.tickets import COPILOT_FIELDS, TicketNotFound

CLIENT = "dana.example@harbourline-bs.example"
SUPPORT = "support@northgate-ms.example"


def first_email(subject: str | None = None) -> Email:
    tag = uuid.uuid4().hex[:8]
    return Email(
        message_id=f"<{tag}@harbourline-bs.example>",
        from_name="Dana Example",
        from_address=CLIENT,
        to=[SUPPORT],
        date=format_datetime(datetime.now(UTC)),
        subject=subject or f"Printer jammed on the second floor {tag}",
        body="Hi,\n\nThe printer on the second floor is jammed again.\n\nThanks",
    )


class AdapterContract:
    def test_create_ticket_keeps_the_email(self, adapter):
        email = first_email()
        ticket = adapter.create_ticket(email)
        assert ticket.id and ticket.number
        assert ticket.title == email.subject
        assert ticket.is_open
        assert len(ticket.emails) == 1
        sent = ticket.emails[0]
        assert sent.from_address == CLIENT
        assert "jammed again" in sent.body
        assert sent.to == [SUPPORT]
        assert set(ticket.fields) <= set(COPILOT_FIELDS)
        assert all(v is None for v in ticket.fields.values())

    def test_update_fields_round_trips(self, adapter):
        ticket = adapter.create_ticket(first_email())
        updated = adapter.update_fields(
            ticket.id, {"category": "Hardware", "impact": 2, "priority": "P3"}
        )
        assert updated.fields["category"] == "Hardware"
        assert updated.fields["impact"] == 2
        assert updated.fields["priority"] == "P3"
        cleared = adapter.update_fields(ticket.id, {"category": None})
        assert cleared.fields["category"] is None
        assert cleared.fields["impact"] == 2

    def test_unknown_fields_are_refused(self, adapter):
        ticket = adapter.create_ticket(first_email())
        with pytest.raises(ValueError):
            adapter.update_fields(ticket.id, {"state": "closed"})

    def test_notes_are_kept_apart_from_emails(self, adapter):
        ticket = adapter.create_ticket(first_email())
        adapter.add_note(ticket.id, "Reported: the printer is jammed.")
        listed = next(t for t in adapter.list_tickets() if t.id == ticket.id)
        assert listed.notes == ["Reported: the printer is jammed."]
        assert len(listed.emails) == 1

    def test_list_tickets_returns_the_thread(self, adapter):
        ticket = adapter.create_ticket(first_email())
        listed = next(t for t in adapter.list_tickets() if t.id == ticket.id)
        assert listed.thread.thread_id == ticket.id
        assert listed.thread.messages[0].subject == ticket.title
        ids = [int(t.id) for t in adapter.list_tickets()]
        assert ids == sorted(ids)

    def test_a_missing_ticket_is_an_error(self, adapter):
        with pytest.raises(TicketNotFound):
            adapter.update_fields("999999999", {"category": "Hardware"})
