"""Model renderer: writes each email's core text with your own model via LiteLLM.

Uses the copilot's settings (LLM_MODEL, LLM_API_KEY, LLM_API_BASE), so any
LiteLLM provider works. The renderer is told the facts each email must carry
and nothing else; the output is checked for those facts, and the call retried
if any are missing. Spending stops at the cap.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from copilot.config import get_settings
from datagen.plan import MessagePlan, ThreadPlan
from datagen.render_template import Rendered
from datagen.validate import DISTINCTIVE, EM_DASH
from datagen.world import DATAGEN, World

PROMPT_PATH = DATAGEN / "prompts" / "render_thread.v1.md"
PROMPT_VERSION = "render_thread.v1"
MAX_ATTEMPTS = 3


class SpendCapReached(RuntimeError):
    pass


class RenderedMessage(BaseModel):
    index: int
    body: str


class RenderedThread(BaseModel):
    messages: list[RenderedMessage]
    forwarded_original: str | None = None


def load_prompt(path: Path = PROMPT_PATH) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    return {k: v.strip() for k, v in sections.items()}


class ModelRenderer:
    name = "model"
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        world: World,
        seed: int,
        max_cost: float | None = None,
        completion: Callable | None = None,
    ):
        settings = get_settings()
        self.world = world
        self.seed = seed
        self.model = settings.llm_model
        self.api_key = settings.llm_api_key
        self.api_base = settings.llm_api_base
        self.max_cost = settings.spend_cap_usd if max_cost is None else max_cost
        self.min_interval = 60 / settings.rate_limit_per_minute
        self.prompt = load_prompt()
        self.cost_usd = 0.0
        self._last_call = 0.0
        if completion is None:
            import litellm

            completion = litellm.completion
        self._completion = completion

    # -- prompt building ------------------------------------------------------------

    def _people(self, plan: ThreadPlan):
        client = self.world.client(plan.client_id)
        people = {p.id: p for p in client.people}
        engineers = {e.id: e for e in self.world.msp.engineers}
        return client, people, engineers

    def _facts(self, plan: ThreadPlan, m: MessagePlan) -> list[str]:
        if m.kind == "initial" and not plan.traits.forwarded:
            facts = [plan.problem]
            if plan.scope_text:
                facts.append(plan.scope_text)
            facts += [f.text for f in m.facts] + plan.tried
            if plan.urgency_text:
                facts.append(plan.urgency_text)
            facts += plan.asks
            facts += [f"A second, unrelated issue: {s.problem}" for s in plan.secondary]
            return facts
        if m.kind == "initial":
            return ["Forwarding the email below from a colleague and asking the desk to help."]
        if m.kind == "ack":
            return ["Thanks the sender and says the request has been logged with the service desk."]
        if m.kind in ("chase", "resend"):
            levels = {
                1: "a polite nudge",
                2: "clearly impatient",
                3: "angry and asking to escalate",
            }
            how = (
                "resending the earlier email"
                if m.kind == "resend"
                else levels[max(1, m.frustration)]
            )
            return [f"Chasing for an update, {how}. No new facts."]
        if m.kind == "false_alarm":
            return [
                "Explicitly says this is not urgent and not a chase (for example 'no rush').",
                *[f.text for f in m.facts],
            ]
        if m.kind == "info":
            return ["Adds a detail they forgot.", *[f.text for f in m.facts]]
        return [f"Says the problem has got worse: {m.scope_change}"]

    def _messages_block(self, plan: ThreadPlan) -> str:
        _, people, engineers = self._people(plan)
        blocks = []
        if plan.traits.forwarded:
            caller = people[plan.caller_id]
            facts = self._facts(plan.model_copy(update={"traits": plan.traits.model_copy(
                update={"forwarded": False})}), plan.messages[0])  # fmt: skip
            blocks.append(
                "Forwarded original (return it as forwarded_original), written by "
                f"{caller.name}, {caller.role}, tone {caller.persona.tone.value}, to "
                f"{people[plan.reported_by_id].first_name}:\n"
                + "\n".join(f"- {f}" for f in facts)
                + self._verbatim(plan.messages[0])
            )
        for m in plan.messages:
            if m.direction == "outbound":
                sender = f"{engineers[m.sender_id].name}, Northgate Service Desk"
            else:
                person = people[m.sender_id]
                sender = f"{person.name}, {person.role}, tone {person.persona.tone.value}"
            when = m.sent_at.strftime("%A %H:%M")
            facts = "\n".join(f"- {f}" for f in self._facts(plan, m))
            verbatim = "" if (m.kind == "initial" and plan.traits.forwarded) else self._verbatim(m)
            blocks.append(
                f"Message {m.index} ({m.direction}, {when}) from {sender}:\n{facts}{verbatim}"
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _verbatim(m: MessagePlan) -> str:
        extra = []
        if m.injection_text and m.injection_type != "signature_payload":
            extra.append(m.injection_text)
        if m.pii_text:
            extra.append(m.pii_text)
        return "".join(f'\n- verbatim: "{t}"' for t in extra)

    def _required_tokens(self, plan: ThreadPlan) -> list[str]:
        texts = [f.text for f in plan.details_stated] + [plan.problem]
        texts += [m.injection_text for m in plan.messages if m.injection_text]
        texts += [m.pii_text for m in plan.messages if m.pii_text]
        texts = [t for t in texts if t]
        return sorted({tok for t in texts for tok in DISTINCTIVE.findall(t)})

    # -- calling the model ----------------------------------------------------------

    def _call(self, system: str, user: str) -> RenderedThread:
        if self.cost_usd >= self.max_cost:
            raise SpendCapReached(f"spend cap of ${self.max_cost:.2f} reached")
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        kwargs = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key.get_secret_value()
        if self.api_base:
            kwargs["api_base"] = self.api_base
        response = self._completion(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=RenderedThread,
            temperature=0.9,
            **kwargs,
        )
        self.cost_usd += _cost(response)
        content = response.choices[0].message.content
        return RenderedThread.model_validate(json.loads(content))

    def render(self, plan: ThreadPlan) -> Rendered:
        client, _, _ = self._people(plan)
        user = self.prompt["user"].format(
            client_name=client.name,
            client_sector=client.sector,
            subject=plan.subject,
            messages=self._messages_block(plan),
        )
        required = self._required_tokens(plan)
        problems: list[str] = []
        for _ in range(MAX_ATTEMPTS):
            try:
                result = self._call(self.prompt["system"], user)
            except (ValidationError, json.JSONDecodeError) as error:
                problems = [f"unparseable output: {error}"]
                continue
            bodies = {m.index: m.body.replace(EM_DASH, ", ") for m in result.messages}
            if sorted(bodies) != [m.index for m in plan.messages]:
                problems = ["wrong number of messages"]
                continue
            original = result.forwarded_original if plan.traits.forwarded else None
            if plan.traits.forwarded and not original:
                problems = ["missing forwarded original"]
                continue
            text = "\n".join([*bodies.values(), original or ""])
            problems = [f"missing {tok!r}" for tok in required if tok not in text]
            if not problems:
                return Rendered(bodies=[bodies[m.index] for m in plan.messages], original=original)
        raise RuntimeError(f"{plan.thread_id}: model output failed checks: {problems}")


def _cost(response) -> float:
    try:
        import litellm

        return float(litellm.completion_cost(completion_response=response) or 0)
    except Exception:
        return 0.0
