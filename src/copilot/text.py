"""Plain text helpers shared by the baseline and the evaluation metrics."""

from __future__ import annotations

import re

# Lines that start quoted history or a forwarded email in common mail clients.
_HISTORY_MARKERS = re.compile(
    r"^(?:_{10,}|-{5,} ?Forwarded message ?-{5,}|-----Original Message-----|"
    r"On .{5,200} wrote:|From: .+<.+@.+>)\s*$",
    re.MULTILINE,
)
_MOBILE_FOOTER = re.compile(r"^(?:Sent from my .+|Get Outlook for .+|Sent from Outlook for .+)$")
_SIGN_OFFS = re.compile(
    r"^(?:many thanks|kind regards|regards|thanks|thank you|thx|ta|cheers|best|"
    r"thanks so much|really appreciate it|thanks a lot|thanks!|thank you in advance)[,.!]*$",
    re.IGNORECASE,
)

STOPWORDS = frozenset(
    """
    a about above after again all am an and any are as at be been before being below
    between both but by can could did do does doing down during each few for from
    further had has have having he her here hers him his how i if in into is it its
    itself just me more most my myself no nor not now of off on once only or other our
    ours out over own please same she should so some such than that the their them then
    there these they this those through to too under until up very was we were what when
    where which while who whom why will with would you your yours yourself i'm i've it's
    can't don't we're they're i'd i'll you're isn't hasn't haven't won't also get got
    """.split()  # noqa: SIM905
)


def new_text(body: str) -> str:
    """The part of an email the sender wrote now, without quoted history."""
    match = _HISTORY_MARKERS.search(body)
    return body[: match.start()] if match else body


def strip_signature(text: str) -> str:
    """Drop everything from the sign-off (or a mobile footer) onwards."""
    lines = text.strip().split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i > 0 and (_SIGN_OFFS.match(stripped) or _MOBILE_FOOTER.match(stripped)):
            return "\n".join(lines[:i]).strip()
    return text.strip()


def core_text(body: str) -> str:
    """New text with the signature removed: roughly what the sender typed."""
    return strip_signature(new_text(body))


def forwarded_text(body: str) -> str:
    """The text of a forwarded or quoted email inside a body, if there is one."""
    match = _HISTORY_MARKERS.search(body)
    if not match:
        return ""
    rest = body[match.end() :]
    # Skip the header block (From/Sent/To/Subject lines) up to the first blank line.
    parts = rest.split("\n\n", 1)
    return strip_signature(new_text(parts[1])) if len(parts) == 2 else ""


def sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [c.strip() for c in chunks if len(c.strip()) > 2]


def tokens(text: str) -> list[str]:
    """Lower-case content words, without stopwords or punctuation."""
    words = re.findall(r"[a-z0-9][a-z0-9'\-:.]*[a-z0-9]|[a-z0-9]", text.lower())
    return [w for w in words if w not in STOPWORDS]


def recall(reference: str, candidate: str) -> float:
    """Share of the reference's content words that appear in the candidate."""
    ref = set(tokens(reference))
    if not ref:
        return 1.0
    return len(ref & set(tokens(candidate))) / len(ref)


def f1(reference: str, candidate: str) -> float:
    ref, cand = set(tokens(reference)), set(tokens(candidate))
    if not ref or not cand:
        return float(ref == cand)
    overlap = len(ref & cand)
    if not overlap:
        return 0.0
    precision, rec = overlap / len(cand), overlap / len(ref)
    return 2 * precision * rec / (precision + rec)
