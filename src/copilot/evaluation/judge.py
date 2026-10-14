"""A model-graded check of notes, alongside the lexical scores in metrics.py.

The judge reads the redacted thread, the labelled facts and the note, and says
which facts the note conveys and which note items the emails do not support.
It paraphrase-matches where the lexical scores cannot, at the cost of a model
call per thread. Its own cost is reported separately from the system's.
"""

from __future__ import annotations

from pydantic import BaseModel

from copilot.directory import load_directory
from copilot.evaluation.dataset import LabelledThread
from copilot.evaluation.metrics import _expected_facts, _note_items
from copilot.llm import ModelClient, Redacted, email_block, load_prompt
from copilot.redaction import Redactor
from copilot.schemas import ThreadResult

PROMPT_NAME = "judge_note.v1"


class Verdict(BaseModel):
    covered_facts: list[int] = []
    unsupported_items: list[int] = []


class NoteJudge:
    def __init__(self, client: ModelClient | None = None):
        self.model = client or ModelClient()
        self.directory = load_directory()
        self.prompt = load_prompt(PROMPT_NAME)

    def judge(self, lt: LabelledThread, result: ThreadResult) -> tuple[int, int, int, int]:
        """(facts covered, facts, items supported, items) for one thread."""
        facts = _expected_facts(lt)
        items = _note_items(result.notes[-1]) if result.notes else []
        if not facts and not items:
            return 0, 0, 0, 0
        if not items:
            return 0, len(facts), 0, 0
        d = self.directory
        redactor = Redactor(directory=d)
        nonce = self.model.new_nonce()
        blocks = []
        for index, email in enumerate(lt.thread.messages):
            who = "service desk" if email.from_address == d.support_address else "client"
            body = Redacted(redactor.redact(email.body))
            blocks.append(email_block(nonce, f"Message {index} from the {who}", body))
        number = lambda xs: "\n".join(f"{i}. {redactor.redact(x)}" for i, x in enumerate(xs, 1))  # noqa: E731
        user = self.prompt["user"].format(
            facts=number(facts) or "(none)", items=number(items), emails="\n\n".join(blocks)
        )
        verdict = self.model.complete(
            system=self.prompt["system"],
            user=Redacted(user),
            schema=Verdict,
            prompt_version=PROMPT_NAME,
            nonce=nonce,
        )
        covered = len({n for n in verdict.covered_facts if 1 <= n <= len(facts)})
        unsupported = len({n for n in verdict.unsupported_items if 1 <= n <= len(items)})
        return covered, len(facts), len(items) - unsupported, len(items)


def judge_metrics(pairs, judge: NoteJudge) -> dict[str, float | None]:
    covered = facts = supported = items = 0
    for lt, result in pairs:
        c, f, s, i = judge.judge(lt, result)
        covered, facts, supported, items = covered + c, facts + f, supported + s, items + i
    return {
        "notes.judge_completeness": covered / facts if facts else None,
        "notes.judge_faithfulness": supported / items if items else None,
        "cost.usd_judge_total": judge.model.usage.cost_usd,
    }
