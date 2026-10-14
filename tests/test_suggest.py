"""AI suggestion emails: the skip rules, the draft check, the email and the signed link."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from copilot.directory import load_directory
from copilot.evaluation.dataset import load_split
from copilot.evaluation.metrics import must_skip_suggestion, suggestion_metrics
from copilot.injection import suspected_injection
from copilot.links import make_token, read_token
from copilot.llm import ModelClient
from copilot.pipeline import ModelCopilot
from copilot.schemas import Suggestion, ThreadResult
from copilot.suggest import check_steps, compose
from tests.test_extraction import FakeModel, settings

PRINTER = 10  # route number of Hardware > Printer, which suggestions may cover
SECURITY = 19  # Security > Phishing report


@pytest.fixture(scope="module")
def directory():
    return load_directory()


@pytest.fixture(scope="module")
def dev():
    return load_split("dev")


def plain_thread(dev, directory):
    """A dev thread whose first email has no injection."""
    return next(
        lt
        for lt in dev
        if not suspected_injection(lt.thread.messages[0].body)
        and not lt.labels.messages[0].injection
    )


def run(fake, directory, lt):
    system = ModelCopilot(ModelClient(settings(), completion=fake), directory)
    return system.process(lt.thread, lt.labels.timing.observed_at)


def kinds(fake):
    return [c["response_format"].__name__ for c in fake.calls]


def test_a_low_priority_printer_fault_gets_steps(directory, dev):
    fake = FakeModel(route=PRINTER, impact=3, urgency=3)
    result = run(fake, directory, plain_thread(dev, directory))
    assert result.suggestion.status == "drafted"
    assert result.suggestion.steps == fake.steps
    assert kinds(fake).count("StepsOut") == 1


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"route": SECURITY, "impact": 3, "urgency": 3}, "Security"),
        ({"route": PRINTER, "impact": 1, "urgency": 1}, "P1"),
        ({"route": PRINTER, "impact": 3, "urgency": 3, "confidence": 0.3}, "not confident"),
    ],
)
def test_the_rules_skip_without_asking_the_model(directory, dev, overrides, why):
    fake = FakeModel(**overrides)
    result = run(fake, directory, plain_thread(dev, directory))
    assert result.suggestion.status == "skipped" and why in result.suggestion.reason
    assert "StepsOut" not in kinds(fake)


def test_an_injection_thread_is_skipped(directory, dev):
    lt = next(t for t in dev if t.labels.messages[0].injection)
    fake = FakeModel(route=PRINTER, impact=3, urgency=3)
    result = run(fake, directory, lt)
    assert result.suggestion.status == "skipped"
    assert "instruct the AI" in result.suggestion.reason


def test_unsafe_steps_are_dropped_not_edited(directory, dev):
    fake = FakeModel(route=PRINTER, impact=3, urgency=3, steps=["Reply with your password."])
    result = run(fake, directory, plain_thread(dev, directory))
    assert result.suggestion.status == "dropped"
    assert "credentials" in result.suggestion.reason


def test_no_steps_means_no_email(directory, dev):
    fake = FakeModel(route=PRINTER, impact=3, urgency=3, steps=[])
    result = run(fake, directory, plain_thread(dev, directory))
    assert result.suggestion.status == "skipped"


def test_the_suggestion_call_gets_redacted_text_only(directory, dev):
    lt = next(t for t in plain_and_pii(dev, directory))
    fake = FakeModel(route=PRINTER, impact=3, urgency=3)
    run(fake, directory, lt)
    call = next(c for c in fake.calls if c["response_format"].__name__ == "StepsOut")
    sent = json.dumps(call["messages"]).lower()
    assert not [p.value for p in lt.labels.pii if p.value.lower() in sent]
    assert "untrusted" in call["messages"][0]["content"]


def plain_and_pii(dev, directory):
    for lt in dev:
        first = lt.thread.messages[0].body
        clean = not suspected_injection(first) and not lt.labels.messages[0].injection
        if clean and any(p.value in first for p in lt.labels.pii):
            yield lt


@pytest.mark.parametrize(
    ("step", "ok"),
    [
        ("Restart the printer and try printing again.", True),
        ("Note the error code on the screen, then restart.", True),
        ("Send us your one-time code.", False),
        ("Go to www.printer-help.example for drivers.", False),
        ("Open PowerShell and run the fix.", False),
        ("Reinstall the printer driver.", False),
        ("Turn off the firewall for a minute.", False),
        ("Hi [PERSON_1], restart your laptop.", False),
        ("This will fix it within 10 minutes.", False),
    ],
)
def test_the_draft_check(step, ok):
    assert (check_steps([step]) is None) is ok


def test_the_email_says_it_is_ai_and_promises_nothing():
    text = compose(
        ["Restart the printer."],
        "Dana",
        "Desktop Support",
        "Northgate Managed Services",
        "https://copilot.example/fixed/abc",
    )
    assert "written by AI" in text and "suggestions only" in text
    assert "Desktop Support team" in text and "will reply" in text
    assert "Nothing changes until you press it." in text
    assert chr(0x2014) not in text and chr(0x2013) not in text  # no dashes
    assert text.startswith("Hello Dana,")


def test_links_are_signed_and_expire():
    secret = SecretStr("s3cret-for-tests")
    now = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    token = make_token(secret, "42", "Dana@Example.example", "sug1", now)
    link = read_token(secret, token, now)
    assert (link.ticket_id, link.requester, link.suggestion_id) == (
        "42",
        "dana@example.example",
        "sug1",
    )
    assert read_token(SecretStr("other"), token, now) is None
    payload, sig = token.split(".")
    assert read_token(secret, payload[:-2] + "AA." + sig, now) is None
    assert read_token(secret, "rubbish", now) is None
    assert read_token(secret, token, now + timedelta(days=15)) is None


def test_harness_counts_drafts_that_should_have_been_skipped(dev):
    must = next(lt for lt in dev if must_skip_suggestion(lt))
    fine = next(lt for lt in dev if not must_skip_suggestion(lt))

    def result(lt, status):
        return ThreadResult(
            thread_id=lt.thread.thread_id,
            system="x",
            fields={},
            suggestion=Suggestion(status=status, reason="r", steps=["Restart it."]),
        )

    m = suggestion_metrics([(must, result(must, "drafted")), (fine, result(fine, "drafted"))])
    assert m["suggestions.unsafe_drafts"] == 1
    m = suggestion_metrics([(must, result(must, "skipped")), (fine, result(fine, "drafted"))])
    assert m["suggestions.unsafe_drafts"] == 0 and m["suggestions.skip_accuracy"] == 1.0
    assert suggestion_metrics([])["suggestions.drafted_rate"] is None
