"""Pydantic models for the fictional world used to generate data.

Threads (what an engineer or the copilot sees) live in `copilot.schemas`, and
labels (the ground truth) in `copilot.evaluation.dataset`. They are re-exported
here for the generator's convenience.
"""

from __future__ import annotations

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


# Re-exported so generator code can import everything from one place.
from copilot.evaluation.dataset import (  # noqa: E402
    ExpectedAlert,
    MessageLabel,
    NoteFacts,
    PiiItem,
    RenderInfo,
    SecondaryIssue,
    ThreadLabels,
    TicketFields,
    Timing,
    Traits,
)
from copilot.schemas import Email, Thread  # noqa: E402

__all__ = [
    "Client",
    "Email",
    "Engineer",
    "ExpectedAlert",
    "Group",
    "Issue",
    "IssueDetail",
    "MessageLabel",
    "Msp",
    "NoteFacts",
    "Person",
    "Persona",
    "PiiItem",
    "RenderInfo",
    "SecondaryIssue",
    "Thread",
    "ThreadLabels",
    "TicketFields",
    "Timing",
    "Tone",
    "Traits",
]
