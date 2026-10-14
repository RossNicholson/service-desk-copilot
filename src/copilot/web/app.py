"""The copilot's web UI: a ticket list, a ticket view with AI fields, alerts and settings.

    uv run uvicorn copilot.web.app:app --reload     # then open http://localhost:8000

This is the Phase 6 UI started early as a preview. It reads the synthetic dev
threads rather than Zammad, takes AI values from the rules baseline unless a
model is configured, and keeps overrides in memory, so they reset on restart.
The API key is never returned: settings report only whether one is set.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from copilot.config import get_settings
from copilot.llm import model_configured
from copilot.web.data import snapshot

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Service Desk Copilot", docs_url=None, redoc_url=None)
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
    return _data()


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
    }


@app.post("/api/tickets/{ticket_id}/fields/{name}")
def override(ticket_id: str, name: str, body: Override):
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
        "by": body.engineer,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "action": "override",
    }
    field["value"] = body.value
    field["overridden"] = True
    _data()["audit"].append(entry)
    return field


@app.delete("/api/tickets/{ticket_id}/fields/{name}")
def revert(ticket_id: str, name: str):
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
            "by": "You",
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "action": "revert",
        }
    )
    field["value"] = field["ai_value"]
    field["overridden"] = False
    return field
