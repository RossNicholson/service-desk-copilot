"""Ticket field extraction: one model call per inbound email, on redacted text.

The model is asked only for what needs judgement: who the caller is, which
catalogue entry fits, impact, urgency and a short description. Everything else
is worked out here, so it cannot drift from the MSP's own data:

- the client comes from the sender's email domain when it is a known client;
- category, assignment group and affected service come from the chosen catalogue
  entry and the client's service names;
- priority comes from the impact and urgency matrix.

Every value the model returns is checked against the options it was given. An
answer outside them is dropped and the field is flagged for review, never guessed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from copilot.directory import ClientRecord, Directory, load_directory
from copilot.llm import ModelClient, Redacted, email_block, load_prompt
from copilot.priority import priority
from copilot.redaction import Redactor
from copilot.schemas import FieldPrediction, Thread

PROMPT_NAME = "extract_fields.v1"
REVIEW_BELOW = 0.6  # confidence under which a field is flagged for an engineer


class Guess(BaseModel):
    value: str | int | None
    confidence: float = Field(ge=0, le=1)
    reason: str


class ExtractedFields(BaseModel):
    """What the model returns. Values are placeholders, catalogue numbers or 1 to 3."""

    caller: Guess
    route: Guess
    impact: Guess
    urgency: Guess
    short_description: Guess
    client: Guess | None = None


def _prediction(value, confidence: float, reason: str) -> FieldPrediction:
    confidence = round(min(max(confidence, 0.0), 1.0), 2)
    if value is None:
        confidence = min(confidence, REVIEW_BELOW - 0.01)
    return FieldPrediction(
        value=value,
        confidence=confidence,
        reason=reason.strip().split("\n")[0][:200],
        needs_review=confidence < REVIEW_BELOW,
    )


class FieldExtractor:
    """Proposes ticket fields for a thread, reading it up to a given email."""

    def __init__(self, client: ModelClient, directory: Directory | None = None):
        self.model = client
        self.directory = directory or load_directory()
        self.prompt = load_prompt(PROMPT_NAME)

    # -- building the request ---------------------------------------------------------

    def _client(self, thread: Thread, upto: int) -> ClientRecord | None:
        d = self.directory
        for email in thread.messages[: upto + 1]:
            if email.from_address != d.support_address:
                client = d.client_for_address(email.from_address)
                if client:
                    return client
        return None

    def _people(self, redactor: Redactor, client: ClientRecord | None) -> str:
        lines = []
        for placeholder, value in redactor.mapping.items():
            if not placeholder.startswith("[PERSON_"):
                continue
            contact = self._contact(value, client)
            if contact:
                lines.append(f"- {placeholder}: {contact.role}, a known contact at the client")
            elif self._engineer(value):
                lines.append(f"- {placeholder}: a {self.directory.msp_name} engineer")
            else:
                lines.append(f"- {placeholder}: not in our records")
        return "\n".join(lines) or "- none"

    def _contact(self, value: str, client: ClientRecord | None):
        words = sorted(value.lower().replace(",", " ").split())
        clients = [client] if client else self.directory.clients
        for c in clients:
            for person in c.people:
                if sorted(person.name.lower().split()) == words:
                    return person
        return None

    def _engineer(self, value: str):
        return next((e for e in self.directory.engineers if e.name == value.strip()), None)

    def _catalogue(self, client: ClientRecord | None) -> str:
        lines = []
        for i, route in enumerate(self.directory.routes, start=1):
            service = client.services.get(route.service) if client else route.service
            lines.append(f"{i}. {route.category} / {route.subcategory} (service: {service})")
        return "\n".join(lines)

    def _emails(self, thread: Thread, upto: int, redactor: Redactor, nonce: str) -> list[str]:
        blocks = []
        for index, email in enumerate(thread.messages[: upto + 1]):
            who = (
                f"{self.directory.msp_name} service desk"
                if email.from_address == self.directory.support_address
                else f"{email.from_name} <{email.from_address}>"
            )
            header = f"Message {index}\nFrom: {who}\nDate: {email.date}\nSubject: {email.subject}"
            body = Redacted(redactor.redact(email.body))
            blocks.append(email_block(nonce, redactor.redact(header), body))
        return blocks

    # -- one call -------------------------------------------------------------------

    def extract(
        self,
        thread: Thread,
        upto: int,
        redactor: Redactor,
        previous: ExtractedFields | None = None,
    ) -> tuple[dict[str, FieldPrediction], ExtractedFields]:
        """Fields after reading emails 0 to `upto`, plus the raw answer for the next call."""
        d = self.directory
        client = self._client(thread, upto)
        nonce = self.model.new_nonce()
        emails = self._emails(thread, upto, redactor, nonce)  # fills the redactor's mapping
        if previous:
            # The model's own earlier answer, so it only changes what it has reason to.
            current = "\n".join(
                f"- {name}: {getattr(previous, name).value}"
                for name in ("caller", "route", "impact", "urgency", "short_description")
            )
        else:
            current = "- none yet"
        user = self.prompt["user"].format(
            client=client.name if client else "not known from the sender's address",
            clients=", ".join(c.name for c in d.clients),
            people=self._people(redactor, client),
            catalogue=self._catalogue(client),
            current=current,
            emails="\n\n".join(emails),
        )
        system = self.prompt["system"].format(msp_name=d.msp_name)
        out = self.model.complete(
            system=system,
            user=Redacted(user),
            schema=ExtractedFields,
            prompt_version=PROMPT_NAME,
            nonce=nonce,
        )
        return self._fields(out, client, redactor), out

    # -- checking the answer --------------------------------------------------------

    def _fields(
        self, out: ExtractedFields, client: ClientRecord | None, redactor: Redactor
    ) -> dict[str, FieldPrediction]:
        d = self.directory
        fields: dict[str, FieldPrediction] = {}

        # Client: the sender's domain decides; the model only fills a gap.
        if client:
            fields["client_company"] = _prediction(
                client.name, 0.99, "The sender's email domain belongs to this client."
            )
        else:
            named = next(
                (c for c in d.clients if out.client and c.name == str(out.client.value)), None
            )
            client = named
            guess = out.client or Guess(value=None, confidence=0, reason="No client given.")
            fields["client_company"] = _prediction(
                named.name if named else None,
                min(guess.confidence, 0.7) if named else 0,
                redactor.restore(guess.reason)
                if named
                else "Sender's domain is not a client's and the emails do not say which.",
            )

        fields["caller"] = self._caller(out.caller, client, redactor)

        route_guess = out.route
        route = None
        try:
            number = int(route_guess.value) if route_guess.value is not None else None
        except (TypeError, ValueError):
            number = None
        if number and 1 <= number <= len(d.routes):
            route = d.routes[number - 1]
        reason = redactor.restore(route_guess.reason)
        if route:
            fields["category"] = _prediction(route.category, route_guess.confidence, reason)
            fields["subcategory"] = _prediction(route.subcategory, route_guess.confidence, reason)
            service = client.services.get(route.service) if client else None
            fields["affected_service"] = _prediction(
                service,
                route_guess.confidence if service else 0,
                reason if service else "Client not known, so its service name is not known.",
            )
            fields["assignment_group"] = _prediction(
                d.team_name(route.group),
                route_guess.confidence,
                f"Routing for {route.subcategory}.",
            )
        else:
            for name in ("category", "subcategory", "affected_service"):
                fields[name] = _prediction(None, 0, "No catalogue entry matched.")
            fields["assignment_group"] = _prediction(
                d.team_name("service-desk"), 0.3, "No catalogue entry matched, so the service desk."
            )

        impact = _level(out.impact)
        urgency = _level(out.urgency)
        fields["impact"] = _prediction(
            impact, out.impact.confidence if impact else 0, redactor.restore(out.impact.reason)
        )
        fields["urgency"] = _prediction(
            urgency, out.urgency.confidence if urgency else 0, redactor.restore(out.urgency.reason)
        )
        if impact and urgency:
            fields["priority"] = _prediction(
                priority(impact, urgency),
                min(out.impact.confidence, out.urgency.confidence),
                f"Impact {impact} and urgency {urgency} on the priority matrix.",
            )
        else:
            fields["priority"] = _prediction(None, 0, "Impact or urgency is missing.")

        text = out.short_description.value
        fields["short_description"] = _prediction(
            redactor.restore(str(text)).strip() if text else None,
            out.short_description.confidence,
            redactor.restore(out.short_description.reason),
        )
        return fields

    def _caller(
        self, guess: Guess, client: ClientRecord | None, redactor: Redactor
    ) -> FieldPrediction:
        """The caller is recorded by work email address, like the ticket system does."""
        value = redactor.restore(str(guess.value)) if guess.value else None
        reason = redactor.restore(guess.reason)
        if not value or value == str(guess.value):  # missing, or not a placeholder we issued
            return _prediction(None, 0, "The model did not name a person from the emails.")
        if "@" in value:
            owner = next(
                (
                    p
                    for c in self.directory.clients
                    for p in c.people
                    if p.email.lower() == value.lower()
                ),
                None,
            )
            return _prediction(owner.email if owner else value, guess.confidence, reason)
        contact = self._contact(value, client) or self._contact(value, None)
        if contact:
            return _prediction(contact.email, guess.confidence, reason)
        return _prediction(value, min(guess.confidence, 0.5), f"{reason} Not a known contact.")


def _level(guess: Guess) -> int | None:
    try:
        level = int(guess.value) if guess.value is not None else None
    except (TypeError, ValueError):
        return None
    return level if level in (1, 2, 3) else None
