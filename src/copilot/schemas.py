"""The copilot's contract: what goes in (email threads) and what comes out.

Every system the evaluation harness scores, the baseline included, takes a
`Thread` and returns a `ThreadResult`. Nothing in a result can send an email or
close a ticket: the copilot only proposes field values, notes and alerts, and
engineers stay in control.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FIELD_NAMES = (
    "caller",
    "client_company",
    "category",
    "subcategory",
    "affected_service",
    "impact",
    "urgency",
    "priority",
    "assignment_group",
    "short_description",
)

# ---------------------------------------------------------------------------
# Input: email threads, exactly as an engineer would see them
# ---------------------------------------------------------------------------


class Email(BaseModel):
    message_id: str
    in_reply_to: str | None = None
    from_name: str
    from_address: str
    to: list[str]
    cc: list[str] = []
    date: str = Field(description="RFC 2822 Date header as sent.")
    subject: str
    body: str


class Thread(BaseModel):
    thread_id: str
    messages: list[Email]


# ---------------------------------------------------------------------------
# Output: what a system proposes for a thread
# ---------------------------------------------------------------------------


class FieldPrediction(BaseModel):
    value: str | int | None = Field(description="None when the system declines to guess.")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(description="One line explaining the value.")
    needs_review: bool = Field(description="Flagged for a human rather than trusted.")


class FieldChange(BaseModel):
    """A field value set or changed after reading a given email (a check-in)."""

    message_index: int
    field: str
    value: str | int | None
    reason: str


class StructuredNote(BaseModel):
    """A note written from the emails up to a given point. Facts stated only."""

    message_index: int = Field(description="The last email this note takes into account.")
    reported: str
    affected: str
    tried: list[str] = []
    asks: list[str] = []
    deadline_or_impact: str | None = None
    open_questions: list[str] = []


class ChaseAssessment(BaseModel):
    message_index: int
    is_chase: bool
    confidence: float = Field(ge=0, le=1)
    reason: str


class Alert(BaseModel):
    kind: Literal["chase", "silence"]
    message_index: int | None = None
    at: datetime
    notify: Literal["engineer", "team_lead"] = "engineer"
    reason: str


class Usage(BaseModel):
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class Suggestion(BaseModel):
    """The AI suggestion email for a new ticket: drafted, skipped by the rules, or dropped."""

    status: Literal["drafted", "skipped", "dropped"]
    reason: str
    steps: list[str] = []


class ThreadResult(BaseModel):
    thread_id: str
    system: str
    fields: dict[str, FieldPrediction] = Field(description="Final value for each ticket field.")
    field_changes: list[FieldChange] = []
    notes: list[StructuredNote] = []
    chase_assessments: list[ChaseAssessment] = []
    alerts: list[Alert] = []
    suggestion: Suggestion | None = Field(
        default=None, description="Only systems that write suggestion emails set this."
    )
    model_inputs: list[str] = Field(
        default=[],
        description="Every piece of text sent to a model, recorded for the PII leakage check.",
    )
    usage: Usage = Usage()
