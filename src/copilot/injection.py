"""Spotting emails that try to instruct the AI.

Email is always handled as untrusted data, so an instruction in an email should
change nothing. This check is a second line of defence for the one place the
copilot writes to a client: a thread that looks like it is addressing the AI or
giving it orders gets no suggestion email, and the ticket says why.

It is a deliberately broad set of phrases. A false alarm only costs a skipped
suggestion; a miss is caught by the prompt's rules and the draft check.
"""

from __future__ import annotations

import re

PATTERNS: dict[str, re.Pattern] = {
    "instruction_override": re.compile(
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,30}"
        r"\b(?:instructions?|rules|prompts?|guidelines|polic(?:y|ies))\b",
        re.I,
    ),
    "fake_system": re.compile(
        r"\[\s*system\b|<\s*/?\s*system\s*>|\bsystem (?:notice|prompt|message|override)\b"
        r"|\bnew polic(?:y|ies)\s*:",
        re.I,
    ),
    "addresses_the_ai": re.compile(
        r"\b(?:ai|assistant|copilot|chatbot|bot|language model|llm|automated systems?)"
        r"\s*(?:reading this\s*[,:]?|,|:)\s*(?:please\s+)?"
        r"(?:send|reply|include|paste|treat|mark|close|set|classify|approve|reset|ignore|print|"
        r"output|list|show|give|tell|forward)\b"
        r"|\bnote to (?:automated systems|the ai|ai|any ai|bots?)\b"
        r"|\b(?:if you are|as) an? (?:ai|language model|assistant)\b",
        re.I,
    ),
    "asks_for_secrets": re.compile(
        r"\b(?:your|the) (?:full )?(?:system prompt|configuration|instructions|api key)\b"
        r"|\badmin(?:istrator)? password\b",
        re.I,
    ),
    "fake_engineer": re.compile(
        r"\bpre-?approved (?:change|request)\b|\bclose the ticket without\b"
        r"|\bskip (?:the )?approvals?\b|\bwithout contacting anyone\b",
        re.I,
    ),
}


def suspected_injection(text: str) -> str | None:
    """The name of the first pattern that matches, or None."""
    for name, pattern in PATTERNS.items():
        if pattern.search(text):
            return name
    return None
