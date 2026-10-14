"""The audit log: every field change, note, alert, override and model run.

Events are only ever appended. Each one says who made it (the copilot, an
engineer by name, or the client), what changed, and why. Model runs also record
the prompt version, provider, model and cost. `PostgresAuditLog` is the real
store; `MemoryAuditLog` is for tests and the offline demo.

Engineer overrides lock a field: the copilot does not change it again until the
engineer reverts it. `locked_fields` reads that from the log, so it survives a
restart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Kind = Literal[
    "run",  # the copilot processed a ticket
    "field_change",
    "note",
    "alert",
    "override",
    "revert",
    "suggestion",
    "close",
    "reopen",
]

COPILOT = "copilot"


class AuditEvent(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ticket_id: str
    kind: Kind
    actor: str = COPILOT
    field: str | None = None
    old: Any = None
    new: Any = None
    reason: str | None = None
    prompt_version: str | None = None
    provider: str | None = None
    model: str | None = None
    cost_usd: float | None = None
    detail: dict[str, Any] = {}


class AuditLog(Protocol):
    def record(self, event: AuditEvent) -> AuditEvent: ...

    def events(self, ticket_id: str | None = None) -> list[AuditEvent]:
        """Events oldest first, for one ticket or all of them."""
        ...


def locked_fields(events: list[AuditEvent]) -> dict[str, Any]:
    """Fields an engineer has overridden and not reverted, with their values."""
    locked: dict[str, Any] = {}
    for event in events:
        if event.kind == "override" and event.field:
            locked[event.field] = event.new
        elif event.kind == "revert" and event.field:
            locked.pop(event.field, None)
    return locked


def latest_ai_values(events: list[AuditEvent]) -> dict[str, AuditEvent]:
    """The copilot's most recent proposal for each field."""
    out: dict[str, AuditEvent] = {}
    for event in events:
        if event.kind == "field_change" and event.actor == COPILOT and event.field:
            out[event.field] = event
    return out


@dataclass
class MemoryAuditLog:
    items: list[AuditEvent] = field(default_factory=list)

    def record(self, event: AuditEvent) -> AuditEvent:
        self.items.append(event)
        return event

    def events(self, ticket_id: str | None = None) -> list[AuditEvent]:
        return [e for e in self.items if ticket_id is None or e.ticket_id == ticket_id]


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL,
    ticket_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    field TEXT,
    old JSONB,
    new JSONB,
    reason TEXT,
    prompt_version TEXT,
    provider TEXT,
    model TEXT,
    cost_usd DOUBLE PRECISION,
    detail JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_log_ticket ON audit_log (ticket_id, id);
"""

COLUMNS = (
    "at",
    "ticket_id",
    "kind",
    "actor",
    "field",
    "old",
    "new",
    "reason",
    "prompt_version",
    "provider",
    "model",
    "cost_usd",
    "detail",
)
JSON_COLUMNS = ("old", "new", "detail")


class PostgresAuditLog:
    def __init__(self, dsn: str):
        import psycopg

        self.connect = lambda: psycopg.connect(dsn, autocommit=True)
        with self.connect() as conn:
            conn.execute(SCHEMA)

    @classmethod
    def from_settings(cls, settings=None) -> PostgresAuditLog:
        from copilot.config import get_settings

        s = settings or get_settings()
        return cls(s.database_url.get_secret_value())

    def record(self, event: AuditEvent) -> AuditEvent:
        row = event.model_dump(mode="json")
        values = [json.dumps(row[c]) if c in JSON_COLUMNS else getattr(event, c) for c in COLUMNS]
        placeholders = ", ".join(["%s"] * len(COLUMNS))
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO audit_log ({', '.join(COLUMNS)}) VALUES ({placeholders})", values
            )
        return event

    def events(self, ticket_id: str | None = None) -> list[AuditEvent]:
        query = f"SELECT {', '.join(COLUMNS)} FROM audit_log"
        params: list = []
        if ticket_id is not None:
            query += " WHERE ticket_id = %s"
            params.append(ticket_id)
        with self.connect() as conn:
            rows = conn.execute(query + " ORDER BY id", params).fetchall()
        return [AuditEvent(**dict(zip(COLUMNS, row, strict=True))) for row in rows]
