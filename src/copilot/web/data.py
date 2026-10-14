"""What the web UI shows.

`snapshot` is the offline demo: the synthetic dev threads with a system's results
(only the dev split; the test split is held out for the evaluation harness).
`live_snapshot` is the real thing: tickets from the ticket system, with the
copilot's fields, notes, alerts and overrides read from the audit log.
"""

from __future__ import annotations

import json
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from copilot.audit import AuditEvent, locked_fields
from copilot.baseline import PREFIX, BaselineCopilot
from copilot.directory import Directory, load_directory
from copilot.evaluation.dataset import DATA_DIR, LabelledThread, load_split
from copilot.schemas import FIELD_NAMES, Email, ThreadResult
from copilot.tickets import Ticket

REPORTS = Path(__file__).resolve().parents[3] / "reports"

FIELD_LABELS = {
    "caller": "Caller",
    "client_company": "Client",
    "category": "Category",
    "subcategory": "Subcategory",
    "affected_service": "Affected service",
    "impact": "Impact",
    "urgency": "Urgency",
    "priority": "Priority",
    "assignment_group": "Assignment group",
    "short_description": "Short description",
}


def _when(header: str) -> str:
    return parsedate_to_datetime(header).isoformat()


def _person(directory: Directory, address: str | None) -> dict | None:
    if not address:
        return None
    for client in directory.clients:
        for person in client.people:
            if person.email.lower() == str(address).lower():
                return {"name": person.name, "role": person.role, "email": person.email}
    return {"name": str(address), "role": "", "email": str(address)}


def options(directory: Directory) -> dict:
    """The values an engineer can pick from when overriding a field."""
    return {
        "client_company": sorted(c.name for c in directory.clients),
        "category": sorted({r.category for r in directory.routes}),
        "subcategory": sorted({r.subcategory for r in directory.routes}),
        "affected_service": sorted({s for c in directory.clients for s in c.services.values()}),
        "assignment_group": [t.name for t in directory.teams],
        "impact": [1, 2, 3],
        "urgency": [1, 2, 3],
        "priority": ["P1", "P2", "P3", "P4", "P5"],
    }


def _emails(emails: list[Email], support: str) -> list[dict]:
    return [
        {
            "from_name": e.from_name,
            "from_address": e.from_address,
            "to": e.to,
            "date": _when(e.date),
            "subject": e.subject,
            "body": e.body,
            "outbound": e.from_address == support,
        }
        for e in emails
    ]


def _evaluation() -> dict | None:
    report = REPORTS / "eval-dev.json"
    return json.loads(report.read_text()) if report.exists() else None


def _common(directory: Directory) -> dict:
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "msp": {"name": directory.msp_name, "support_address": directory.support_address},
        "field_labels": FIELD_LABELS,
        "options": options(directory),
        "teams": [
            {"name": t.name, "lead": next(e.name for e in directory.engineers if e.id == t.lead)}
            for t in directory.teams
        ],
        "response_targets_minutes": directory.response_targets_minutes,
        "evaluation": _evaluation(),
    }


def ticket(lt: LabelledThread, result: ThreadResult, directory: Directory, number: int) -> dict:
    thread = lt.thread
    support = directory.support_address
    replies = [e for e in thread.messages if e.from_address == support]
    fields = {
        name: result.fields[name].model_dump() for name in FIELD_NAMES if name in result.fields
    }
    caller = fields.get("caller", {}).get("value")
    chasing = [a for a in result.chase_assessments if a.is_chase]
    return {
        "id": thread.thread_id,
        "number": 260000 + number,
        "subject": PREFIX.sub("", thread.messages[0].subject).strip(),
        "opened_at": _when(thread.messages[0].date),
        "updated_at": _when(thread.messages[-1].date),
        "observed_at": lt.labels.timing.observed_at.isoformat(),
        "caller": _person(directory, caller),
        "assignee": replies[0].from_name.split(" (")[0] if replies else None,
        "status": "Waiting on client" if thread.messages[-1].from_address == support else "Open",
        "responded": bool(replies),
        "fields": fields,
        "flagged": [n for n, f in fields.items() if f["needs_review"]],
        "chasing": len(chasing),
        "emails": _emails(thread.messages, support),
        "note": result.notes[-1].model_dump() if result.notes else None,
        "alerts": [a.model_dump(mode="json") for a in result.alerts],
    }


def snapshot(limit: int | None = None) -> dict:
    """Everything the UI needs, as plain JSON-ready data."""
    directory = load_directory()
    threads = load_split("dev", DATA_DIR)
    threads.sort(key=lambda lt: parsedate_to_datetime(lt.thread.messages[0].date))
    if limit:
        threads = threads[-limit:]
    system = BaselineCopilot(directory)
    tickets = []
    for i, lt in enumerate(threads, start=1):
        result = system.process(lt.thread, now=lt.labels.timing.observed_at)
        tickets.append(ticket(lt, result, directory, i))
    return {
        **_common(directory),
        "source": {
            "system": system.name,
            "live": False,
            "note": "AI values come from the rules baseline until a model is configured.",
        },
        "tickets": tickets,
        "audit": [],
        "suggestions": None,
    }


# -- live -----------------------------------------------------------------------------

STATES = {"new": "New", "open": "Open", "closed": "Closed", "merged": "Closed"}


def audit_entry(event: AuditEvent) -> dict:
    """An override or revert as the activity log on the ticket view shows it."""
    return {
        "ticket": event.ticket_id,
        "field": event.field,
        "from": event.old,
        "to": event.new,
        "by": event.actor,
        "at": event.at.isoformat(timespec="seconds"),
        "action": event.kind,
    }


def live_fields(ticket: Ticket, events: list[AuditEvent]) -> dict:
    """Each field as the copilot last proposed it, with any engineer override on top."""
    runs = [e for e in events if e.kind == "run"]
    proposed = runs[-1].detail.get("fields", {}) if runs else {}
    locked = locked_fields(events)
    out = {}
    for name in FIELD_NAMES:
        field = dict(
            proposed.get(name)
            or {
                "value": ticket.fields.get(name),
                "confidence": 0.0,
                "reason": "Not read by the copilot yet.",
                "needs_review": True,
            }
        )
        if name in locked:
            field["ai_value"] = field["value"]
            field["value"] = locked[name]
            field["overridden"] = True
        out[name] = field
    return out


def live_suggestion(events: list[AuditEvent]) -> dict:
    """The ticket's suggestion email: sent, skipped, dropped or not yet decided."""
    out = {"status": "none", "reason": None, "text": None, "at": None, "off": False}
    for e in events:
        if e.kind != "suggestion":
            continue
        action = e.detail.get("action")
        if action in ("on", "off"):
            out["off"] = action == "off"
        else:
            out.update(
                status=action,
                reason=e.reason,
                text=e.new,
                steps=e.detail.get("steps", []),
                at=e.at.isoformat(timespec="seconds"),
            )
    states = [e for e in events if e.kind in ("close", "reopen")]
    out["closed_by_client"] = bool(states) and states[-1].kind == "close"
    return out


def suggestion_stats(events: list[AuditEvent]) -> dict:
    """How many suggestion emails went out, and how many clients said one fixed it."""
    sent = {
        e.detail.get("id")
        for e in events
        if e.kind == "suggestion" and e.detail.get("action") == "sent"
    }
    fixed = {
        e.detail.get("suggestion_id")
        for e in events
        if e.kind == "close" and e.actor == "client" and e.detail.get("suggestion_id") in sent
    }
    return {
        "sent": len(sent),
        "fixed": len(fixed),
        "fix_rate": len(fixed) / len(sent) if sent else None,
    }


def live_ticket(ticket: Ticket, events: list[AuditEvent], directory: Directory) -> dict:
    support = directory.support_address
    fields = live_fields(ticket, events)
    suggestion = live_suggestion(events)
    owner = next(
        (e.name for e in directory.engineers if ticket.owner and e.email == ticket.owner),
        ticket.owner,
    )
    notes = [e for e in events if e.kind == "note"]
    alerts = [e.detail["alert"] for e in events if e.kind == "alert"]
    runs = [e for e in events if e.kind == "run"]
    chasing = runs[-1].detail.get("chase_assessments", []) if runs else []
    last = ticket.emails[-1] if ticket.emails else None
    status = STATES.get(ticket.state, ticket.state.title())
    if last is not None and last.from_address == support and ticket.is_open:
        status = "Waiting on client"
    if not ticket.is_open and suggestion["closed_by_client"]:
        status = "Closed by the client"
    return {
        "id": ticket.id,
        "number": ticket.number,
        "subject": PREFIX.sub("", ticket.title).strip(),
        "opened_at": _when(ticket.emails[0].date) if ticket.emails else None,
        "updated_at": _when(last.date) if last else None,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "caller": _person(directory, fields["caller"]["value"]),
        "assignee": owner,
        "status": status,
        "responded": any(e.from_address == support for e in ticket.emails),
        "fields": fields,
        "flagged": [n for n, f in fields.items() if f["needs_review"] and not f.get("overridden")],
        "chasing": sum(1 for a in chasing if a.get("is_chase")),
        "emails": _emails(ticket.emails, support),
        "note": notes[-1].detail.get("note") if notes else None,
        "alerts": alerts,
        "open": ticket.is_open,
        "suggestion": suggestion,
    }


def live_snapshot(service) -> dict:
    """Everything the UI needs, from the ticket system and the audit log."""
    d = service.directory
    events = service.audit.events()
    by_ticket: dict[str, list[AuditEvent]] = {}
    for event in events:
        by_ticket.setdefault(event.ticket_id, []).append(event)
    from copilot.service import without_own_emails

    tickets = []
    for t in service.adapter.list_tickets(open_only=False):
        events = by_ticket.get(t.id, [])
        t = without_own_emails(t, events)
        if t.emails:
            tickets.append(live_ticket(t, events, d))
    return {
        **_common(d),
        "source": {
            "system": service.system.name,
            "live": True,
            "note": f"Live tickets from the ticket system. AI values come from the "
            f"{service.system.name} system.",
        },
        "tickets": tickets,
        "audit": [audit_entry(e) for e in events if e.kind in ("override", "revert")],
        "suggestions": suggestion_stats(events),
    }
