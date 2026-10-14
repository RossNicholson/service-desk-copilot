"""A simple rule-based baseline: what a few hours of keyword rules achieves.

It uses no model, so it costs nothing and sends no text anywhere. Every number
the copilot reports is compared against it. It is deliberately naive:

- the caller is whoever sent the first email (wrong for forwarded emails);
- category comes from the first keyword that matches;
- impact and urgency come from a handful of phrases;
- notes copy sentences that look relevant, with no open questions;
- any follow-up containing "update", "chasing", "still" or "again" is a chase.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from copilot.directory import Directory, load_directory
from copilot.priority import priority
from copilot.schemas import (
    Alert,
    ChaseAssessment,
    FieldChange,
    FieldPrediction,
    StructuredNote,
    Thread,
    ThreadResult,
)
from copilot.text import core_text, forwarded_text, sentences

# (pattern, subcategory, service key). First match wins, so order matters.
KEYWORDS: list[tuple[str, str, str]] = [
    (r"phish|dodgy email|strange email", "Phishing report", "security"),
    (
        r"mfa prompt|approve sign-?ins|signed in to my account from",
        "Suspicious sign-in",
        "accounts",
    ),
    (r"lost|stolen|left my work phone", "Lost device", "mobile"),
    (r"locked", "Account locked", "accounts"),
    (r"authenticator|mfa", "Multi-factor authentication", "mfa"),
    (r"new starter|new user|starts with us|joining us", "New starter", "accounts"),
    (r"leaver|leaving|left the business", "Leaver", "accounts"),
    (r"shared mailbox|mailbox", "Shared mailbox", "email"),
    (r"outlook", "Outlook", "email"),
    (r"teams|hear me|hear anyone", "Teams", "teams"),
    (r"sharepoint|access denied|request access", "SharePoint and OneDrive", "sharepoint"),
    (r"monitor|screen|dock", "Monitors and peripherals", "laptop"),
    (r"laptop|won't turn on|black screen", "Laptop", "laptop"),
    (r"print", "Printer", "printer"),
    (r"vpn", "VPN", "vpn"),
    (r"internet|router|nothing online", "Internet outage", "internet"),
    (r"wi-?fi", "Wi-Fi", "wifi"),
    (r"install", "Install request", "office_apps"),
    (r"excel|spreadsheet|\.xlsx", "Microsoft 365 apps", "office_apps"),
    (r"calls|phones|ringing|busy tone", "Phone system", "phone"),
    (r"deleted|restore|overwritten|disappeared", "Backup and restore", "backup"),
    (r"shared drive|s: drive", "File share", "file_share"),
    (r"crm|record", "Business application", "lob_crm"),
    (r"error|save|submit|times out", "Business application", "lob_core"),
]

SITE_WORDS = re.compile(r"everyone at|whole of|nobody can|all of us here|whole office", re.I)
TEAM_WORDS = re.compile(r"whole .+ team|all of .+, about|several of us|\d+ of us", re.I)
URGENT_WORDS = re.compile(r"urgent|asap|today|deadline|before then|by \d|tomorrow morning", re.I)
RELAXED_WORDS = re.compile(r"no rush|not urgent|whenever|workaround|not stopping me", re.I)
CHASE_WORDS = re.compile(r"update|chasing|still|again|resend|sending this again", re.I)
TRIED_WORDS = re.compile(r"\b(?:i've|we've|i|we) (?:tried|restarted|rebooted|checked|turned)", re.I)
DEADLINE_WORDS = re.compile(r"today|deadline|by \d|tomorrow|meeting|before", re.I)
PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)+", re.I)

CONFIDENCE_HIT = 0.6
CONFIDENCE_MISS = 0.2
REVIEW_BELOW = 0.5


def _prediction(value, confident: bool, reason: str) -> FieldPrediction:
    confidence = CONFIDENCE_HIT if confident else CONFIDENCE_MISS
    return FieldPrediction(
        value=value, confidence=confidence, reason=reason, needs_review=confidence < REVIEW_BELOW
    )


class BaselineCopilot:
    name = "baseline"

    def __init__(self, directory: Directory | None = None):
        self.directory = directory or load_directory()

    # -- fields -------------------------------------------------------------------

    def _fields(self, thread: Thread, upto: int) -> dict[str, FieldPrediction]:
        d = self.directory
        inbound = [e for e in thread.messages[: upto + 1] if e.from_address != d.support_address]
        first = inbound[0]
        text = "\n".join(f"{core_text(e.body)}\n{forwarded_text(e.body)}" for e in inbound)
        fields = {
            "caller": _prediction(first.from_address, True, "Sender of the first email."),
        }
        client = d.client_for_address(first.from_address)
        fields["client_company"] = _prediction(
            client.name if client else None, client is not None, "Matched the sender's domain."
        )

        match = next(
            ((sub, svc) for pattern, sub, svc in KEYWORDS if re.search(pattern, text, re.I)), None
        )
        if match:
            subcategory, service = match
            route = next(
                r for r in d.routes if r.subcategory == subcategory and r.service == service
            )
            reason = f"Keyword rule for {subcategory.lower()}."
            fields["category"] = _prediction(route.category, True, reason)
            fields["subcategory"] = _prediction(subcategory, True, reason)
            service_name = client.services.get(service) if client else None
            fields["affected_service"] = _prediction(service_name, client is not None, reason)
            fields["assignment_group"] = _prediction(d.team_name(route.group), True, reason)
        else:
            for name in ("category", "subcategory", "affected_service"):
                fields[name] = _prediction(None, False, "No keyword rule matched.")
            fields["assignment_group"] = _prediction(
                d.team_name("service-desk"), False, "No rule matched, so the service desk."
            )

        impact = 1 if SITE_WORDS.search(text) else 2 if TEAM_WORDS.search(text) else 3
        urgency = 1 if URGENT_WORDS.search(text) else 3 if RELAXED_WORDS.search(text) else 2
        fields["impact"] = _prediction(impact, impact != 3, "Phrases about who is affected.")
        fields["urgency"] = _prediction(urgency, urgency != 2, "Phrases about urgency.")
        fields["priority"] = _prediction(
            priority(impact, urgency), impact != 3 or urgency != 2, "Impact and urgency matrix."
        )
        fields["short_description"] = _prediction(
            PREFIX.sub("", first.subject).strip(), False, "Email subject."
        )
        return fields

    # -- notes ----------------------------------------------------------------------

    def _note(self, thread: Thread, upto: int) -> StructuredNote:
        inbound = [
            e
            for e in thread.messages[: upto + 1]
            if e.from_address != self.directory.support_address
        ]
        said = [s for e in inbound for s in sentences(core_text(e.body))]
        said = [s for s in said if not s.endswith(",")]  # greetings
        return StructuredNote(
            message_index=upto,
            reported=said[0] if said else "",
            affected=next((s for s in said if SITE_WORDS.search(s) or TEAM_WORDS.search(s)), ""),
            tried=[s for s in said if TRIED_WORDS.search(s)],
            asks=[s for s in said if s.endswith("?")],
            deadline_or_impact=next((s for s in said if DEADLINE_WORDS.search(s)), None),
            open_questions=[],
        )

    # -- the whole thread -------------------------------------------------------------

    def process(self, thread: Thread, now: datetime) -> ThreadResult:
        d = self.directory
        changes: list[FieldChange] = []
        notes: list[StructuredNote] = []
        chases: list[ChaseAssessment] = []
        alerts: list[Alert] = []
        current: dict[str, FieldPrediction] = {}

        for index, email in enumerate(thread.messages):
            if email.from_address == d.support_address:
                continue
            fields = self._fields(thread, index)
            for name, prediction in fields.items():
                if name not in current or current[name].value != prediction.value:
                    changes.append(
                        FieldChange(
                            message_index=index,
                            field=name,
                            value=prediction.value,
                            reason=prediction.reason,
                        )
                    )
            current = fields
            notes.append(self._note(thread, index))
            if index > 0:
                is_chase = bool(CHASE_WORDS.search(core_text(email.body)))
                chases.append(
                    ChaseAssessment(
                        message_index=index,
                        is_chase=is_chase,
                        confidence=0.6 if is_chase else 0.4,
                        reason="Chasing keyword." if is_chase else "No chasing keyword.",
                    )
                )
                if is_chase:
                    alerts.append(
                        Alert(
                            kind="chase",
                            message_index=index,
                            at=_sent_at(email.date),
                            reason="Client appears to be chasing.",
                        )
                    )

        first_at = _sent_at(thread.messages[0].date)
        replied = any(e.from_address == d.support_address for e in thread.messages)
        target = d.response_targets_minutes[str(current["priority"].value)]
        deadline = first_at + timedelta(minutes=target)
        if not replied and now > deadline:
            alerts.append(
                Alert(
                    kind="silence",
                    at=deadline,
                    reason=f"No response within the {target} minute target.",
                )
            )

        return ThreadResult(
            thread_id=thread.thread_id,
            system=self.name,
            fields=current,
            field_changes=changes,
            notes=notes,
            chase_assessments=chases,
            alerts=alerts,
        )


def _sent_at(date_header: str) -> datetime:
    from email.utils import parsedate_to_datetime

    return parsedate_to_datetime(date_header)
