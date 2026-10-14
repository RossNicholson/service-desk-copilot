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

import html
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
    from copilot.directory import load_directory
    from copilot.mail import SmtpMailer
    from copilot.notify import LogNotifier, TeamsNotifier
    from copilot.outreach import Outreach
    from copilot.pipeline import ModelCopilot
    from copilot.service import CopilotService
    from copilot.tickets.zammad import ZammadAdapter

    notifier = (
        TeamsNotifier(s.teams_webhook_url, s.public_url) if s.teams_webhook_url else LogNotifier()
    )
    system = ModelCopilot() if model_configured(s) else BaselineCopilot()
    directory = load_directory()
    adapter = ZammadAdapter.from_settings(s)
    audit = PostgresAuditLog.from_settings(s)
    mailer = None
    if s.suggestion_email_transport == "smtp":
        mailer = SmtpMailer(
            s.smtp_host,
            s.smtp_port,
            directory.support_address,
            f"{directory.msp_name} service desk",
            s.smtp_username,
            s.smtp_password,
            s.smtp_starttls,
        )
    outreach = Outreach(s, adapter, audit, notifier, directory, mailer)
    return CopilotService(system, adapter, audit, notifier, directory, outreach)


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


# -- the client's "this fixed it" page (public: keep it outside Cloudflare Access) ----------


def _fixed_page(body: str, status: int = 200) -> HTMLResponse:
    from copilot.directory import load_directory

    template = (STATIC / "fixed.html").read_text(encoding="utf-8")
    msp = html.escape(load_directory().msp_name)
    return HTMLResponse(template.format(msp=msp, body=body), status_code=status)


def _paragraph(text: str) -> str:
    return f"<p>{html.escape(text)}</p>"


@app.get("/fixed/{token}", include_in_schema=False, response_class=HTMLResponse)
def fixed_page(token: str):
    """Shows a confirm button. Opening the link changes nothing, so mail scanners are safe."""
    live = service()
    if live is None or live.outreach is None:
        return _fixed_page(_paragraph("This link is not valid or has expired."), 404)
    _, ticket, problem = live.outreach.check_link(token)
    if problem:
        return _fixed_page(f"<h1>Thanks for checking</h1>{_paragraph(problem)}", 410)
    token_attr = html.escape(token, quote=True)
    button = "Yes, it is fixed. Close my ticket"
    return _fixed_page(
        "<h1>Did one of our suggestions fix it?</h1>"
        + _paragraph(f"Ticket {ticket.number}: {ticket.title}")
        + _paragraph(
            "If it is fixed, press the button and we will close your ticket. If not, there "
            "is nothing to do: an engineer still has it and will reply."
        )
        + f'<form method="post" action="/fixed/{token_attr}">'
        + f'<button type="submit">{button}</button></form>'
    )


@app.post("/fixed/{token}", include_in_schema=False, response_class=HTMLResponse)
def fixed_confirm(token: str):
    live = service()
    if live is None or live.outreach is None:
        return _fixed_page(_paragraph("This link is not valid or has expired."), 404)
    problem = live.outreach.client_confirms(token)
    if problem:
        return _fixed_page(f"<h1>Thanks for checking</h1>{_paragraph(problem)}", 410)
    return _fixed_page(
        "<h1>Thanks, your ticket is closed</h1>"
        + _paragraph(
            "We have let the engineer know. If the problem comes back, reply to any email "
            "about this ticket and it will open again."
        )
    )


# -- engineers ----------------------------------------------------------------------


class SuggestionSwitch(BaseModel):
    enabled: bool


def _live_or_409():
    live = service()
    if live is None or live.outreach is None:
        raise HTTPException(409, "Only available on live tickets (COPILOT_SOURCE=zammad).")
    return live


@app.post("/api/tickets/{ticket_id}/suggestions")
def suggestions_switch(
    ticket_id: str,
    body: SuggestionSwitch,
    cf_user: str | None = Header(None, alias="Cf-Access-Authenticated-User-Email"),
):
    live = _live_or_409()
    live.outreach.set_enabled(ticket_id, body.enabled, _engineer(cf_user))
    return {"enabled": body.enabled}


@app.post("/api/tickets/{ticket_id}/reopen")
def reopen(
    ticket_id: str,
    cf_user: str | None = Header(None, alias="Cf-Access-Authenticated-User-Email"),
):
    from copilot.tickets import TicketNotFound

    live = _live_or_409()
    try:
        ticket = live._ticket(ticket_id)
    except TicketNotFound:
        raise HTTPException(404, "No ticket with that id.") from None
    live.outreach.reopen(ticket, _engineer(cf_user), "Reopened by an engineer.")
    return {"state": "open"}


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
        "suggestions": _suggestion_settings(s),
    }


def _suggestion_settings(s) -> dict:
    from copilot.outreach import harness_clearance

    return {
        "enabled": s.suggestions_enabled,
        "clients": s.suggestion_clients or "none",
        "transport": s.suggestion_email_transport,
        "link_secret_set": bool(s.suggestion_link_secret),  # never the secret itself
        "harness": harness_clearance(s) or "Cleared by the latest harness run.",
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
