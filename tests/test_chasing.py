"""Chase detection and the escalation rules, with a fake model."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from copilot.chasing import ChaseDetector, chase_alerts, escalate_silence, is_resend
from copilot.directory import load_directory
from copilot.llm import ModelClient
from copilot.redaction import Redactor
from copilot.schemas import Alert, ChaseAssessment, Email, Thread
from tests.test_extraction import FakeModel, settings

START = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def directory():
    return load_directory()


def email(directory, minutes, body, desk=False):
    person = directory.clients[0].people[0]
    sender = directory.support_address if desk else person.email
    return Email(
        message_id=f"<{minutes}@test.example>",
        from_name="Service Desk" if desk else person.name,
        from_address=sender,
        to=[person.email if desk else directory.support_address],
        date=format_datetime(START + timedelta(minutes=minutes)),
        subject="Outlook keeps crashing",
        body=body,
    )


FIRST = (
    "Hi,\n\nOutlook crashes every time I open the shared calendar for the finance team. "
    "It started this morning after the update and nobody else seems affected.\n\nThanks"
)


def thread(directory, *later):
    return Thread(thread_id="t1", messages=[email(directory, 0, FIRST), *later])


def detector(directory, fake):
    return ChaseDetector(ModelClient(settings(), completion=fake), directory)


def test_a_resend_is_a_chase_without_a_model_call(directory):
    t = thread(directory, email(directory, 90, "Sending this again.\n\n" + FIRST))
    assert is_resend(t, 1, directory.support_address)
    fake = FakeModel()
    assessment, _ = detector(directory, fake).assess(t, 1, Redactor(directory=directory))
    assert assessment.is_chase and fake.calls == []


def test_no_rush_is_not_a_chase(directory):
    body = "No rush on this, just an update: it crashed again."
    t = thread(directory, email(directory, 60, body))
    assessment, frustration = detector(directory, FakeModel()).assess(
        t, 1, Redactor(directory=directory)
    )
    assert not assessment.is_chase and frustration == 0


def test_the_model_sees_redacted_text_and_waiting_times(directory):
    person = directory.clients[0].people[0]
    body = f"Any update please? Call me on 07700 900123.\n\n{person.name}"
    t = thread(directory, email(directory, 150, body))
    fake = FakeModel()
    assessment, _ = detector(directory, fake).assess(t, 1, Redactor(directory=directory))
    assert assessment.is_chase
    sent = fake.calls[0]["messages"][1]["content"]
    assert "07700 900123" not in sent and person.last_name not in sent
    assert "2 hours" in sent and "has not replied yet" in sent


def chase(index):
    return ChaseAssessment(message_index=index, is_chase=True, confidence=0.9, reason="Chasing.")


def test_a_second_chase_with_no_reply_escalates(directory):
    support = directory.support_address
    t = thread(
        directory, email(directory, 60, "Any update?"), email(directory, 120, "Any update now?")
    )
    alerts = chase_alerts(t, [(chase(1), 1), (chase(2), 1)], support)
    assert [a.notify for a in alerts] == ["engineer", "team_lead"]
    assert alerts[1].at == START + timedelta(minutes=120)


def test_a_reply_between_chases_resets_the_escalation(directory):
    support = directory.support_address
    t = thread(
        directory,
        email(directory, 60, "Any update?"),
        email(directory, 70, "Looking now.", desk=True),
        email(directory, 200, "Any update now?"),
    )
    alerts = chase_alerts(t, [(chase(1), 1), (chase(3), 1)], support)
    assert [a.notify for a in alerts] == ["engineer", "engineer"]


def test_anger_escalates_at_once(directory):
    t = thread(directory, email(directory, 60, "This is ridiculous, escalate it."))
    alerts = chase_alerts(t, [(chase(1), 3)], directory.support_address)
    assert alerts[0].notify == "team_lead"


def test_silence_escalates_at_twice_the_target():
    alert = Alert(kind="silence", at=START + timedelta(minutes=60), reason="No response.")
    assert escalate_silence(alert, START + timedelta(minutes=90), 60).notify == "engineer"
    assert escalate_silence(alert, START + timedelta(minutes=120), 60).notify == "team_lead"
    assert escalate_silence(None, START, 60) is None
