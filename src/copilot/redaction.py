"""PII redaction: personal data is replaced with placeholders before any model sees it.

Detection combines three layers, and a span found by any of them is redacted:

1. Microsoft Presidio with spaCy's small English model, for names the other
   layers do not know about (a customer's name in the body, say), plus its email,
   phone, card, IBAN and NHS number recognisers.
2. Custom patterns for UK formats: phone numbers, sort codes, account numbers
   next to the word "account", National Insurance numbers, dates of birth,
   client reference numbers and card numbers.
3. The MSP's directory: every known client contact and engineer, by full name
   and in "Surname, First name" form.

Placeholders are numbered per thread and reused, so the same person is always
[PERSON_1] across every email. The mapping from placeholder to original value
stays in this process; `restore` puts the originals back into model output
before an engineer sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from copilot.directory import Directory, load_directory

_NAME = r"[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?"

# Our own patterns. Each is (entity, regex, group holding the value).
PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("EMAIL", re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+"), 0),
    ("CARD", re.compile(r"(?<!\d)(?:\d{4}[ -]){3}\d{4}(?!\d)|(?<!\d)\d{16}(?!\d)"), 0),
    (
        "PHONE",
        re.compile(
            r"(?<![\d-])(?:\+44\s?(?:\(0\)\s?)?|0)\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}(?![\d-])"
        ),
        0,
    ),
    ("SORT_CODE", re.compile(r"(?<![\d-])\d{2}-\d{2}-\d{2}(?![\d-])"), 0),
    # UK account numbers are eight digits. Any standalone run of eight is redacted,
    # because clients misspell "account" too often for a keyword to be reliable.
    ("ACCOUNT_NUMBER", re.compile(r"(?<![\d-])\d{8}(?![\d-])"), 0),
    ("NI_NUMBER", re.compile(r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"), 0),
    ("DATE_OF_BIRTH", re.compile(r"\b\d{1,2}/\d{1,2}/(?:19|20)\d{2}\b"), 0),
    ("REFERENCE", re.compile(r"\b[A-Z]{3}-\d{6}\b"), 0),
    # Names the small spaCy model tends to miss: after a title, or in phrases about
    # starters, leavers and customers.
    ("PERSON", re.compile(rf"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr)\.? {_NAME}(?: {_NAME})?"), 0),
    (
        "PERSON",
        re.compile(
            rf"(?i:new starter|starter|leaver|customer|colleague|called|named)[,:]?[ \t]+"
            rf"(?:(?i:is)[ \t]+)?({_NAME} {_NAME})"
        ),
        1,
    ),
    (
        "PERSON",
        re.compile(
            rf"\b({_NAME} {_NAME})"
            r"(?= (?:starts|is starting|joins|is joining|is leaving|leaves|has left)\b)"
        ),
        1,
    ),
]

# Presidio entities to keep, mapped to our placeholder names. Email addresses are left
# to our own pattern: Presidio's email recogniser downloads the public suffix list,
# and redaction must never make a network call.
PRESIDIO_ENTITIES = {
    "PERSON": "PERSON",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "CARD",
    "IBAN_CODE": "ACCOUNT_NUMBER",
    "UK_NHS": "NHS_NUMBER",
}
PERSON_MIN_SCORE = 0.6

PLACEHOLDER = re.compile(r"\[([A-Z_]+)_(\d+)\]")

# Quote markers and greetings spaCy includes in a name ("> Hello Owen").
LEADING_NOISE = re.compile(
    r"^[\s>]*(?:(?:hi|hello|helo|hey|dear|morning|afternoon)\b[\s,]*)?", re.I
)

NOT_NAME_PHRASES = [
    "hi hello helo hey dear thanks thank cheers regards best chasing sent from to subject",
    "partner specialist handler lead agent adviser advisor head officer manager director",
    "analyst assistant administrator coordinator executive associate consultant engineer",
    "compliance onboarding merchant claims support savings risk finance settlements people",
    "operations office branch software files team desk service mlro hr it bin m t e",
    "microsoft adobe acrobat pro visio excel word outlook teams sharepoint onedrive",
    "mon tue wed thu fri sat sun monday tuesday wednesday thursday friday saturday sunday",
]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'-]*", text.lower())


@dataclass
class Span:
    start: int
    end: int
    entity: str


@lru_cache
def _analyzer():
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    return AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])


@dataclass
class Redactor:
    """Redacts one thread. Create a new one per thread so numbering starts afresh."""

    directory: Directory = field(default_factory=load_directory)
    use_presidio: bool = True
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original
    _reverse: dict[tuple[str, str], str] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)
    _name_parts: dict[str, str] = field(default_factory=dict)  # "patrick" -> [PERSON_1]

    def __post_init__(self):
        names: set[str] = set()
        for client in self.directory.clients:
            for person in client.people:
                names.add(person.name)
                names.add(f"{person.last_name}, {person.first_name}")
        for engineer in self.directory.engineers:
            names.add(engineer.name)
        ordered = sorted(names, key=len, reverse=True)
        self._known_names = re.compile("|".join(re.escape(n) for n in ordered)) if ordered else None
        # Words spaCy's small model sometimes tags as names but which are not: job
        # titles, team and site names, products, greetings and sign-offs. A span made
        # only of these is kept. Anything else it tags as a person is redacted.
        phrases = [self.directory.msp_name, *NOT_NAME_PHRASES]
        for client in self.directory.clients:
            phrases += [client.name, *client.sites, *client.services.values()]
            phrases += [person.role for person in client.people]
        phrases += [team.name for team in self.directory.teams]
        self._not_names = {w for phrase in phrases for w in _words(phrase)}

    # -- detection --------------------------------------------------------------------

    def _spans(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for entity, pattern, group in PATTERNS:
            for match in pattern.finditer(text):
                words = _words(match.group(group))
                if entity == "PERSON" and all(w in self._not_names for w in words):
                    continue  # "Customer Support Lead" is a job title, not a customer
                spans.append(Span(match.start(group), match.end(group), entity))
        if self._known_names:
            for match in self._known_names.finditer(text):
                spans.append(Span(match.start(), match.end(), "PERSON"))
        if self.use_presidio and text.strip():
            for result in _analyzer().analyze(
                text=text, language="en", entities=list(PRESIDIO_ENTITIES)
            ):
                value = text[result.start : result.end]
                if result.entity_type == "PERSON" and result.score < PERSON_MIN_SCORE:
                    continue
                entity = PRESIDIO_ENTITIES[result.entity_type]
                # spaCy sometimes runs a name across a line break ("Patrick\n\nPatrick
                # Bailey"), so treat each line as its own span.
                offset = result.start
                for line in value.split("\n"):
                    span = self._trim(line, offset) if entity == "PERSON" else None
                    if entity != "PERSON" and line.strip():
                        start = offset + line.index(line.strip())
                        span = Span(start, start + len(line.strip()), entity)
                    if span:
                        spans.append(span)
                    offset += len(line) + 1
        return _resolve(spans)

    def _trim(self, line: str, offset: int) -> Span | None:
        """Narrow a person span to the name itself, or drop it if there is no name."""
        match = LEADING_NOISE.match(line)
        start = match.end() if match else 0
        name = line[start:].rstrip(" ,.:;")
        words = _words(name)
        if not words or all(w in self._not_names for w in words) or PLACEHOLDER.search(name):
            return None
        return Span(offset + start, offset + start + len(name), "PERSON")

    # -- replacement ------------------------------------------------------------------

    def _key(self, entity: str, value: str) -> tuple[str, str]:
        if entity == "PERSON":
            # "Bailey, Patrick" and "Patrick Bailey" are the same person.
            return (entity, " ".join(sorted(_words(value))))
        return (entity, value.strip())

    def _placeholder(self, entity: str, value: str) -> str:
        key = self._key(entity, value)
        words = _words(value)
        # A first name or surname alone belongs to a full name seen earlier.
        single = entity == "PERSON" and len(words) == 1
        if key not in self._reverse and single and words[0] in self._name_parts:
            return self._name_parts[words[0]]
        if key not in self._reverse:
            self._counts[entity] = self._counts.get(entity, 0) + 1
            placeholder = f"[{entity}_{self._counts[entity]}]"
            self._reverse[key] = placeholder
            self.mapping[placeholder] = value.strip()
        placeholder = self._reverse[key]
        if entity == "PERSON" and len(words) > 1:
            for word in words:
                if len(word) > 2 and word not in self._not_names:
                    self._name_parts.setdefault(word, placeholder)
        return placeholder

    def redact(self, text: str) -> str:
        # Text that already looks like a placeholder is defanged, so an email cannot
        # get a real value restored in its place.
        text = PLACEHOLDER.sub(lambda m: f"({m.group(1)} {m.group(2)})", text)
        spans = self._spans(text)
        # Register full names first, so "Patrick" above "Patrick Bailey" shares its number.
        people = [text[s.start : s.end] for s in spans if s.entity == "PERSON"]
        for value in sorted(people, key=lambda v: -len(_words(v))):
            self._placeholder("PERSON", value)
        # Now catch first names and surnames on their own ("Thanks, Patrick").
        if self._name_parts:
            parts = sorted(self._name_parts, key=len, reverse=True)
            pattern = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in parts) + r")\b", re.I)
            extra = [Span(m.start(), m.end(), "PERSON") for m in pattern.finditer(text)]
            spans = _resolve(spans + extra)
        out, last = [], 0
        for span in spans:
            out.append(text[last : span.start])
            out.append(self._placeholder(span.entity, text[span.start : span.end]))
            last = span.end
        out.append(text[last:])
        return "".join(out)

    def restore(self, text: str) -> str:
        """Put original values back into text a model produced from redacted input."""
        return PLACEHOLDER.sub(lambda m: self.mapping.get(m.group(0), m.group(0)), text)


def _resolve(spans: list[Span]) -> list[Span]:
    """Drop overlapping spans, keeping the longest (then the earliest)."""
    chosen: list[Span] = []
    for span in sorted(spans, key=lambda s: (-(s.end - s.start), s.start)):
        if all(span.end <= c.start or span.start >= c.end for c in chosen):
            chosen.append(span)
    return sorted(chosen, key=lambda s: s.start)
