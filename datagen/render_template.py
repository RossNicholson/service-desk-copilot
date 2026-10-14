"""Template renderer: writes the core text of each email from a thread plan.

It needs no API key, so it is used for the committed dataset and in tests. The
core text is what the sender typed; signatures, quoted history, forwards and
typos are added afterwards by the messifier.
"""

from __future__ import annotations

import random
from datetime import datetime

from pydantic import BaseModel

from datagen import pools
from datagen.plan import LONDON, MessagePlan, ThreadPlan
from datagen.world import World


class Rendered(BaseModel):
    """Core text of each message, plus the caller's original email when forwarded."""

    bodies: list[str]
    original: str | None = None


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


QUESTION_STARTS = ("Can ", "Could ", "Is ", "Please can ", "Would ", "Any ", "Has ")


def _sentence(text: str) -> str:
    text = text.strip()
    if text.endswith((".", "!", "?", "]", ">", '."', '!"', '?"')):
        return text
    return text + ("?" if text.startswith(QUESTION_STARTS) else ".")


def _greeting(rng: random.Random, tone: str, sent_at: datetime) -> str:
    """A greeting that suits the time of day the email was sent."""
    hour = sent_at.astimezone(LONDON).hour
    wrong = "afternoon" if hour < 12 else "morning"
    return rng.choice([g for g in pools.GREETINGS[tone] if wrong not in g.lower()])


class TemplateRenderer:
    name = "template"
    prompt_version = None
    model = None

    def __init__(self, world: World, seed: int):
        self.world = world
        self.rng = random.Random(seed)
        self.cost_usd = 0.0

    def render(self, plan: ThreadPlan) -> Rendered:
        bodies = [self._message(plan, m) for m in plan.messages]
        original = self.render_original(plan) if plan.traits.forwarded else None
        return Rendered(bodies=bodies, original=original)

    def _person(self, plan: ThreadPlan, person_id: str):
        client = self.world.client(plan.client_id)
        return next(p for p in client.people if p.id == person_id)

    def _message(self, plan: ThreadPlan, m: MessagePlan) -> str:
        if m.kind == "ack":
            return self._ack(plan, m)
        sender = self._person(plan, m.sender_id)
        tone = sender.persona.tone.value
        rng = self.rng
        parts: list[str] = []

        if m.kind == "initial":
            parts = self._initial(plan, m, tone)
        elif m.kind in ("chase", "resend"):
            pool = pools.RESEND_CHASE if m.kind == "resend" else pools.CHASES[max(1, m.frustration)]
            parts = [rng.choice(pool)]
        elif m.kind == "info":
            parts = [rng.choice(pools.INFO_FOLLOWUPS).format(detail=_lower_first(m.facts[0].text))]
        elif m.kind == "false_alarm":
            detail = m.facts[0].text
            parts = [
                rng.choice(pools.FALSE_ALARMS).format(
                    detail=_lower_first(detail), detail_cap=_cap(detail)
                )
            ]
        elif m.kind == "escalation":
            change = m.scope_change or ""
            parts = [
                rng.choice(pools.ESCALATIONS).format(
                    scope_text=_lower_first(change), scope_text_cap=_cap(change)
                )
            ]
            if m.urgency_change == 1 or rng.random() < 0.5:
                parts.append("We really need someone on this now.")

        forwarded_note = m.kind == "initial" and plan.traits.forwarded
        if m.injection_text and m.injection_type != "signature_payload" and not forwarded_note:
            parts.insert(rng.randint(0, len(parts)), m.injection_text)

        greeting = (
            _greeting(rng, tone, m.sent_at) if m.kind == "initial" or rng.random() < 0.5 else ""
        )
        closing = rng.choice(pools.CLOSINGS[tone])
        sign = sender.first_name if rng.random() < 0.8 else sender.first_name[0]
        body = (
            " ".join(_sentence(p) for p in parts)
            if tone == "terse"
            else "\n\n".join(_sentence(p) for p in parts)
        )
        lines = [greeting, body, f"{closing}\n{sign}"]
        return "\n\n".join(line for line in lines if line)

    def _initial(self, plan: ThreadPlan, m: MessagePlan, tone: str) -> list[str]:
        rng = self.rng
        if plan.traits.forwarded:
            # The forwarder's note; the caller's original email is embedded by the messifier.
            caller = self._person(plan, plan.caller_id)
            return [rng.choice(pools.FORWARD_NOTES).format(caller_first=caller.first_name)]
        return self.original_parts(plan, m, tone)

    def original_parts(self, plan: ThreadPlan, m: MessagePlan, tone: str) -> list[str]:
        """The caller's own description of the problem."""
        rng = self.rng
        parts = [plan.problem]
        if tone == "anxious":
            parts[0] = parts[0] + ", and I'm not sure what to do"
        if plan.scope_text:
            parts.append(plan.scope_text)
        parts += [f.text for f in m.facts]
        parts += plan.tried
        if plan.urgency_text:
            parts.append(plan.urgency_text)
        parts += plan.asks
        if tone == "casual" and len(parts) > 3:
            middle = parts[1:-1]
            rng.shuffle(middle)
            parts = [parts[0], *middle, parts[-1]]
        for s in plan.secondary:
            parts.append(
                rng.choice(pools.SECONDARY_LEADS).format(
                    problem=s.problem, problem_lc=_lower_first(s.problem)
                )
            )
        if m.pii_text:
            parts.append(m.pii_text)
        return parts

    def render_original(self, plan: ThreadPlan) -> str:
        """Text of the caller's original email inside a forward."""
        caller = self._person(plan, plan.caller_id)
        tone = caller.persona.tone.value
        parts = self.original_parts(plan, plan.messages[0], tone)
        if (
            plan.messages[0].injection_text
            and plan.messages[0].injection_type != "signature_payload"
        ):
            parts.insert(self.rng.randint(0, len(parts)), plan.messages[0].injection_text)
        body = "\n\n".join(_sentence(p) for p in parts)
        forwarder = self._person(plan, plan.reported_by_id)
        greeting = self.rng.choice(
            [f"Hi {forwarder.first_name},", f"{forwarder.first_name},", "Hi,"]
        )
        return f"{greeting}\n\n{body}\n\n{caller.first_name}"

    def _ack(self, plan: ThreadPlan, m: MessagePlan) -> str:
        engineer = next(e for e in self.world.msp.engineers if e.id == m.sender_id)
        recipient = self._person(plan, plan.reported_by_id)
        issue = self.world.issue(plan.issue_id)
        return self.rng.choice(pools.ACKS).format(
            first=recipient.first_name,
            engineer=engineer.name,
            group=self.world.group_name(issue.group),
        )
