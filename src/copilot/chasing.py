"""Chase detection and the alert policy.

Each follow-up email from the client is assessed when it arrives. A resend of an
earlier email is a chase without asking the model. Anything else goes to the model
on redacted text, with how long the client has waited, and comes back with a
verdict, a frustration level and a reason.

Alerts go to the assigned engineer. They escalate to the team lead when the client
is chasing again with no reply since the last alert, is angry or asks to escalate,
or the ticket has had no response for twice its target.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from pydantic import BaseModel, Field

from copilot.directory import Directory, load_directory
from copilot.llm import ModelClient, Redacted, email_block, load_prompt
from copilot.redaction import Redactor
from copilot.schemas import Alert, ChaseAssessment, Thread
from copilot.text import core_text, tokens

PROMPT_NAME = "assess_followup.v1"
CHASE_ABOVE = 0.5  # confidence at which a "chase" verdict raises an alert
RESEND_OVERLAP = 0.8  # share of an earlier email's words a resend repeats
ESCALATE_FRUSTRATION = 3
ESCALATE_SILENCE_FACTOR = 2


class FollowUp(BaseModel):
    is_chase: bool
    frustration: int = Field(ge=0, le=3)
    confidence: float = Field(ge=0, le=1)
    reason: str


def sent_at(email) -> datetime:
    return parsedate_to_datetime(email.date)


def _span(delta: timedelta | None) -> str:
    if delta is None:
        return "the desk has not replied yet"
    minutes = int(delta.total_seconds() // 60)
    if minutes < 120:
        return f"{minutes} minutes"
    hours = minutes // 60
    return f"{hours} hours" if hours < 48 else f"{hours // 24} days"


def is_resend(thread: Thread, index: int, support: str) -> bool:
    """True when the email repeats most of an earlier client email."""
    new = set(tokens(core_text(thread.messages[index].body)))
    for earlier in thread.messages[:index]:
        if earlier.from_address == support:
            continue
        old = set(tokens(core_text(earlier.body)))
        if len(old) >= 8 and len(old & new) / len(old) >= RESEND_OVERLAP:
            return True
    return False


class ChaseDetector:
    def __init__(self, client: ModelClient, directory: Directory | None = None):
        self.model = client
        self.directory = directory or load_directory()
        self.prompt = load_prompt(PROMPT_NAME)

    def assess(self, thread: Thread, index: int, redactor: Redactor) -> tuple[ChaseAssessment, int]:
        """The verdict on email `index`, and the client's frustration from 0 to 3."""
        d = self.directory
        if is_resend(thread, index, d.support_address):
            return (
                ChaseAssessment(
                    message_index=index,
                    is_chase=True,
                    confidence=0.95,
                    reason="Resends an earlier email.",
                ),
                1,
            )
        nonce = self.model.new_nonce()
        now = sent_at(thread.messages[index])
        replies = [
            sent_at(e) for e in thread.messages[:index] if e.from_address == d.support_address
        ]
        blocks = []
        for i, email in enumerate(thread.messages[: index + 1]):
            who = "the service desk" if email.from_address == d.support_address else "the client"
            body = Redacted(redactor.redact(core_text(email.body)))
            blocks.append(email_block(nonce, f"Message {i} from {who}, {email.date}", body))
        user = self.prompt["user"].format(
            since_first=_span(now - sent_at(thread.messages[0])),
            since_reply=_span(now - replies[-1]) if replies else _span(None),
            earlier="\n\n".join(blocks[:-1]),
            newest=blocks[-1],
        )
        out = self.model.complete(
            system=self.prompt["system"].format(msp_name=d.msp_name),
            user=Redacted(user),
            schema=FollowUp,
            prompt_version=PROMPT_NAME,
            nonce=nonce,
        )
        chase = out.is_chase and out.confidence >= CHASE_ABOVE
        return (
            ChaseAssessment(
                message_index=index,
                is_chase=chase,
                confidence=out.confidence,
                reason=redactor.restore(out.reason).strip().split("\n")[0][:200],
            ),
            out.frustration,
        )


def chase_alerts(
    thread: Thread, assessments: list[tuple[ChaseAssessment, int]], support: str
) -> list[Alert]:
    """One alert per chase, escalated to the team lead when the rules say so."""
    alerts: list[Alert] = []
    last_alert_at: datetime | None = None
    for assessment, frustration in assessments:
        if not assessment.is_chase:
            continue
        email = thread.messages[assessment.message_index]
        at = sent_at(email)
        replied_since = last_alert_at is not None and any(
            e.from_address == support and last_alert_at <= sent_at(e) <= at for e in thread.messages
        )
        repeat = last_alert_at is not None and not replied_since
        escalate = repeat or frustration >= ESCALATE_FRUSTRATION
        why = assessment.reason
        if repeat:
            why += " Chasing again with no reply since the last alert."
        elif frustration >= ESCALATE_FRUSTRATION:
            why += " The client is angry or asking to escalate."
        alerts.append(
            Alert(
                kind="chase",
                message_index=assessment.message_index,
                at=at,
                notify="team_lead" if escalate else "engineer",
                reason=why,
            )
        )
        last_alert_at = at
    return alerts


def escalate_silence(alert: Alert | None, now: datetime, target_minutes: int) -> Alert | None:
    """Send a silence alert to the team lead once the wait is twice the target."""
    if alert is None:
        return None
    waited = now - (alert.at - timedelta(minutes=target_minutes))
    if waited >= timedelta(minutes=target_minutes * ESCALATE_SILENCE_FACTOR):
        return alert.model_copy(
            update={
                "notify": "team_lead",
                "reason": alert.reason + " Still no response at twice the target.",
            }
        )
    return alert
