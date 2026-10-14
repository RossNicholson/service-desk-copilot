"""The scheduled check-in, run directly with the rules baseline."""

from datetime import timedelta

import pytest

from copilot.baseline import BaselineCopilot
from copilot.chasing import sent_at
from copilot.config import Settings
from copilot.directory import load_directory
from copilot.evaluation.dataset import load_split
from copilot.scheduler import CheckIn, CollectingNotifier, ThreadSource, start


@pytest.fixture(scope="module")
def directory():
    return load_directory()


@pytest.fixture(scope="module")
def silent(directory):
    """A dev thread nobody at the desk ever answered."""
    for lt in load_split("dev"):
        support = directory.support_address
        if lt.labels.timing.breach_at and all(
            e.from_address != support for e in lt.thread.messages
        ):
            return lt
    pytest.skip("no silent thread in the dev split")


class Counting(BaselineCopilot):
    def __init__(self, directory):
        super().__init__(directory)
        self.runs = 0

    def process(self, thread, now):
        self.runs += 1
        return super().process(thread, now)


def checkin(directory, lt):
    system = Counting(directory)
    notifier = CollectingNotifier()
    job = CheckIn(system, ThreadSource([lt.thread]), notifier, directory)
    return job, system, notifier


def test_silence_alerts_once_then_escalates(directory, silent):
    job, system, notifier = checkin(directory, silent)
    first = silent.thread.messages[0]
    result = system.process(silent.thread, silent.labels.timing.observed_at)
    target = directory.response_targets_minutes[str(result.fields["priority"].value)]
    breach = sent_at(first) + timedelta(minutes=target)

    assert [
        a for _, a in job.run(breach - timedelta(minutes=1)).alerts if a.kind == "silence"
    ] == []
    alerts = [a for _, a in job.run(breach + timedelta(minutes=1)).alerts if a.kind == "silence"]
    assert [a.notify for a in alerts] == ["engineer"]
    assert job.run(breach + timedelta(minutes=2)).alerts == []  # sent once only
    late = job.run(sent_at(first) + timedelta(minutes=2 * target)).alerts
    assert [a.notify for _, a in late if a.kind == "silence"] == ["team_lead"]
    assert len([a for _, a in notifier.sent if a.kind == "silence"]) == 2


def test_only_tickets_with_new_emails_are_processed_again(directory, silent):
    job, system, _ = checkin(directory, silent)
    now = silent.labels.timing.observed_at
    assert job.run(now).processed == [silent.thread.thread_id]
    assert job.run(now).processed == []
    assert system.runs == 1
    reply = silent.thread.messages[0].model_copy(update={"message_id": "<new@test.example>"})
    job.source.threads = [
        silent.thread.model_copy(update={"messages": [*silent.thread.messages, reply]})
    ]
    assert job.run(now).processed == [silent.thread.thread_id]


def test_start_schedules_the_job_at_the_configured_interval(directory, silent):
    job, _, _ = checkin(directory, silent)
    scheduler = start(job, Settings(_env_file=None, checkin_interval_minutes=7))
    try:
        trigger = scheduler.get_job("checkin").trigger
        assert trigger.interval == timedelta(minutes=7)
    finally:
        scheduler.shutdown(wait=True)
