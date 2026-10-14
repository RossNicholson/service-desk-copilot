"""Pydantic models for the fictional world, the generated threads and their labels.

Threads (what an engineer or the copilot sees) and labels (the ground truth) are
kept in separate models and separate files, so a model under evaluation can be
given threads without any chance of seeing the answers.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# The fictional world
# ---------------------------------------------------------------------------


class Tone(StrEnum):
    FORMAL = "formal"
    CASUAL = "casual"
    TERSE = "terse"
    ANXIOUS = "anxious"


class Persona(BaseModel):
    tone: Tone
    typo_rate: float = Field(ge=0, le=0.2, description="Chance per word of a typo.")
    mobile_share: float = Field(ge=0, le=1, description="Chance an email is sent from a phone.")


class Person(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    role: str
    phone: str | None = None
    mobile: str | None = None
    vip: bool = False
    persona: Persona

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Client(BaseModel):
    id: str
    name: str
    short_name: str
    sector: str
    domain: str
    sites: list[str]
    contract: Literal["essentials", "professional", "premier"]
    disclaimer: str
    services: dict[str, str] = Field(description="Service key to the client's name for it.")
    people: list[Person]


class Group(BaseModel):
    key: str
    name: str
    lead: str = Field(description="Engineer id of the team lead.")


class Engineer(BaseModel):
    id: str
    name: str
    email: str
    group: str


class Msp(BaseModel):
    name: str
    domain: str
    support_address: str
    response_targets_minutes: dict[str, int]
    groups: list[Group]
    engineers: list[Engineer]


class IssueDetail(BaseModel):
    key: str
    text: str
    question: str = Field(description="Open question to ask when this detail is missing.")


class Issue(BaseModel):
    id: str
    category: str
    subcategory: str
    service: str
    group: str
    short_description: str
    scopes: list[Literal["single", "team", "site"]]
    problems: list[str]
    details: list[IssueDetail]
    tried: list[str]
    asks: list[str]


# ---------------------------------------------------------------------------
# Generated threads (no labels in here)
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
# Labels (ground truth)
# ---------------------------------------------------------------------------


class TicketFields(BaseModel):
    caller: str = Field(description="Caller's email address.")
    client_company: str
    category: str
    subcategory: str
    affected_service: str
    impact: int = Field(ge=1, le=3, description="1 high (site or firm), 2 team, 3 one person.")
    urgency: int = Field(ge=1, le=3)
    priority: str = Field(pattern=r"^P[1-5]$")
    assignment_group: str
    short_description: str = Field(description="Reference wording; scored for meaning.")


class NoteFacts(BaseModel):
    """What a faithful note for the whole thread must contain, and nothing more."""

    reported: str
    affected: str
    tried: list[str]
    asks: list[str]
    deadline_or_impact: str | None
    details: list[str] = Field(description="Specific facts stated, such as an error message.")
    open_questions: list[str]


class SecondaryIssue(BaseModel):
    issue_id: str
    category: str
    subcategory: str
    summary: str


class Traits(BaseModel):
    forwarded: bool = False
    multi_issue: bool = False
    chasing: bool = False
    false_alarm: bool = False
    silence: bool = False
    adversarial: bool = False
    pii_heavy: bool = False


class MessageLabel(BaseModel):
    index: int
    direction: Literal["inbound", "outbound"]
    is_chase: bool = False
    frustration: int = Field(default=0, ge=0, le=3)
    false_alarm: bool = Field(default=False, description="Chase-like wording that is not a chase.")
    injection: str | None = Field(
        default=None, description="Injection type, if this email has one."
    )
    field_updates: dict[str, str | int] = Field(
        default={}, description="Ticket fields this email changes, such as a wider impact."
    )


class PiiItem(BaseModel):
    type: Literal[
        "person",
        "email",
        "phone",
        "account_number",
        "sort_code",
        "card_number",
        "ni_number",
        "date_of_birth",
        "client_reference",
    ]
    value: str


class Timing(BaseModel):
    first_inbound_at: datetime
    response_target_minutes: int
    first_response_at: datetime | None
    observed_at: datetime = Field(description="The 'now' at which alerts are judged.")
    response_breached: bool
    breach_at: datetime | None


class ExpectedAlert(BaseModel):
    kind: Literal["chase", "silence"]
    message_index: int | None = None
    at: datetime


class RenderInfo(BaseModel):
    renderer: Literal["template", "model"]
    prompt_version: str | None = None
    model: str | None = None


class ThreadLabels(BaseModel):
    thread_id: str
    split: Literal["dev", "test"]
    client_id: str
    caller_id: str
    reported_by_id: str = Field(description="Who sent the first email (differs when forwarded).")
    issue_id: str
    fields: TicketFields
    note: NoteFacts
    secondary_issues: list[SecondaryIssue] = []
    traits: Traits
    messages: list[MessageLabel]
    pii: list[PiiItem] = []
    timing: Timing
    expected_alerts: list[ExpectedAlert] = []
    render: RenderInfo
