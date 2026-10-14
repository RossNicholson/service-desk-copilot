"""AI suggestion emails: a few safe things to try while an engineer has the ticket.

The rules (agreed with Ross, see CLAUDE.md) are applied in this order:

1. Only a new ticket's first email, before the desk has replied.
2. Skipped for P1 and P2, security, account access and lost device tickets,
   requests rather than faults, low-confidence routing, and any thread that looks
   like it is trying to instruct the AI.
3. The model writes only the steps, from redacted text. Everything else in the
   email (that AI wrote it, that these are suggestions only, that a named team
   has the ticket and will reply) is fixed text.
4. The steps are checked: no links, no requests for credentials or personal
   details, nothing that needs admin rights, no placeholders. A draft that fails
   is dropped, never edited.

Whether a drafted suggestion is sent is decided in `copilot.service`: it must be
switched on globally and for the client, not turned off for the ticket, and the
harness must have scored it.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from copilot.directory import Directory, load_directory
from copilot.injection import suspected_injection
from copilot.llm import ModelClient, Redacted, email_block, load_prompt
from copilot.redaction import Redactor
from copilot.schemas import FieldPrediction, Suggestion, Thread
from copilot.text import core_text

PROMPT_NAME = "suggest_steps.v1"
SKIP_PRIORITIES = {"P1", "P2"}
SKIP_CATEGORIES = {"Security", "Access and Identity"}
SKIP_SUBCATEGORIES = {"Lost device", "Install request", "New starter", "Leaver"}
ROUTING_CONFIDENCE = 0.6  # below this the ticket may be in the wrong place
MAX_STEPS = 4
MAX_STEP_CHARS = 220


def _words(*words: str) -> re.Pattern:
    return re.compile("|".join(words), re.I)


UNSAFE: dict[str, re.Pattern] = {
    "a link": _words(
        r"https?://",
        r"www\.",
        r"\b[\w-]+\.(?:com|co\.uk|org|net|io|example|uk|gov|me)\b",
        r"@",
    ),
    "a request for credentials or personal details": _words(
        r"\bpass(?:word|code|phrase)s?\b",
        r"\bpin\b",
        r"\bone[- ]time\b",
        r"\b(?:mfa|2fa|otp)\b",
        r"\bverification code\b",
        r"(?<!error )\bcodes?\b",
        r"\bdate of birth\b",
        r"\bbank\b",
        r"\bcard (?:number|details)\b",
        r"\baccount (?:number|details)\b",
        r"\bsort code\b",
        r"\bsecurity questions?\b",
        r"\bsend (?:us|me) (?:your|a)\b",
        r"\bshare (?:your|the)\b",
        r"\breply with\b",
    ),
    "something that needs admin rights or changes security": _words(
        r"\badmin(?:istrator)?\b",
        r"\bregistry\b",
        r"\bregedit\b",
        r"\bcommand prompt\b",
        r"\bcmd\b",
        r"\bpowershell\b",
        r"\bterminal\b",
        r"\bsudo\b",
        r"\bbios\b",
        r"\banti-?virus\b",
        r"\bfirewall\b",
        r"\bdefender\b",
        r"\b(?:install|uninstall|reinstall|download)\b",
        r"\bdisable\b",
        r"\bturn off (?:security|protection)\b",
        r"\bdns\b",
        r"\bip address\b",
        r"\bproxy\b",
        r"\bgroup policy\b",
        r"\bformat\b",
        r"\b(?:factory )?reset (?:your )?(?:pc|laptop|computer|phone|device)\b",
    ),
    "a placeholder": re.compile(r"\[[A-Z_]+(?:_\d+)?\]|\{[^}]*\}|<[^>]*>|\bTODO\b|\bXXX\b"),
    "a promise": _words(
        r"\bwill (?:fix|be fixed|resolve|be resolved)\b",
        r"\bguarantee",
        r"\bwithin \d",
        r"\bby (?:today|tomorrow)\b",
        r"\bwe(?:'ll| will) (?:close|update|change)\b",
    ),
}


class StepsOut(BaseModel):
    steps: list[str]
    worth_sending: bool
    reason: str


def skip_reason(thread: Thread, fields: dict[str, FieldPrediction], support: str) -> str | None:
    """Why this ticket gets no suggestion email, or None if it may get one."""
    inbound = [e for e in thread.messages if e.from_address != support]
    if len(inbound) != 1 or len(thread.messages) != 1:
        return "Only a new ticket's first email gets suggestions."
    for email in inbound:
        found = suspected_injection(f"{email.subject}\n{email.body}")
        if found:
            return (
                f"The email looks like it is trying to instruct the AI ({found.replace('_', ' ')})."
            )

    def value(name):
        f = fields.get(name)
        return f.value if f else None

    if value("priority") in SKIP_PRIORITIES:
        return f"{value('priority')} tickets get no suggestions."
    if value("category") in SKIP_CATEGORIES:
        return f"{value('category')} tickets get no suggestions."
    if value("subcategory") in SKIP_SUBCATEGORIES:
        return f"{value('subcategory')} tickets get no suggestions."
    routing = [fields.get(n) for n in ("category", "subcategory", "priority")]
    if any(f is None or f.value is None or f.needs_review for f in routing) or any(
        f.confidence < ROUTING_CONFIDENCE for f in routing if f
    ):
        return "The AI is not confident enough about what the ticket is."
    return None


def check_steps(steps: list[str]) -> str | None:
    """Why the drafted steps cannot be sent, or None if they pass."""
    if not 1 <= len(steps) <= MAX_STEPS:
        return f"The draft had {len(steps)} steps."
    for step in steps:
        if not step.strip() or len(step) > MAX_STEP_CHARS:
            return "A step was empty or too long."
        for what, pattern in UNSAFE.items():
            if pattern.search(step):
                return f"A step contained {what}."
    return None


class SuggestionWriter:
    def __init__(self, client: ModelClient, directory: Directory | None = None):
        self.model = client
        self.directory = directory or load_directory()
        self.prompt = load_prompt(PROMPT_NAME)

    def draft(
        self, thread: Thread, redactor: Redactor, fields: dict[str, FieldPrediction]
    ) -> Suggestion:
        d = self.directory
        reason = skip_reason(thread, fields, d.support_address)
        if reason:
            return Suggestion(status="skipped", reason=reason)
        email = thread.messages[0]
        nonce = self.model.new_nonce()
        body = Redacted(redactor.redact(core_text(email.body)))
        block = email_block(nonce, f"Subject: {redactor.redact(email.subject)}", body)
        out = self.model.complete(
            system=self.prompt["system"].format(msp_name=d.msp_name),
            user=Redacted(
                self.prompt["user"].format(subcategory=fields["subcategory"].value, email=block)
            ),
            schema=StepsOut,
            prompt_version=PROMPT_NAME,
            nonce=nonce,
        )
        if not out.worth_sending or not out.steps:
            return Suggestion(status="skipped", reason="The AI had no safe steps to suggest.")
        steps = [s.strip() for s in out.steps]
        failed = check_steps(steps)
        if failed:
            return Suggestion(status="dropped", reason=f"Draft dropped: {failed}", steps=steps)
        return Suggestion(status="drafted", reason=out.reason.strip()[:200], steps=steps)


def compose(
    steps: list[str],
    first_name: str | None,
    team: str,
    msp_name: str,
    fixed_link: str | None,
) -> str:
    """The whole email. Only the numbered steps come from the model."""
    greeting = f"Hello {first_name}," if first_name else "Hello,"
    lines = [
        greeting,
        "",
        "This email was written by AI, not by a person, and it contains suggestions only.",
        "",
        f"Your request is with the {team} team at {msp_name}, and an engineer will reply "
        "to you. You do not need to do anything below, but while you wait, these steps "
        "sometimes help:",
        "",
        *[f"{i}. {step}" for i, step in enumerate(steps, start=1)],
        "",
    ]
    if fixed_link:
        lines += [
            "If one of these fixed it, you can let us know here and we will close your ticket:",
            fixed_link,
            "The link opens a page with a button. Nothing changes until you press it.",
            "",
        ]
    lines += [
        f"If they do not help, there is no need to reply. Your ticket stays with the {team} "
        "team either way.",
        "",
        f"{msp_name} service desk (AI assistant)",
    ]
    return "\n".join(lines)


def compose_problems(text: str) -> str | None:
    """A last check on the finished email: nothing left unfilled."""
    if UNSAFE["a placeholder"].search(text):
        return "The email still had a placeholder."
    return None
