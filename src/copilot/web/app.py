"""The copilot's web UI: a ticket list, a ticket view with AI fields, alerts and settings.

    uv run uvicorn copilot.web.app:app --reload     # then open http://localhost:8000

With ``COPILOT_SOURCE=demo`` (the default) it shows the synthetic dev threads with
the rules baseline's values and keeps overrides in memory, so they reset on
restart. With ``COPILOT_SOURCE=zammad`` it works on the live Zammad: the scheduled
check-ins run in the background, overrides are written to Zammad and the audit
log in Postgres, and the copilot leaves an overridden field alone until it is
reverted. Behind Cloudflare Access, the engineer is named from the
``Cf-Access-Authenticated-User-Email`` header Access adds.

The API key is never returned: settings report only whether one is set.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from copilot.config import get_settings
from copilot.llm import model_configured
from copilot.schemas import FIELD_NAMES
from copilot.web.data import live_fields, live_snapshot, snapshot

STATIC = Path(__file__).parent / "static"


@lru_cache
def service():
    """The live copilot, when COPILOT_SOURCE=zammad; None for the demo."""
    s = get_settings()
    if s.copilot_source != "zammad":
        return None
    from copilot.audit import PostgresAuditLog
    from copilot.baseline import BaselineCopilot
    from copilot.notify import LogNotifier, TeamsNotifier
    from copilot.pipeline import ModelCopilot
    from copilot.service import CopilotService
    from copilot.tickets.zammad import ZammadAdapter

    notifier = (
        TeamsNotifier(s.teams_webhook_url, s.public_url) if s.teams_webhook_url else LogNotifier()
    )
    system = ModelCopilot() if model_configured(s) else BaselineCopilot()
    return CopilotService(
        system, ZammadAdapter.from_settings(s), PostgresAuditLog.from_settings(s), notifier
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    live = service()
    scheduler = None
    if live is not None:
        from copilot.scheduler import start

        scheduler = start(live)
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Service Desk Copilot", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@lru_cache
def _data() -> dict:
    data = snapshot()
    data["audit"] = []
    return data


def _ticket(ticket_id: str) -> dict:
    ticket = next((t for t in _data()["tickets"] if t["id"] == ticket_id), None)
    if ticket is None:
        raise HTTPException(404, "No ticket with that id.")
    return ticket


class Override(BaseModel):
    value: str | int | None
    engineer: str = "You"


def page(style: str, script: str) -> str:
    """The whole page: the shared shell with the stylesheet and script put in."""
    shell = (STATIC / "shell.html").read_text(encoding="utf-8")
    shell = shell.replace("<!--STYLE-->", style).replace("<!--SCRIPT-->", script)
    return (
        '<!doctype html>\n<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{shell}\n</html>\n"
    )


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
def index():
    return page(
        '<link rel="stylesheet" href="/static/app.css">', '<script src="/static/app.js"></script>'
    )


@app.get("/api/data")
def data():
    live = service()
    return live_snapshot(live) if live else _data()


def _engineer(header: str | None, given: str = "You") -> str:
    return header or given


def _live_field(live, ticket_id: str, name: str) -> dict:
    ticket = live._ticket(ticket_id)
    return live_fields(ticket, live.audit.events(ticket_id))[name]


@app.get("/api/settings")
def settings():
    s = get_settings()
    return {
        "model": s.llm_model,
        "provider": s.llm_model.split("/", 1)[0],
        "api_base": s.llm_api_base,
        "api_key_set": bool(s.llm_api_key),  # never the key itself
        "model_configured": model_configured(s),
        "spend_cap_usd": s.spend_cap_usd,
        "rate_limit_per_minute": s.rate_limit_per_minute,
        "source": s.copilot_source,
        "teams_webhook_set": bool(s.teams_webhook_url),  # never the URL itself
    }


@app.post("/api/tickets/{ticket_id}/fields/{name}")
def override(
    ticket_id: str,
    name: str,
    body: Override,
    cf_user: str | None = Header(None, alias="Cf-Access-Authenticated-User-Email"),
):
    if name not in FIELD_NAMES:
        raise HTTPException(404, "No field with that name.")
    live = service()
    if live is not None:
        from copilot.tickets import TicketNotFound

        try:
            live.override(ticket_id, name, body.value, _engineer(cf_user, body.engineer))
        except TicketNotFound:
            raise HTTPException(404, "No ticket with that id.") from None
        return _live_field(live, ticket_id, name)
    ticket = _ticket(ticket_id)
    field = ticket["fields"].get(name)
    if field is None:
        raise HTTPException(404, "No field with that name.")
    field.setdefault("ai_value", field["value"])
    entry = {
        "ticket": ticket_id,
        "field": name,
        "from": field["value"],
        "to": body.value,
        "by": _engineer(cf_user, body.engineer),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "action": "override",
    }
    field["value"] = body.value
    field["overridden"] = True
    _data()["audit"].append(entry)
    return field


@app.delete("/api/tickets/{ticket_id}/fields/{name}")
def revert(
    ticket_id: str,
    name: str,
    cf_user: str | None = Header(None, alias="Cf-Access-Authenticated-User-Email"),
):
    live = service()
    if live is not None:
        from copilot.tickets import TicketNotFound

        try:
            live.revert(ticket_id, name, _engineer(cf_user))
        except TicketNotFound:
            raise HTTPException(404, "No ticket with that id.") from None
        except LookupError:
            raise HTTPException(409, "That field has not been overridden.") from None
        return _live_field(live, ticket_id, name)
    ticket = _ticket(ticket_id)
    field = ticket["fields"].get(name)
    if field is None or not field.get("overridden"):
        raise HTTPException(409, "That field has not been overridden.")
    _data()["audit"].append(
        {
            "ticket": ticket_id,
            "field": name,
            "from": field["value"],
            "to": field["ai_value"],
            "by": _engineer(cf_user),
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "action": "revert",
        }
    )
    field["value"] = field["ai_value"]
    field["overridden"] = False
    return field
