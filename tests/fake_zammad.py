"""Just enough of Zammad's REST API to test the adapter without the compose stack."""

import json
import re
from datetime import UTC, datetime

import httpx


class FakeZammad:
    def __init__(self):
        self.tickets: dict[int, dict] = {}
        self.articles: list[dict] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _article(self, ticket_id: int, data: dict) -> dict:
        article = {
            "id": len(self.articles) + 1,
            "ticket_id": ticket_id,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **data,
        }
        self.articles.append(article)
        return article

    def handle(self, request: httpx.Request) -> httpx.Response:
        if not request.headers.get("authorization"):
            return httpx.Response(401, json={"error": "Authentication required"})
        path = request.url.path.removeprefix("/api/v1")
        body = json.loads(request.content) if request.content else {}
        method = request.method
        if method == "POST" and path == "/tickets":
            tid = len(self.tickets) + 1
            customer = body["customer_id"].removeprefix("guess:")
            self.tickets[tid] = {
                "id": tid,
                "number": str(55000 + tid),
                "title": body["title"],
                "state": "new",
                "owner": "-",
                "customer": customer,
                "group": body["group"],
            }
            self._article(tid, body["article"])
            return httpx.Response(201, json=self.tickets[tid])
        if method == "GET" and path == "/tickets":
            page = int(request.url.params.get("page", 1))
            per = int(request.url.params.get("per_page", 100))
            rows = sorted(self.tickets.values(), key=lambda t: t["id"])
            return httpx.Response(200, json=rows[(page - 1) * per : page * per])
        if m := re.fullmatch(r"/tickets/(\d+)", path):
            ticket = self.tickets.get(int(m.group(1)))
            if ticket is None:
                return httpx.Response(404, json={"error": "Not found"})
            if method == "PUT":
                ticket.update(body)
            return httpx.Response(200, json=ticket)
        if method == "POST" and path == "/ticket_articles":
            if body["ticket_id"] not in self.tickets:
                return httpx.Response(404, json={"error": "Not found"})
            article = self._article(body["ticket_id"], body)
            return httpx.Response(201, json=article)
        if m := re.fullmatch(r"/ticket_articles/by_ticket/(\d+)", path):
            tid = int(m.group(1))
            return httpx.Response(200, json=[a for a in self.articles if a["ticket_id"] == tid])
        return httpx.Response(404, json={"error": f"No route {method} {path}"})
