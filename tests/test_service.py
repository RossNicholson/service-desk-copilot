"""The copilot in service: check-ins, overrides, alerts and the audit trail.

Uses the memory adapter, the memory audit log and the rules baseline, so it
needs no stack and no model.
"""

from datetime import timedelta

import pytest

from copilot.audit import MemoryAuditLog, locked_fields
from copilot.baseline import BaselineCopilot
from copilot.chasing import sent_at
from copilot.config import Settings
from copilot.directory import load_directory
from copilot.evaluation.dataset import load_split
from copilot.notify import LogNotifier
from copilot.scheduler import start
from copilot.service import CopilotService, note_text
from copilot.tickets import MemoryAdapter


@pytest.fixture(scope="module")
def directory():
    return load_directory()


@pytest.fixture(scope="module")
def dev():
    return load_split("dev")


def silent_thread(dev, directory):
    support = directory.support_address
    return next(
        lt
        for lt in dev
        if lt.labels.timing.breach_at and all(e.from_address != support for e in lt.thread.messages)
    )


def chased_thread(dev, directory):
    """A thread with a chase the desk never answered."""
    support = directory.support_address
    for lt in dev:
        msgs = lt.thread.messages
        for alert in lt.labels.expected_alerts:
            if alert.kind == "chase" and all(
                e.from_address != support for e in msgs[alert.message_index + 1 :]
            ):
                return lt
    pytest.skip("no unanswered chase in the dev split")


class Counting(BaselineCopilot):
    runs = 0

    def process(self, thread, now):
        self.runs += 1
        return super().process(thread, now)


def setup(directory, emails, notifier=None):
    adapter = MemoryAdapter()
    ticket = adapter.create_ticket(emails[0])
    for email in emails[1:]:
        adapter.receive(ticket.id, email, directory.support_address)
    service = CopilotService(
        Counting(directory), adapter, MemoryAuditLog(), notifier or LogNotifier(), directory
    )
    return service, adapter, ticket.id


def test_a_check_in_fills_the_ticket_and_logs_why(directory, dev):
    lt = dev[0]
    service, adapter, tid = setup(directory, lt.thread.messages)
    report = service.check_in(lt.labels.timing.observed_at)
    assert report.processed == [tid]
    ticket = adapter.list_tickets()[0]
    assert ticket.fields["client_company"] == lt.labels.fields.client_company
    assert ticket.notes and ticket.notes[0].startswith("Service Desk Copilot note")
    kinds = [e.kind for e in service.audit.events(tid)]
    assert kinds.count("run") == 1 and "note" in kinds and "field_change" in kinds
    change = next(e for e in service.audit.events(tid) if e.field == "client_company")
    assert change.actor == "copilot" and change.reason
    run = next(e for e in service.audit.events(tid) if e.kind == "run")
    assert run.detail["emails"] == len(lt.thread.messages)


def test_only_new_emails_trigger_processing(directory, dev):
    lt = next(t for t in dev if len(t.thread.messages) > 1)
    first, *rest = lt.thread.messages
    service, adapter, tid = setup(directory, [first])
    now = lt.labels.timing.observed_at
    service.check_in(now)
    service.check_in(now)
    assert service.system.runs == 1
    adapter.receive(tid, rest[0], directory.support_address)
    assert service.check_in(now).processed == [tid]
    assert service.system.runs == 2


def test_an_override_locks_the_field_until_reverted(directory, dev):
    lt = next(t for t in dev if len(t.thread.messages) > 1)
    first, *rest = lt.thread.messages
    service, adapter, tid = setup(directory, [first])
    now = lt.labels.timing.observed_at
    service.check_in(now)
    ai = adapter.list_tickets()[0].fields["category"]
    service.override(tid, "category", "Engineer's choice", "Sam Engineer")
    assert locked_fields(service.audit.events(tid)) == {"category": "Engineer's choice"}
    adapter.receive(tid, rest[0], directory.support_address)
    service.check_in(now)
    assert adapter.list_tickets()[0].fields["category"] == "Engineer's choice"
    service.revert(tid, "category", "Sam Engineer")
    assert adapter.list_tickets()[0].fields["category"] == ai
    by = [(e.kind, e.actor) for e in service.audit.events(tid) if e.kind in ("override", "revert")]
    assert by == [("override", "Sam Engineer"), ("revert", "Sam Engineer")]
    with pytest.raises(LookupError):
        service.revert(tid, "category", "Sam Engineer")


def test_silence_alerts_once_then_escalates(directory, dev):
    lt = silent_thread(dev, directory)
    service, adapter, _tid = setup(directory, lt.thread.messages)
    start_at = sent_at(lt.thread.messages[0])
    service.check_in(start_at)
    priority = adapter.list_tickets()[0].fields["priority"]
    target = directory.response_targets_minutes[str(priority)]
    breach = start_at + timedelta(minutes=target)

    def silence(at):
        return [a for _, a in service.check_in(at).alerts if a.kind == "silence"]

    assert silence(breach - timedelta(minutes=1)) == []
    assert [a.notify for a in silence(breach + timedelta(minutes=1))] == ["engineer"]
    assert silence(breach + timedelta(minutes=2)) == []
    assert [a.notify for a in silence(start_at + timedelta(minutes=2 * target))] == ["team_lead"]
    sent = [(a.notify, to.role) for _, a, to in service.notifier.sent if a.kind == "silence"]
    assert sent == [("engineer", "team"), ("team_lead", "team lead")]


def test_an_engineers_priority_sets_the_target(directory, dev):
    lt = silent_thread(dev, directory)
    service, _adapter, tid = setup(directory, lt.thread.messages)
    start_at = sent_at(lt.thread.messages[0])
    service.check_in(start_at)
    service.override(tid, "priority", "P1", "Sam Engineer")
    p1 = directory.response_targets_minutes["P1"]
    alerts = service.check_in(start_at + timedelta(minutes=p1 + 1)).alerts
    assert [a.kind for _, a in alerts] == ["silence"]


def test_chases_go_to_the_owner_by_name(directory, dev):
    lt = chased_thread(dev, directory)
    service, adapter, tid = setup(directory, lt.thread.messages)
    engineer = directory.engineers[0]
    adapter.assign(tid, engineer.email)
    service.check_in(lt.labels.timing.observed_at)
    chases = [(a, to) for _, a, to in service.notifier.sent if a.kind == "chase"]
    assert chases
    assert any(to.name == engineer.name and to.role == "engineer" for _, to in chases)


def test_a_failed_alert_is_retried_and_not_logged_as_sent(directory, dev):
    class Flaky(LogNotifier):
        fail = True

        def send(self, ticket, alert, to):
            if self.fail:
                raise RuntimeError("Teams webhook answered 500")
            super().send(ticket, alert, to)

    lt = silent_thread(dev, directory)
    notifier = Flaky()
    service, _, tid = setup(directory, lt.thread.messages, notifier)
    late = lt.labels.timing.observed_at + timedelta(days=3)
    assert service.check_in(late).alerts == []
    assert not [e for e in service.audit.events(tid) if e.kind == "alert"]
    notifier.fail = False
    assert service.check_in(late).alerts


def test_a_restart_remembers_what_was_seen_and_sent(directory, dev):
    lt = silent_thread(dev, directory)
    service, adapter, _tid = setup(directory, lt.thread.messages)
    late = lt.labels.timing.observed_at + timedelta(days=3)
    assert service.check_in(late).alerts
    again = CopilotService(Counting(directory), adapter, service.audit, LogNotifier(), directory)
    report = again.check_in(late)
    assert report.processed == [] and report.alerts == []


def test_notes_are_added_only_when_they_change(directory, dev):
    lt = dev[0]
    service, adapter, _tid = setup(directory, lt.thread.messages)
    service.check_in(lt.labels.timing.observed_at)
    # Forget the run so the thread is processed again with the same emails.
    service.audit.items = [e for e in service.audit.items if e.kind != "run"]
    service.check_in(lt.labels.timing.observed_at)
    assert len(adapter.list_tickets()[0].notes) == 1


def test_note_text_lists_only_what_is_there(directory, dev):
    lt = dev[0]
    result = BaselineCopilot(directory).process(lt.thread, lt.labels.timing.observed_at)
    text = note_text(result.notes[-1].model_copy(update={"tried": [], "open_questions": ["Who?"]}))
    assert "Already tried" not in text
    assert "Open questions:\n- Who?" in text


def test_the_scheduler_runs_the_check_in_at_the_configured_interval(directory, dev):
    service, _, _ = setup(directory, dev[0].thread.messages)
    scheduler = start(service, Settings(_env_file=None, checkin_interval_minutes=7))
    try:
        assert scheduler.get_job("checkin").trigger.interval == timedelta(minutes=7)
    finally:
        scheduler.shutdown(wait=True)
