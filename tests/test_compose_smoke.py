"""Smoke tests against the running docker compose stack.

Run with `uv run pytest -m compose` after `docker compose up -d --wait` and
`uv run python scripts/bootstrap_zammad.py`.
"""

import json
import smtplib
import time
import urllib.request
import uuid
from email.message import EmailMessage

import pytest

from scripts.bootstrap_zammad import ROOT, SUPPORT_ADDRESS, Zammad, load_env

pytestmark = pytest.mark.compose

ENV = load_env(ROOT / ".env")
ZAMMAD_URL = ENV.get("ZAMMAD_URL", "http://localhost:8080")
SMTP_HOST = ENV.get("SMTP_HOST", "localhost")
SMTP_PORT = int(ENV.get("SMTP_PORT", "1025"))


def admin() -> Zammad:
    return Zammad(ZAMMAD_URL, ENV["ZAMMAD_ADMIN_EMAIL"], ENV["ZAMMAD_ADMIN_PASSWORD"])


def test_zammad_admin_can_sign_in():
    assert admin().request("GET", "/api/v1/users/me")["email"] == ENV["ZAMMAD_ADMIN_EMAIL"]


def test_mailpit_api_answers():
    with urllib.request.urlopen("http://localhost:8025/api/v1/info", timeout=10) as resp:
        assert "Version" in json.loads(resp.read())


def test_auto_replies_are_off():
    triggers = admin().request("GET", "/api/v1/triggers")
    assert not [t for t in triggers if t["name"].startswith("auto reply") and t["active"]]


def test_email_to_mailpit_becomes_a_zammad_ticket():
    subject = f"Smoke test {uuid.uuid4().hex[:8]}"
    message = EmailMessage()
    message["From"] = "Smoke Test <smoke.test@harbourline-bs.example>"
    message["To"] = SUPPORT_ADDRESS
    message["Subject"] = subject
    message.set_content("The printer on the second floor is jammed again.")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.send_message(message)

    # Zammad polls the POP3 mailbox on a schedule, so allow a couple of minutes.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        tickets = admin().request("GET", "/api/v1/tickets?per_page=50&order_by=desc&sort_by=id")
        if any(t["title"] == subject for t in tickets):
            return
        time.sleep(5)
    pytest.fail(f"No Zammad ticket titled {subject!r} appeared within 180 seconds")
