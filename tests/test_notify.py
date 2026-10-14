"""Teams alerts: who they are for, what the card says, and that the URL stays secret."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from copilot.directory import load_directory
from copilot.notify import TeamsNotifier, card, recipient
from copilot.schemas import Alert
from copilot.tickets import Ticket

HOOK = "https://example.webhook.office.example/workflows/secret-signature-abc"


@pytest.fixture(scope="module")
def directory():
    return load_directory()


def ticket(directory, owner=None, group=None):
    return Ticket(
        id="7",
        number="55007",
        title="Printer jammed",
        state="open",
        owner=owner,
        fields={"priority": "P3", "client_company": "Harbourline", "assignment_group": group},
    )


def alert(notify="engineer", kind="chase"):
    return Alert(kind=kind, message_index=1, at=datetime.now(UTC), notify=notify, reason="Chasing.")


def test_recipient_is_the_owner_then_the_team_then_the_lead(directory):
    engineer = directory.engineers[0]
    team = next(t for t in directory.teams if t.key == engineer.group)
    lead = next(e for e in directory.engineers if e.id == team.lead)
    assert recipient(ticket(directory, engineer.email), alert(), directory).name == engineer.name
    unowned = recipient(ticket(directory, group=team.name), alert(), directory)
    assert (unowned.name, unowned.role) == (team.name, "team")
    escalated = recipient(ticket(directory, group=team.name), alert("team_lead"), directory)
    assert (escalated.name, escalated.role) == (lead.name, "team lead")


def test_card_names_the_ticket_reason_and_person(directory):
    to = recipient(ticket(directory), alert("team_lead", "silence"), directory)
    message = card(
        ticket(directory), alert("team_lead", "silence"), to, "https://copilot.example/#ticket-7"
    )
    content = message["attachments"][0]["content"]
    text = json.dumps(content)
    assert "Escalation: no response yet" in text
    assert "#55007 Printer jammed" in text and "Chasing." in text and to.name in text
    assert content["actions"][0]["url"].endswith("#ticket-7")


def test_teams_posts_the_card_and_keeps_the_url_out_of_errors(directory):
    posted = []

    def ok(request):
        posted.append(request)
        return httpx.Response(202)

    notifier = TeamsNotifier(SecretStr(HOOK), transport=httpx.MockTransport(ok))
    notifier.send(ticket(directory), alert(), recipient(ticket(directory), alert(), directory))
    assert str(posted[0].url) == HOOK
    assert json.loads(posted[0].content)["type"] == "message"
    assert "secret-signature" not in repr(notifier.__dict__)

    failing = TeamsNotifier(
        SecretStr(HOOK), transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    with pytest.raises(RuntimeError) as error:
        failing.send(ticket(directory), alert(), recipient(ticket(directory), alert(), directory))
    assert "secret-signature" not in str(error.value)
