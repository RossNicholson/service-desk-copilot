"""What the web UI shows: tickets built from the dev threads and a system's results.

Until the Zammad adapter arrives in Phase 6, the UI reads the synthetic dev
threads directly. Only the dev split is used here; the test split is held out
for the evaluation harness.
"""

from __future__ import annotations

import json
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from copilot.baseline import PREFIX, BaselineCopilot
from copilot.directory import Directory, load_directory
from copilot.evaluation.dataset import DATA_DIR, LabelledThread, load_split
from copilot.schemas import FIELD_NAMES, ThreadResult

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
        "emails": [
            {
                "from_name": e.from_name,
                "from_address": e.from_address,
                "to": e.to,
                "date": _when(e.date),
                "subject": e.subject,
                "body": e.body,
                "outbound": e.from_address == support,
            }
            for e in thread.messages
        ],
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
    report = REPORTS / "eval-dev.json"
    evaluation = json.loads(report.read_text()) if report.exists() else None
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {
            "system": system.name,
            "note": "AI values come from the rules baseline until a model is configured.",
        },
        "msp": {"name": directory.msp_name, "support_address": directory.support_address},
        "field_labels": FIELD_LABELS,
        "options": options(directory),
        "teams": [
            {"name": t.name, "lead": next(e.name for e in directory.engineers if e.id == t.lead)}
            for t in directory.teams
        ],
        "response_targets_minutes": directory.response_targets_minutes,
        "tickets": tickets,
        "evaluation": evaluation,
    }
