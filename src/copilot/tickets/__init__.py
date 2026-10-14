"""The ticket system, behind one small interface.

The copilot talks to a ticket system only through `TicketAdapter`, with four
operations: create a ticket, update its fields, add a note and list tickets.
`ZammadAdapter` is the real one; `MemoryAdapter` keeps tickets in memory for
tests and the offline demo. A ServiceNow adapter would implement the same four
operations and must pass the same shared test suite (`tests/adapter_contract.py`).
"""

from copilot.tickets.base import COPILOT_FIELDS, Ticket, TicketAdapter, TicketNotFound
from copilot.tickets.memory import MemoryAdapter

__all__ = ["COPILOT_FIELDS", "MemoryAdapter", "Ticket", "TicketAdapter", "TicketNotFound"]
