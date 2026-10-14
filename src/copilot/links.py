"""Signed, single-use "this fixed it" links in suggestion emails.

A link carries the ticket, the suggestion it belongs to and an expiry, signed
with ``SUGGESTION_LINK_SECRET`` (HMAC-SHA256). It holds no personal details: the
requester it was sent to is in the audit log, and the link only works while the
ticket's requester is still that person.
Opening it only shows a page with a confirm button; pressing the button closes
the ticket. Mail scanners that open links therefore cannot close tickets, and
the audit log makes each link work once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, SecretStr

LIFETIME = timedelta(days=14)


class FixedLink(BaseModel):
    ticket_id: str
    suggestion_id: str
    expires: datetime


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: SecretStr, payload: str) -> str:
    key = secret.get_secret_value().encode()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def make_token(
    secret: SecretStr,
    ticket_id: str,
    suggestion_id: str,
    now: datetime | None = None,
) -> str:
    expires = (now or datetime.now(UTC)) + LIFETIME
    payload = _b64(
        json.dumps(
            {
                "t": ticket_id,
                "s": suggestion_id,
                "e": int(expires.timestamp()),
            },
            separators=(",", ":"),
        ).encode()
    )
    return f"{payload}.{_sign(secret, payload)}"


def read_token(secret: SecretStr, token: str, now: datetime | None = None) -> FixedLink | None:
    """The link's contents, or None if it is forged, damaged or expired."""
    try:
        payload, signature = token.split(".")
        if not hmac.compare_digest(signature, _sign(secret, payload)):
            return None
        data = json.loads(_unb64(payload))
        link = FixedLink(
            ticket_id=str(data["t"]),
            suggestion_id=data["s"],
            expires=datetime.fromtimestamp(data["e"], UTC),
        )
    except (ValueError, KeyError, TypeError):
        return None
    if link.expires < (now or datetime.now(UTC)):
        return None
    return link
