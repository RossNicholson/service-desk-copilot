"""Zammad, through its REST API.

The copilot's fields live in custom ticket attributes named ``copilot_<field>``
(``copilot_category``, ``copilot_priority`` and so on), which
``scripts/bootstrap_zammad.py`` creates, so engineers see them in Zammad too.
Notes are internal articles. Emails are the ticket's articles that are not
internal, oldest first, with the time Zammad received them as the date.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import format_datetime, getaddresses, parseaddr

import httpx
from pydantic import SecretStr

from copilot.config import Settings, get_settings
from copilot.schemas import Email
from copilot.tickets.base import COPILOT_FIELDS, Ticket, TicketNotFound, Value, check_fields

PREFIX = "copilot_"
INTEGER_FIELDS = ("impact", "urgency")
PAGE_SIZE = 100

# Attribute definitions for scripts/bootstrap_zammad.py.
FIELD_DISPLAY = {
    "caller": "Copilot: caller",
    "client_company": "Copilot: client",
    "category": "Copilot: category",
    "subcategory": "Copilot: subcategory",
    "affected_service": "Copilot: affected service",
    "impact": "Copilot: impact",
    "urgency": "Copilot: urgency",
    "priority": "Copilot: priority",
    "assignment_group": "Copilot: assignment group",
    "short_description": "Copilot: short description",
}


def attribute_definitions() -> list[dict]:
    return [
        {
            "object": "Ticket",
            "name": PREFIX + name,
            "display": FIELD_DISPLAY[name],
            "data_type": "input",
            "data_option": {"type": "text", "maxlength": 255, "null": True, "default": ""},
            "active": True,
            "position": 1500 + i,
            "screens": {"edit": {"ticket.agent": {"shown": True, "required": False}}},
        }
        for i, name in enumerate(COPILOT_FIELDS)
    ]


BLOCK_END = re.compile(r"</(?:p|div|li|tr|h\d|blockquote)>|<br\s*/?>", re.I)
TAG = re.compile(r"<[^>]+>")


def html_to_text(body: str) -> str:
    text = TAG.sub("", BLOCK_END.sub("\n", body))
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def _addresses(value: str | None) -> list[str]:
    return [address for _, address in getaddresses([value or ""]) if address]


def _email(article: dict) -> Email:
    name, address = parseaddr(article.get("from") or "")
    body = article.get("body") or ""
    if (article.get("content_type") or "").startswith("text/html"):
        body = html_to_text(body)
    when = datetime.fromisoformat(article["created_at"].replace("Z", "+00:00"))
    return Email(
        message_id=article.get("message_id") or f"<zammad-article-{article['id']}>",
        in_reply_to=article.get("in_reply_to"),
        from_name=name or address,
        from_address=address.lower(),
        to=_addresses(article.get("to")),
        cc=_addresses(article.get("cc")),
        date=format_datetime(when),
        subject=article.get("subject") or "",
        body=body,
    )


def _value(name: str, raw) -> Value:
    if raw in (None, ""):
        return None
    if name in INTEGER_FIELDS:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return str(raw)


class ZammadAdapter:
    def __init__(
        self,
        url: str,
        token: SecretStr | None = None,
        basic: tuple[str, str] | None = None,
        group: str = "Users",
        transport: httpx.BaseTransport | None = None,
    ):
        if token:
            headers = {"Authorization": f"Token token={token.get_secret_value()}"}
            auth = None
        elif basic:
            headers, auth = {}, basic
        else:
            raise ValueError("Zammad needs a token (ZAMMAD_TOKEN) or a user and password.")
        self.group = group
        self.http = httpx.Client(
            base_url=url.rstrip("/") + "/api/v1",
            headers=headers,
            auth=auth,
            timeout=30,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ZammadAdapter:
        s = settings or get_settings()
        return cls(s.zammad_url, token=s.zammad_token, group=s.zammad_group)

    def _call(self, method: str, path: str, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if response.status_code == 404:
            raise TicketNotFound(path)
        response.raise_for_status()
        return response.json() if response.content else None

    # -- the four operations ---------------------------------------------------------

    def create_ticket(self, email: Email) -> Ticket:
        created = self._call(
            "POST",
            "/tickets",
            json={
                "title": email.subject,
                "group": self.group,
                "customer_id": f"guess:{email.from_address}",
                "article": {
                    "type": "email",
                    "sender": "Customer",
                    "internal": False,
                    "from": f"{email.from_name} <{email.from_address}>",
                    "to": ", ".join(email.to),
                    "cc": ", ".join(email.cc),
                    "subject": email.subject,
                    "body": email.body,
                    "content_type": "text/plain",
                    "message_id": email.message_id,
                },
            },
        )
        return self._ticket(self._call("GET", f"/tickets/{created['id']}?expand=true"))

    def update_fields(self, ticket_id: str, fields: dict[str, Value]) -> Ticket:
        check_fields(fields)
        body = {PREFIX + k: "" if v is None else str(v) for k, v in fields.items()}
        self._call("PUT", f"/tickets/{ticket_id}", json=body)
        return self._ticket(self._call("GET", f"/tickets/{ticket_id}?expand=true"))

    def add_note(self, ticket_id: str, text: str) -> None:
        self._call(
            "POST",
            "/ticket_articles",
            json={
                "ticket_id": int(ticket_id),
                "type": "note",
                "internal": True,
                "content_type": "text/plain",
                "subject": "Service Desk Copilot note",
                "body": text,
            },
        )

    def list_tickets(self, open_only: bool = True) -> list[Ticket]:
        tickets, page = [], 1
        while True:
            batch = self._call(
                "GET",
                "/tickets",
                params={"expand": "true", "per_page": PAGE_SIZE, "page": page},
            )
            tickets += [self._ticket(t) for t in batch]
            if len(batch) < PAGE_SIZE:
                break
            page += 1
        tickets.sort(key=lambda t: int(t.id))
        return [t for t in tickets if t.is_open or not open_only]

    # -- mapping ----------------------------------------------------------------------

    def _ticket(self, raw: dict) -> Ticket:
        articles = self._call("GET", f"/ticket_articles/by_ticket/{raw['id']}")
        articles.sort(key=lambda a: a["id"])
        owner = raw.get("owner")
        return Ticket(
            id=str(raw["id"]),
            number=str(raw["number"]),
            title=raw.get("title") or "",
            state=raw.get("state") or "new",
            owner=None if owner in (None, "-") else owner,
            customer=raw.get("customer"),
            fields={name: _value(name, raw.get(PREFIX + name)) for name in COPILOT_FIELDS},
            emails=[_email(a) for a in articles if not a.get("internal")],
            notes=[
                html_to_text(a.get("body") or "")
                if (a.get("content_type") or "").startswith("text/html")
                else a.get("body") or ""
                for a in articles
                if a.get("internal") and a.get("type") == "note"
            ],
        )
