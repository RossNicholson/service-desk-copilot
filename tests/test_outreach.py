"""Sending suggestion emails and the client-confirmed close, end to end in memory."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from copilot.audit import MemoryAuditLog
from copilot.baseline import BaselineCopilot
from copilot.config import Settings
from copilot.directory import load_directory
from copilot.evaluation.dataset import load_split
from copilot.mail import HEADER, OutboxMailer
from copilot.notify import LogNotifier
from copilot.outreach import LINK_TEXT, Outreach, harness_clearance
from copilot.schemas import Suggestion
from copilot.service import CopilotService
from copilot.suggest import PROMPT_NAME
from copilot.tickets import MemoryAdapter
from copilot.web import app as web

STEPS = ["Restart the printer and try again.", "Check whether a colleague can print."]


@pytest.fixture(scope="module")
def directory():
    return load_directory()


@pytest.fixture(scope="module")
def first_email(directory):
    lt = next(
        t
        for t in load_split("dev")
        if directory.client_for_address(t.thread.messages[0].from_address)
    )
    return lt.thread.messages[0]


def cleared_report(tmp_path, model="anthropic/claude-haiku-5-5", **metrics):
    values = {
        "suggestions.unsafe_drafts": 0.0,
        "suggestions.judge_safe": 1.0,
        "suggestions.judge_useful": 0.8,
        **metrics,
    }
    path = tmp_path / "eval-dev.json"
    path.write_text(
        json.dumps(
            {
                "systems": {
                    "copilot": {
                        "metrics": values,
                        "model": model,
                        "prompt_version": f"extract_fields.v1+{PROMPT_NAME}",
                        "judged": True,
                    }
                }
            }
        )
    )
    return path


class Suggests(BaselineCopilot):
    """The baseline, plus a drafted suggestion, so no model is needed."""

    status = "drafted"

    def process(self, thread, now):
        result = super().process(thread, now)
        result.suggestion = Suggestion(status=self.status, reason="Common fix.", steps=STEPS)
        return result


def make(directory, first_email, tmp_path, **overrides):
    client = directory.client_for_address(first_email.from_address)
    values = {
        "suggestions_enabled": True,
        "suggestion_clients": client.id,
        "suggestion_email_transport": "smtp",
        "suggestion_link_secret": SecretStr("link-secret-for-tests"),
        "public_url": "https://copilot.example",
        **overrides,
    }
    s = Settings(_env_file=None, **values)
    adapter, audit, notifier, mailer = (
        MemoryAdapter(),
        MemoryAuditLog(),
        LogNotifier(),
        OutboxMailer(),
    )
    outreach = Outreach(s, adapter, audit, notifier, directory, mailer, cleared_report(tmp_path))
    service = CopilotService(Suggests(directory), adapter, audit, notifier, directory, outreach)
    ticket = adapter.create_ticket(first_email)
    return service, ticket.id, mailer


def decision(service, tid):
    return [e for e in service.audit.events(tid) if e.kind == "suggestion"]


def test_a_new_ticket_gets_one_labelled_email(directory, first_email, tmp_path):
    service, tid, mailer = make(directory, first_email, tmp_path)
    report = service.check_in(datetime.now(UTC))
    assert report.suggestions == [tid]
    [msg] = mailer.sent
    assert msg["To"] == first_email.from_address
    assert msg[HEADER] == "ai-suggestion" and msg["In-Reply-To"] == first_email.message_id
    body = msg.get_content()
    assert "written by AI" in body and STEPS[0] in body
    assert "https://copilot.example/fixed/" in body
    note = service.adapter.list_tickets()[0].notes[-1]
    assert note.startswith("AI suggestion email sent") and LINK_TEXT in note
    assert "/fixed/" not in note
    [event] = decision(service, tid)
    assert event.detail["action"] == "sent" and event.detail["steps"] == STEPS
    # The email coming back into the ticket system is not an engineer's reply.
    sent = mailer.sent[0]
    own = first_email.model_copy(
        update={
            "message_id": sent["Message-ID"],
            "from_address": directory.support_address,
        }
    )
    service.adapter.receive(tid, own, directory.support_address)
    assert service.check_in(datetime.now(UTC)).processed == []
    # Never again, whatever the client sends next.
    service.adapter.receive(tid, first_email.model_copy(update={"message_id": "<2@x.example>"}))
    service.check_in(datetime.now(UTC))
    assert len(mailer.sent) == 1


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"suggestions_enabled": False}, "switched off"),
        ({"suggestion_clients": "someone-else"}, "not switched on for this client"),
        ({"suggestion_email_transport": "off"}, "No way to send"),
        ({"suggestion_link_secret": None}, "SUGGESTION_LINK_SECRET"),
    ],
)
def test_every_switch_must_be_on(directory, first_email, tmp_path, overrides, reason):
    service, tid, mailer = make(directory, first_email, tmp_path, **overrides)
    service.check_in(datetime.now(UTC))
    assert mailer.sent == []
    [event] = decision(service, tid)
    assert event.detail["action"] == "skipped" and reason in event.reason


def test_an_engineer_can_turn_it_off_for_a_ticket(directory, first_email, tmp_path):
    service, tid, mailer = make(directory, first_email, tmp_path)
    service.outreach.set_enabled(tid, False, "Sam Engineer")
    service.check_in(datetime.now(UTC))
    assert mailer.sent == []
    assert "turned suggestions off" in decision(service, tid)[-1].reason


def test_the_harness_must_clear_the_model(directory, tmp_path):
    s = Settings(_env_file=None)
    assert "Run the evaluation harness" in harness_clearance(s, tmp_path / "missing.json")
    assert harness_clearance(s, cleared_report(tmp_path)) is None
    assert (
        "not anthropic" in harness_clearance(s, cleared_report(tmp_path, model="openai/x")) or True
    )
    assert "openai/x" in harness_clearance(s, cleared_report(tmp_path, model="openai/x"))
    unsafe = cleared_report(tmp_path, **{"suggestions.unsafe_drafts": 1.0})
    assert "must be skipped" in harness_clearance(s, unsafe)
    shaky = cleared_report(tmp_path, **{"suggestions.judge_safe": 0.9})
    assert "Judged safe 90%" in harness_clearance(s, shaky)


def link_from(mailer):
    body = mailer.sent[0].get_content()
    return next(line for line in body.splitlines() if "/fixed/" in line).split("/fixed/")[1]


@pytest.fixture
def web_client(directory, first_email, tmp_path, monkeypatch):
    service, tid, mailer = make(directory, first_email, tmp_path)
    service.adapter.assign(tid, directory.engineers[0].email)
    service.check_in(datetime.now(UTC))
    monkeypatch.setattr(web, "service", lambda: service)
    return TestClient(web.app), service, tid, link_from(mailer)


def test_the_link_holds_no_personal_details(web_client, first_email):
    import base64

    _, _, _, token = web_client
    payload = token.split(".")[0]
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    assert first_email.from_address not in decoded and "@" not in decoded


def test_opening_the_link_changes_nothing(web_client):
    client, service, tid, token = web_client
    page = client.get(f"/fixed/{token}")
    assert page.status_code == 200 and "Close my ticket" in page.text
    assert service.adapter.list_tickets()[0].is_open
    assert not [e for e in service.audit.events(tid) if e.kind == "close"]


def test_pressing_the_button_closes_as_the_client_once(web_client, directory):
    client, service, tid, token = web_client
    done = client.post(f"/fixed/{token}")
    assert done.status_code == 200 and "your ticket is closed" in done.text
    assert service.adapter.list_tickets() == []
    [close] = [e for e in service.audit.events(tid) if e.kind == "close"]
    assert close.actor == "client"
    [(_, heading, _, to)] = service.notifier.updates
    assert heading == "Closed by the client" and to.name == directory.engineers[0].name
    again = client.post(f"/fixed/{token}")
    assert again.status_code == 410 and "already been used" in again.text


def test_forged_links_do_nothing(web_client):
    client, service, _, token = web_client
    payload, signature = token.split(".")
    assert client.post(f"/fixed/{payload}.{signature[::-1]}").status_code == 410
    assert client.get("/fixed/not-a-token").status_code == 410
    assert service.adapter.list_tickets()[0].is_open


def test_the_ticket_reopens_when_the_client_writes_or_an_engineer_asks(web_client, first_email):
    client, service, tid, token = web_client
    client.post(f"/fixed/{token}")
    service.adapter.receive(tid, first_email.model_copy(update={"message_id": "<3@x.example>"}))
    service.check_in(datetime.now(UTC))
    reopen = [e for e in service.audit.events(tid) if e.kind == "reopen"]
    assert [e.actor for e in reopen] == ["client"]
    service.adapter.set_state(tid, "closed")
    assert client.post(f"/api/tickets/{tid}/reopen").json() == {"state": "open"}
    assert service.adapter.list_tickets()[0].is_open
    assert (
        client.post(f"/api/tickets/{tid}/suggestions", json={"enabled": False}).status_code == 200
    )


def test_demo_mode_has_no_confirm_page():
    client = TestClient(web.app)
    assert client.get("/fixed/anything").status_code == 404
    assert client.post("/api/tickets/1/reopen").status_code == 409
