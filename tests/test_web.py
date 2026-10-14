import pytest
from fastapi.testclient import TestClient

from copilot.config import get_settings
from copilot.web import app as web


@pytest.fixture(scope="module")
def client():
    return TestClient(web.app)


def test_page_and_data(client):
    page = client.get("/")
    assert page.status_code == 200 and "Service Desk Copilot" in page.text
    data = client.get("/api/data").json()
    assert len(data["tickets"]) == 200
    assert all(t["id"].startswith("DEV-") for t in data["tickets"])  # never the test split
    ticket = data["tickets"][0]
    assert set(ticket["fields"]["priority"]) >= {"value", "confidence", "reason", "needs_review"}


def test_settings_never_return_the_key(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret-value")
    get_settings.cache_clear()
    try:
        body = client.get("/api/settings").text
    finally:
        get_settings.cache_clear()
    assert "sk-test-secret-value" not in body
    assert '"api_key_set":true' in body


def test_override_is_logged_and_can_be_undone(client):
    ticket = client.get("/api/data").json()["tickets"][0]
    original = ticket["fields"]["urgency"]["value"]
    url = f"/api/tickets/{ticket['id']}/fields/urgency"
    changed = client.post(url, json={"value": 3 if original != 3 else 1}).json()
    assert changed["overridden"] and changed["ai_value"] == original
    reverted = client.delete(url).json()
    assert reverted["value"] == original and not reverted["overridden"]
    audit = [a for a in client.get("/api/data").json()["audit"] if a["ticket"] == ticket["id"]]
    assert [a["action"] for a in audit] == ["override", "revert"]
    assert client.delete(url).status_code == 409
    assert client.post("/api/tickets/NOPE/fields/urgency", json={"value": 1}).status_code == 404


def test_preview_export_is_self_contained():
    from scripts.export_ui_preview import build

    html = build(fragment=True, limit=5)
    assert "window.PREVIEW_DATA=" in html and '<script src="' not in html
    assert "<html" not in html


@pytest.fixture
def live(monkeypatch):
    from copilot.audit import MemoryAuditLog
    from copilot.baseline import BaselineCopilot
    from copilot.directory import load_directory
    from copilot.evaluation.dataset import load_split
    from copilot.notify import LogNotifier
    from copilot.service import CopilotService
    from copilot.tickets import MemoryAdapter

    directory = load_directory()
    adapter = MemoryAdapter()
    lt = load_split("dev")[0]
    ticket = adapter.create_ticket(lt.thread.messages[0])
    for email in lt.thread.messages[1:]:
        adapter.receive(ticket.id, email, directory.support_address)
    service = CopilotService(
        BaselineCopilot(directory), adapter, MemoryAuditLog(), LogNotifier(), directory
    )
    service.check_in(lt.labels.timing.observed_at)
    monkeypatch.setattr(web, "service", lambda: service)
    return service, ticket.id


def test_live_data_comes_from_the_ticket_system_and_audit_log(client, live):
    _, tid = live
    data = client.get("/api/data").json()
    assert data["source"]["live"] is True
    [ticket] = data["tickets"]
    assert ticket["id"] == tid
    assert ticket["fields"]["client_company"]["reason"]
    assert ticket["note"] is not None


def test_live_overrides_are_saved_and_name_the_engineer(client, live):
    service, tid = live
    url = f"/api/tickets/{tid}/fields/urgency"
    headers = {"Cf-Access-Authenticated-User-Email": "sam.engineer@northgate-ms.example"}
    field = client.post(url, json={"value": 1}, headers=headers).json()
    assert field["value"] == 1 and field["overridden"]
    assert service.adapter.list_tickets()[0].fields["urgency"] == 1
    [event] = [e for e in service.audit.events(tid) if e.kind == "override"]
    assert event.actor == "sam.engineer@northgate-ms.example"
    audit = client.get("/api/data").json()["audit"]
    assert audit[0]["by"] == "sam.engineer@northgate-ms.example"
    reverted = client.delete(url).json()
    assert not reverted.get("overridden") and reverted["value"] == field["ai_value"]
    assert client.delete(url).status_code == 409
    assert client.post("/api/tickets/999/fields/urgency", json={"value": 1}).status_code == 404
    assert client.post(f"/api/tickets/{tid}/fields/state", json={"value": 1}).status_code == 404
