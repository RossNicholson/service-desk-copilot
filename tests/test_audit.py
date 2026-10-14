"""The audit log: the memory store, Postgres (with -m compose), and the override rules."""

import uuid

import pytest

from copilot.audit import AuditEvent, MemoryAuditLog, PostgresAuditLog, locked_fields


class AuditContract:
    def test_events_come_back_in_order_with_their_details(self, log):
        tid = uuid.uuid4().hex
        log.record(AuditEvent(ticket_id=tid, kind="field_change", field="impact", old=None, new=2))
        log.record(
            AuditEvent(
                ticket_id=tid,
                kind="run",
                prompt_version="extract_fields.v1",
                provider="anthropic",
                model="anthropic/claude-haiku-5-5",
                cost_usd=0.0012,
                detail={"emails": 3, "fields": {"impact": {"value": 2}}},
            )
        )
        log.record(AuditEvent(ticket_id="someone-else", kind="note", new="x"))
        events = log.events(tid)
        assert [e.kind for e in events] == ["field_change", "run"]
        assert events[0].new == 2 and events[0].old is None
        assert events[1].detail == {"emails": 3, "fields": {"impact": {"value": 2}}}
        assert events[1].cost_usd == pytest.approx(0.0012)
        assert events[1].model == "anthropic/claude-haiku-5-5"
        assert len(log.events()) >= 3


class TestMemoryAudit(AuditContract):
    @pytest.fixture
    def log(self):
        return MemoryAuditLog()


@pytest.mark.compose
class TestPostgresAudit(AuditContract):
    @pytest.fixture
    def log(self):
        from scripts.bootstrap_zammad import ROOT, load_env

        env = load_env(ROOT / ".env")
        return PostgresAuditLog(env["DATABASE_URL"])


def test_overrides_lock_until_reverted():
    def ev(kind, field, new=None):
        return AuditEvent(ticket_id="1", kind=kind, actor="Sam", field=field, new=new)

    events = [
        ev("override", "impact", 1),
        ev("override", "category", "Hardware"),
        ev("revert", "impact"),
        ev("override", "category", "Software"),
    ]
    assert locked_fields(events) == {"category": "Software"}
