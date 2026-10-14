"""Messifier: turns rendered core text into realistic, messy emails.

Adds what real client email is full of and models tend to smooth over:
signatures and legal disclaimers, "Sent from my iPhone", forwarded and quoted
chains, stacked RE: and FW: prefixes, typos, and mixed date formats and time
zones. Everything is seeded, so the same plan always gives the same emails.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from zoneinfo import ZoneInfo

from datagen.plan import ThreadPlan
from datagen.render_template import Rendered
from datagen.schema import Client, Email, Person, Thread
from datagen.world import World

LONDON = ZoneInfo("Europe/London")
MOBILE_FOOTERS = [
    "Sent from my iPhone",
    "Get Outlook for Android",
    "Sent from my Galaxy",
    "Sent from Outlook for iOS",
]
KEYBOARD_NEIGHBOURS = {
    "a": "s",
    "e": "r",
    "i": "o",
    "o": "p",
    "n": "m",
    "t": "r",
    "s": "d",
    "r": "e",
    "h": "j",
    "l": "k",
    "u": "y",
    "c": "v",
    "d": "f",
}


def _typo(word: str, rng: random.Random) -> str:
    core = word.strip(".,!?;:()")
    if len(core) < 4 or not core.isalpha():
        return word
    i = rng.randrange(1, len(core) - 1)
    kind = rng.random()
    if kind < 0.4:
        new = core[:i] + core[i + 1] + core[i] + core[i + 2 :]  # swap
    elif kind < 0.7:
        new = core[:i] + core[i + 1 :]  # drop
    else:
        new = core[:i] + KEYBOARD_NEIGHBOURS.get(core[i].lower(), core[i]) + core[i + 1 :]
    return word.replace(core, new, 1)


def add_typos(text: str, rate: float, rng: random.Random) -> str:
    """Introduce typos, leaving quoted strings (error messages) and anything with digits alone."""
    if rate <= 0:
        return text
    out_lines = []
    for line in text.split("\n"):
        words, in_quote = [], False
        for word in line.split(" "):
            quotes = word.count('"')
            if not in_quote and quotes == 0 and rng.random() < rate:
                word = _typo(word, rng)
            if quotes % 2 == 1:
                in_quote = not in_quote
            words.append(word)
        out_lines.append(" ".join(words))
    return "\n".join(out_lines)


def signature(person: Person, client: Client, rng: random.Random, payload: str | None) -> str:
    lines = [person.name, person.role, client.name]
    contact = []
    if person.phone:
        contact.append(f"T: {person.phone}")
    if person.mobile and rng.random() < 0.7:
        contact.append(f"M: {person.mobile}")
    if contact:
        lines.append(" | ".join(contact) if rng.random() < 0.5 else "\n".join(contact))
    lines.append(f"E: {person.email}" if rng.random() < 0.5 else f"www.{client.domain}")
    if payload:
        lines.append(payload)
    sig = "\n".join(lines)
    if rng.random() < 0.85:
        sig += "\n\n" + client.disclaimer
    return sig


def _local(dt: datetime, rng: random.Random) -> datetime:
    return dt.astimezone(UTC if rng.random() < 0.1 else LONDON)


def _quote_header_date(dt: datetime, style: str) -> str:
    local = dt.astimezone(LONDON)
    if style == "outlook":
        return local.strftime("%A, %-d %B %Y %H:%M")
    if style == "gmail":
        return local.strftime("%a, %-d %b %Y at %H:%M")
    return local.strftime("%d/%m/%Y %H:%M")


def quote(previous: Email, sent: datetime, style: str) -> str:
    if style == "gmail":
        quoted = "\n".join(f"> {line}" if line else ">" for line in previous.body.split("\n"))
        return (
            f"On {_quote_header_date(sent, 'gmail')}, {previous.from_name} "
            f"<{previous.from_address}> wrote:\n\n{quoted}"
        )
    header = (
        "-----Original Message-----\n"
        if style == "original"
        else "________________________________\n"
    )
    return (
        f"{header}From: {previous.from_name} <{previous.from_address}>\n"
        f"Sent: {_quote_header_date(sent, style)}\n"
        f"To: {', '.join(previous.to)}\n"
        f"Subject: {previous.subject}\n\n{previous.body}"
    )


def _reply_subject(subject: str, rng: random.Random) -> str:
    if subject.upper().startswith(("RE:", "FW:", "FWD:")):
        return subject if rng.random() < 0.8 else f"RE: {subject}"
    return f"{rng.choice(['RE', 'RE', 'Re'])}: {subject}"


class Messifier:
    def __init__(self, world: World, seed: int):
        self.world = world
        self.rng = random.Random(seed)

    def _message_id(self, plan: ThreadPlan, index: int, domain: str) -> str:
        digest = hashlib.sha1(f"{plan.thread_id}/{index}".encode()).hexdigest()[:12]
        return f"<{digest}.{plan.thread_id.lower()}@{domain}>"

    def build(self, plan: ThreadPlan, rendered: Rendered) -> Thread:
        rng = self.rng
        world = self.world
        client = world.client(plan.client_id)
        people = {p.id: p for p in client.people}
        engineers = {e.id: e for e in world.msp.engineers}
        support = world.msp.support_address
        emails: list[Email] = []
        quote_styles = {
            pid: rng.choice(["outlook", "outlook", "gmail", "original"]) for pid in people
        }

        previous_sent = plan.messages[0].sent_at
        for m, core in zip(plan.messages, rendered.bodies, strict=True):
            previous = emails[-1] if emails else None
            if m.direction == "outbound":
                engineer = engineers[m.sender_id]
                body = core
                if previous:
                    body += "\n\n" + quote(previous, previous_sent, "outlook")
                to = [previous.from_address] if previous else [people[plan.reported_by_id].email]
                emails.append(
                    Email(
                        message_id=self._message_id(plan, m.index, world.msp.domain),
                        in_reply_to=previous.message_id if previous else None,
                        from_name=f"{engineer.name} (Northgate Service Desk)",
                        from_address=support,
                        to=to,
                        date=format_datetime(m.sent_at.astimezone(LONDON)),
                        subject=_reply_subject(previous.subject, rng) if previous else plan.subject,
                        body=body,
                    )
                )
                previous_sent = m.sent_at
                continue

            sender = people[m.sender_id]
            from_mobile = rng.random() < sender.persona.mobile_share and m.kind != "initial"
            text = add_typos(core, sender.persona.typo_rate * (2 if from_mobile else 1), rng)
            payload = m.injection_text if m.injection_type == "signature_payload" else None
            if from_mobile:
                text += "\n\n" + rng.choice(MOBILE_FOOTERS)
                if payload:
                    text += "\n" + payload
            else:
                text += "\n\n" + signature(sender, client, rng, payload)

            if m.kind == "initial" and plan.traits.forwarded:
                text += "\n\n" + self._forwarded_original(plan, rendered, client, sender, m.sent_at)
                subject = f"{rng.choice(['FW', 'Fwd', 'FW'])}: {plan.subject}"
            elif previous:
                text += "\n\n" + quote(previous, previous_sent, quote_styles[sender.id])
                subject = _reply_subject(previous.subject, rng)
            else:
                subject = plan.subject

            cc = []
            if m.kind == "initial" and rng.random() < 0.2:
                cc = [rng.choice([p for p in client.people if p.id != sender.id]).email]
            if m.sender_id != plan.reported_by_id and m.kind != "initial":
                cc.append(people[plan.reported_by_id].email)

            emails.append(
                Email(
                    message_id=self._message_id(plan, m.index, client.domain),
                    in_reply_to=previous.message_id if previous else None,
                    from_name=sender.name
                    if rng.random() < 0.85
                    else f"{sender.last_name}, {sender.first_name}",
                    from_address=sender.email,
                    to=[support],
                    cc=cc,
                    date=format_datetime(_local(m.sent_at, rng)),
                    subject=subject,
                    body=text,
                )
            )
            previous_sent = m.sent_at

        return Thread(thread_id=plan.thread_id, messages=emails)

    def _forwarded_original(
        self,
        plan: ThreadPlan,
        rendered: Rendered,
        client: Client,
        forwarder: Person,
        forwarded_at: datetime,
    ) -> str:
        rng = self.rng
        caller = next(p for p in client.people if p.id == plan.caller_id)
        sent = forwarded_at - timedelta(minutes=rng.uniform(10, 300))
        original = add_typos(rendered.original or "", caller.persona.typo_rate, rng)
        sig = signature(caller, client, rng, None)
        if rng.random() < 0.5:
            return (
                "---------- Forwarded message ---------\n"
                f"From: {caller.name} <{caller.email}>\n"
                f"Date: {_quote_header_date(sent, 'gmail')}\n"
                f"Subject: {plan.subject}\n"
                f"To: {forwarder.name} <{forwarder.email}>\n\n{original}\n\n{sig}"
            )
        return (
            "________________________________\n"
            f"From: {caller.name} <{caller.email}>\n"
            f"Sent: {_quote_header_date(sent, 'outlook')}\n"
            f"To: {forwarder.name} <{forwarder.email}>\n"
            f"Subject: {plan.subject}\n\n{original}\n\n{sig}"
        )
