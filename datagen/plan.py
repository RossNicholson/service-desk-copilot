"""Thread plans: every decision about a thread, made before any text is written.

A plan is drawn deterministically from a seed. It fixes the client, caller,
issue, scope, urgency, which facts are stated or left out, the timeline of
messages, and any chasing, injection or PII. Renderers (template or model) turn
a plan into email text; labels are derived from the plan, so they hold whichever
renderer is used.
"""

from __future__ import annotations

import random
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from datagen import pools
from datagen.names import FIRST_NAMES, LAST_NAMES
from datagen.schema import Client, Engineer, Issue, Person, Traits
from datagen.world import World

LONDON = ZoneInfo("Europe/London")

PRIORITY_MATRIX = {
    (1, 1): "P1",
    (1, 2): "P2",
    (1, 3): "P3",
    (2, 1): "P2",
    (2, 2): "P3",
    (2, 3): "P4",
    (3, 1): "P3",
    (3, 2): "P4",
    (3, 3): "P5",
}
SCOPE_IMPACT = {"site": 1, "team": 2, "single": 3}

TRAIT_NAMES = (
    "forwarded",
    "multi_issue",
    "chasing",
    "false_alarm",
    "silence",
    "adversarial",
    "pii_heavy",
)

MessageKind = Literal["initial", "ack", "chase", "resend", "false_alarm", "info", "escalation"]


class Fact(BaseModel):
    key: str
    text: str


class Secondary(BaseModel):
    issue_id: str
    category: str
    subcategory: str
    problem: str


class MessagePlan(BaseModel):
    index: int
    kind: MessageKind
    direction: Literal["inbound", "outbound"]
    sender_id: str
    sent_at: datetime
    frustration: int = 0
    facts: list[Fact] = []
    injection_type: str | None = None
    injection_text: str | None = None
    pii_text: str | None = None
    scope_change: str | None = None
    urgency_change: int | None = None


class ThreadPlan(BaseModel):
    thread_id: str
    split: Literal["dev", "test"]
    client_id: str
    caller_id: str
    reported_by_id: str
    issue_id: str
    traits: Traits
    fills: dict[str, str]
    subject: str
    problem: str
    scope: Literal["single", "team", "site"]
    scope_text: str | None
    final_scope: Literal["single", "team", "site"]
    final_scope_text: str | None
    urgency: int
    final_urgency: int
    urgency_text: str | None
    tried: list[str]
    asks: list[str]
    details_stated: list[Fact]
    details_missing: list[Fact]
    secondary: list[Secondary]
    pii_values: list[tuple[str, str]]
    messages: list[MessagePlan]
    observed_at: datetime
    ack_engineer_id: str | None


def _fill(text: str, fills: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: fills.get(m.group(1), m.group(0)), text)


def priority(impact: int, urgency: int) -> str:
    return PRIORITY_MATRIX[(impact, urgency)]


def allocate_traits(n: int, mix: dict[str, float], rng: random.Random) -> list[Traits]:
    """Give each trait to exactly round(share * n) threads, chosen at random."""
    flags = [dict.fromkeys(TRAIT_NAMES, False) for _ in range(n)]
    for trait in TRAIT_NAMES:
        for i in rng.sample(range(n), round(mix[trait] * n)):
            flags[i][trait] = True
    return [Traits(**f) for f in flags]


def _business_time(rng: random.Random, start: date, end: date) -> datetime:
    days = [start + timedelta(d) for d in range((end - start).days + 1)]
    day = rng.choice([d for d in days if d.weekday() < 5])
    minutes = rng.randint(8 * 60, 17 * 60 + 30)
    local = datetime.combine(day, time(minutes // 60, minutes % 60, rng.randint(0, 59)), LONDON)
    return local.astimezone(UTC)


def _mailbox(first: str, last: str, suffix: int | str = "") -> str:
    local = f"{first}.{last}{suffix}".lower().replace("'", "")
    return f"{local}@homemail.example"


def _minutes(value: float) -> timedelta:
    """A whole number of seconds, so timestamps stay tidy."""
    return timedelta(seconds=round(value * 60))


class Planner:
    def __init__(self, world: World, mix: dict, seed: int, split: Literal["dev", "test"]):
        self.world = world
        self.mix = mix
        self.rng = random.Random(seed)
        self.split = split
        self.start = date.fromisoformat(mix["dates"]["start"])
        self.end = date.fromisoformat(mix["dates"]["end"])

    # -- helpers ----------------------------------------------------------------

    def _fills(self, client: Client, caller: Person, issue: Issue) -> dict[str, str]:
        rng = self.rng
        others = [p for p in client.people if p.id != caller.id]
        colleague, colleague_two = rng.sample(others, 2)
        if issue.id == "new-starter":
            starter = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            colleague_name = starter
        else:
            colleague_name = colleague.name if rng.random() < 0.5 else colleague.first_name
        if issue.service == "mobile" or issue.id == "mfa-new-phone":
            device = rng.choice(pools.PHONE_MODELS)
        elif issue.id == "teams-meetings-audio":
            device = rng.choice(pools.HEADSETS)
        else:
            device = f"{client.short_name[:3].upper()}-LT-{rng.randint(100, 999)}"
            if rng.random() < 0.3:
                device = f"{rng.choice(pools.LAPTOP_MODELS)} ({device})"
        prefix = pools.REFERENCE_PREFIX[client.id]
        return {
            "service": client.services[issue.service],
            "site": rng.choice(client.sites[:-1] if client.sites[-1] == "Remote" else client.sites),
            "device": device,
            "colleague": colleague_name,
            "colleague_two": colleague_two.name,
            "error": rng.choice(pools.ERRORS.get(issue.id, pools.GENERIC_ERRORS)),
            "time": rng.choice(pools.TIMES),
            "day": rng.choice(pools.DAYS),
            "start_date": rng.choice(pools.START_DATES),
            "last_day": rng.choice(pools.LAST_DAYS),
            "count": str(rng.randint(3, 25)),
            "file": rng.choice(pools.FILES),
            "mailbox": rng.choice(pools.MAILBOXES[client.sector]),
            "team": rng.choice(pools.TEAMS[client.sector]),
            "role": rng.choice([p.role for p in client.people]),
            "reference": f"{prefix}-{rng.randint(100000, 999999)}",
            "software": rng.choice(pools.SOFTWARE),
            "bad_domain": rng.choice(pools.PHISHING_DOMAINS),
            "office_phone": pools.OFFICE_PHONES[client.id],
            "domain": client.domain,
        }

    def _scope_text(self, scope: str, fills: dict[str, str]) -> str | None:
        if scope == "single":
            return (
                _fill(self.rng.choice(pools.SCOPE_SINGLE), fills)
                if self.rng.random() < 0.4
                else None
            )
        options = pools.SCOPE_TEAM if scope == "team" else pools.SCOPE_SITE
        return _fill(self.rng.choice(options), fills)

    def _pii(
        self, client: Client, caller: Person, issue: Issue
    ) -> tuple[str, list[tuple[str, str]]]:
        """A paragraph dense with personal data, and the planted values."""
        rng = self.rng
        title = rng.choice(["Mr", "Mrs", "Ms", "Dr"])
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        name = f"{title} {first} {last}"
        dob = f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1948, 2001)}"
        account = f"{rng.randint(10000000, 99999999)}"
        sort_code = f"{rng.randint(10, 99)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}"
        mobile = f"07700 900{rng.randint(0, 999):03d}"
        personal = _mailbox(first, last)
        digits = " ".join(str(rng.randint(10, 99)) for _ in range(3))
        ni = f"QQ {digits} {rng.choice('ABCD')}"
        card = rng.choice(pools.TEST_CARD_NUMBERS)
        ref = f"{pools.REFERENCE_PREFIX[client.id]}-{rng.randint(100000, 999999)}"
        if issue.service in ("lob_core", "lob_crm"):
            text = (
                f"The customer is {name}, date of birth {dob}, account number {account}, "
                f"sort code {sort_code}, reference {ref}. Their mobile is {mobile} and email "
                f"{personal} if you need to check the record."
            )
            values = [
                ("person", name),
                ("date_of_birth", dob),
                ("account_number", account),
                ("sort_code", sort_code),
                ("client_reference", ref),
                ("phone", mobile),
                ("email", personal),
            ]
        elif issue.id == "new-starter":
            text = (
                f"Their details for HR: NI number {ni}, date of birth {dob}, personal email "
                f"{personal}, personal mobile {mobile}."
            )
            values = [
                ("ni_number", ni),
                ("date_of_birth", dob),
                ("email", personal),
                ("phone", mobile),
            ]
        elif issue.category == "Security":
            text = (
                f"I'm worried because I used the same card on that site, {card}, and my "
                f"personal account {account} sort code {sort_code}."
            )
            values = [("card_number", card), ("account_number", account), ("sort_code", sort_code)]
        else:
            personal = _mailbox(caller.first_name, caller.last_name, rng.randint(1, 99))
            text = (
                f"If you can't get me on my work number, try my personal mobile {mobile} or "
                f"email me at {personal}."
            )
            values = [("phone", mobile), ("email", personal)]
        return text, values

    def _engineer(self, group: str) -> Engineer:
        return self.rng.choice([e for e in self.world.msp.engineers if e.group == group])

    # -- the plan -----------------------------------------------------------------

    def plan(self, thread_id: str, client: Client, traits: Traits) -> ThreadPlan:
        rng = self.rng
        world = self.world
        issue = rng.choice(world.issues)
        caller = rng.choice(client.people)
        reported_by = caller
        if traits.forwarded:
            reported_by = rng.choice([p for p in client.people if p.id != caller.id])
        fills = self._fills(client, caller, issue)

        scope = rng.choice(issue.scopes)
        urgency = rng.choices([1, 2, 3], weights=[3, 6, 3])[0]
        if issue.id in ("new-starter", "leaver", "software-install"):
            urgency = rng.choice([2, 3])
        stated_urgency = rng.random() < 0.85
        if not stated_urgency:
            urgency = 3  # nothing in the email suggests urgency
        options = pools.URGENCY_STATEMENTS[urgency]
        if urgency == 1:
            options = options + pools.SECTOR_URGENT[client.sector]
        urgency_text = _fill(rng.choice(options), fills) if stated_urgency else None
        scope_text = self._scope_text(scope, fills)

        problem = _fill(rng.choice(issue.problems), fills)
        details = [Fact(key=d.key, text=_fill(d.text, fills)) for d in issue.details]
        stated = [d for d in details if rng.random() < 0.5]
        missing = [d for d in details if d not in stated]
        tried = [
            _fill(t, fills)
            for t in rng.sample(issue.tried, rng.randint(0, min(2, len(issue.tried))))
        ]
        asks = [
            _fill(a, fills)
            for a in rng.sample(issue.asks, rng.choice([1, 1, 2]) if len(issue.asks) > 1 else 1)
        ]

        secondary = []
        if traits.multi_issue:
            other = rng.choice([i for i in world.issues if i.category != issue.category])
            secondary.append(
                Secondary(
                    issue_id=other.id,
                    category=other.category,
                    subcategory=other.subcategory,
                    problem=_fill(
                        rng.choice(other.problems), {**fills, **self._fills(client, caller, other)}
                    ),
                )
            )

        pii_text, pii_values = (None, [])
        if traits.pii_heavy:
            pii_text, pii_values = self._pii(client, caller, issue)

        target = world.msp.response_targets_minutes[priority(SCOPE_IMPACT[scope], urgency)]
        t0 = _business_time(rng, self.start, self.end)
        messages = [
            MessagePlan(
                index=0,
                kind="initial",
                direction="inbound",
                sender_id=reported_by.id,
                sent_at=t0,
                facts=stated[:],
                pii_text=pii_text,
            )
        ]

        # Injection goes in the first email or a later one; a signature payload is
        # placed by the messifier but planned here.
        injection_type = None
        if traits.adversarial:
            injection_type = rng.choice(list(pools.INJECTIONS))
            messages[0].injection_type = injection_type
            messages[0].injection_text = rng.choice(pools.INJECTIONS[injection_type])

        clock = t0
        ack_engineer = None
        if not traits.silence:
            ack_engineer = self._engineer("service-desk")
            clock = t0 + _minutes(target * rng.uniform(0.2, 0.9))
            messages.append(
                MessagePlan(
                    index=1,
                    kind="ack",
                    direction="outbound",
                    sender_id=ack_engineer.id,
                    sent_at=clock,
                )
            )

        def add(kind: MessageKind, gap_minutes: float, **kw) -> MessagePlan:
            nonlocal clock
            clock = clock + _minutes(gap_minutes)
            m = MessagePlan(
                index=len(messages),
                kind=kind,
                direction="inbound",
                sender_id=kw.pop("sender_id", caller.id),
                sent_at=clock,
                **kw,
            )
            messages.append(m)
            return m

        final_scope, final_scope_text, final_urgency = scope, scope_text, urgency
        # Some threads change over time: a missing detail arrives, or it gets worse.
        if missing and rng.random() < 0.25:
            fact = missing.pop(rng.randrange(len(missing)))
            add("info", rng.uniform(10, 90), facts=[fact])
            stated.append(fact)
        if scope != "site" and "site" in issue.scopes and rng.random() < 0.12:
            final_scope = "site" if scope == "team" or "team" not in issue.scopes else "team"
            final_scope_text = self._scope_text(final_scope, fills)
            final_urgency = min(urgency, 2)
            add(
                "escalation",
                rng.uniform(20, 120),
                scope_change=final_scope_text,
                urgency_change=final_urgency,
            )

        if traits.false_alarm:
            fact = (
                missing.pop(rng.randrange(len(missing)))
                if missing
                else Fact(key="extra", text=_fill("it happened again at {time}", fills))
            )
            add("false_alarm", rng.uniform(15, 240), facts=[fact])
            stated.append(fact)

        if traits.chasing:
            n = rng.choice([1, 2, 2, 3])
            for level in range(1, n + 1):
                gap = max(target * rng.uniform(0.8, 1.6), 20)
                kind: MessageKind = "resend" if level == 1 and rng.random() < 0.25 else "chase"
                add(kind, gap, frustration=level if n > 1 else rng.choice([1, 2]))

        # Adversarial follow-up instead of first email, sometimes.
        if traits.adversarial and len(messages) > 2 and rng.random() < 0.3:
            target_msg = rng.choice([m for m in messages[2:] if m.direction == "inbound"])
            target_msg.injection_type, messages[0].injection_type = messages[0].injection_type, None
            target_msg.injection_text, messages[0].injection_text = messages[0].injection_text, None

        last_inbound = max(m.sent_at for m in messages if m.direction == "inbound")
        if traits.silence:
            breach = t0 + timedelta(minutes=target)
            observed = max(breach, last_inbound) + _minutes(max(target * rng.uniform(0.5, 2), 10))
        else:
            observed = messages[-1].sent_at + _minutes(rng.uniform(2, 15))

        subject_template = rng.choice(pools.SUBJECTS)
        short = issue.short_description
        subject = _fill(subject_template, {**fills, "short": short, "short_lc": short.lower()})
        return ThreadPlan(
            thread_id=thread_id,
            split=self.split,
            client_id=client.id,
            caller_id=caller.id,
            reported_by_id=reported_by.id,
            issue_id=issue.id,
            traits=traits,
            fills=fills,
            subject=subject,
            problem=problem,
            scope=scope,
            scope_text=scope_text,
            final_scope=final_scope,
            final_scope_text=final_scope_text,
            urgency=urgency,
            final_urgency=final_urgency,
            urgency_text=urgency_text,
            tried=tried,
            asks=asks,
            details_stated=stated,
            details_missing=missing,
            secondary=secondary,
            pii_values=pii_values,
            messages=messages,
            observed_at=observed,
            ack_engineer_id=ack_engineer.id if ack_engineer else None,
        )


def plan_split(
    world: World, mix: dict, split: Literal["dev", "test"], count: int, seed: int
) -> list[ThreadPlan]:
    """Plan `count` threads, spreading them evenly across clients."""
    planner = Planner(world, mix, seed, split)
    traits = allocate_traits(count, mix["traits"], planner.rng)
    prefix = "DEV" if split == "dev" else "TST"
    clients = list(world.clients)
    plans = []
    for i in range(count):
        client = clients[i % len(clients)]
        plans.append(planner.plan(f"{prefix}-{i + 1:04d}", client, traits[i]))
    return plans
