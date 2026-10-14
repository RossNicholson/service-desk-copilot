"""The labelled dataset format, and loading it.

A split lives in `data/<split>/` as two JSON Lines files with one object per
thread, in the same order:

- `threads.jsonl`: `copilot.schemas.Thread`, the emails only.
- `labels.jsonl`: `ThreadLabels` below, the ground truth.

Keeping them apart means a system under evaluation is only ever handed threads.
`LABEL_FORMAT` is bumped whenever the label models change in a way that breaks
older files; `data/MANIFEST.json` records the version the data was written with.

Only the evaluation harness may read the `test` split.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from copilot.schemas import Thread

LABEL_FORMAT = "1"
DATA_DIR = Path(__file__).resolve().parents[3] / "data"

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
    escalation: bool = Field(default=False, description="The problem spreads in a later email.")


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


class LabelledThread(BaseModel):
    thread: Thread
    labels: ThreadLabels


def load_split(split: Literal["dev", "test"], data_dir: Path = DATA_DIR) -> list[LabelledThread]:
    """Load a split, checking threads and labels line up and the format matches."""
    folder = data_dir / split
    manifest = data_dir / "MANIFEST.json"
    if manifest.exists():
        found = json.loads(manifest.read_text()).get("label_format")
        if found not in (None, LABEL_FORMAT):
            raise ValueError(f"data uses label format {found}, this code reads {LABEL_FORMAT}")
    threads = [
        Thread.model_validate_json(line)
        for line in (folder / "threads.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    labels = [
        ThreadLabels.model_validate_json(line)
        for line in (folder / "labels.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if [t.thread_id for t in threads] != [lab.thread_id for lab in labels]:
        raise ValueError(f"{folder}: threads and labels do not line up")
    return [LabelledThread(thread=t, labels=lab) for t, lab in zip(threads, labels, strict=True)]
