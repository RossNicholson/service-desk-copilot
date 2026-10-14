"""Structured notes: what the client said, and what the desk still needs to ask.

The model fills in each part of the note from redacted emails, and must back every
item with words quoted from a client's email. Items whose quote does not appear in
the emails are dropped, so an invented detail cannot reach the note. Open questions
are chosen from the desk's triage checklist (`knowledge/triage.yaml`) for the
ticket's subcategory, so the copilot asks what the desk actually needs.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from copilot.directory import Directory, load_directory
from copilot.llm import ModelClient, Redacted, email_block, load_prompt
from copilot.redaction import Redactor
from copilot.schemas import StructuredNote, Thread

PROMPT_NAME = "write_note.v1"
TRIAGE = Path(__file__).parent / "knowledge" / "triage.yaml"
MIN_QUOTE_WORDS = 2


class Item(BaseModel):
    text: str
    quote: str


class NoteOut(BaseModel):
    reported: Item | None = None
    affected: Item | None = None
    tried: list[Item] = []
    asks: list[Item] = []
    deadline_or_impact: Item | None = None
    missing: list[int] = []


@lru_cache
def triage() -> dict[str, list[str]]:
    return yaml.safe_load(TRIAGE.read_text(encoding="utf-8"))


def checklist(subcategory: str | None) -> list[str]:
    data = triage()
    return [*data["always"], *data.get(subcategory or "", [])]


def _squash(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9\[\]_']+", text.lower()))


class NoteWriter:
    def __init__(self, client: ModelClient, directory: Directory | None = None):
        self.model = client
        self.directory = directory or load_directory()
        self.prompt = load_prompt(PROMPT_NAME)

    def write(
        self, thread: Thread, upto: int, redactor: Redactor, subcategory: str | None
    ) -> StructuredNote:
        d = self.directory
        nonce = self.model.new_nonce()
        blocks, client_text = [], []
        for index, email in enumerate(thread.messages[: upto + 1]):
            body = redactor.redact(email.body)
            if email.from_address == d.support_address:
                who = f"{d.msp_name} service desk (not the client)"
            else:
                who = redactor.redact(f"{email.from_name} <{email.from_address}>")
                client_text.append(body)
            header = f"Message {index}\nFrom: {who}\nSubject: {redactor.redact(email.subject)}"
            blocks.append(email_block(nonce, header, Redacted(body)))
        questions = checklist(subcategory)
        user = self.prompt["user"].format(
            subcategory=subcategory or "not yet known",
            checklist="\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1)),
            emails="\n\n".join(blocks),
        )
        out = self.model.complete(
            system=self.prompt["system"].format(msp_name=d.msp_name),
            user=Redacted(user),
            schema=NoteOut,
            prompt_version=PROMPT_NAME,
            nonce=nonce,
        )
        source = _squash("\n".join(client_text))

        def keep(item: Item | None) -> str | None:
            """The item's text, restored, if its quote really is in a client's email."""
            if item is None or not item.text.strip():
                return None
            quote = _squash(item.quote)
            if len(quote.split()) < MIN_QUOTE_WORDS or quote not in source:
                return None
            return redactor.restore(item.text.strip())

        missing = sorted({n for n in out.missing if 1 <= n <= len(questions)})
        return StructuredNote(
            message_index=upto,
            reported=keep(out.reported) or "",
            affected=keep(out.affected) or "",
            tried=[t for t in map(keep, out.tried) if t],
            asks=[a for a in map(keep, out.asks) if a],
            deadline_or_impact=keep(out.deadline_or_impact),
            open_questions=[questions[n - 1] for n in missing],
        )
