"""The copilot end to end: everything it does with one email thread."""

from __future__ import annotations

from datetime import datetime

from copilot.baseline import BaselineCopilot, silence_alert
from copilot.directory import Directory, load_directory
from copilot.extraction import PROMPT_NAME as EXTRACT_PROMPT
from copilot.extraction import FieldExtractor
from copilot.llm import ModelClient
from copilot.notes import PROMPT_NAME as NOTE_PROMPT
from copilot.notes import NoteWriter
from copilot.redaction import Redactor
from copilot.schemas import (
    FieldChange,
    FieldPrediction,
    StructuredNote,
    Thread,
    ThreadResult,
    Usage,
)


class ModelCopilot:
    """The copilot as the harness scores it.

    For each inbound email the model proposes the ticket fields, then writes the
    note. Chase detection and chase alerts still come from the baseline until
    Phase 5; the silence alert uses the model's priority.
    """

    name = "copilot"

    def __init__(self, client: ModelClient | None = None, directory: Directory | None = None):
        self.directory = directory or load_directory()
        self.model = client or ModelClient()
        self.extractor = FieldExtractor(self.model, self.directory)
        self.notes = NoteWriter(self.model, self.directory)
        self.baseline = BaselineCopilot(self.directory)

    def process(self, thread: Thread, now: datetime) -> ThreadResult:
        d = self.directory
        redactor = Redactor(directory=d)
        before = self.model.usage.model_copy()
        inputs_before = len(self.model.inputs)
        current: dict[str, FieldPrediction] = {}
        changes: list[FieldChange] = []
        notes: list[StructuredNote] = []
        raw = None
        for index, email in enumerate(thread.messages):
            if email.from_address == d.support_address:
                continue
            fields, raw = self.extractor.extract(thread, index, redactor, raw)
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
            subcategory = fields["subcategory"].value
            notes.append(self.notes.write(thread, index, redactor, subcategory))

        rules = self.baseline.process(thread, now)
        alerts = [a for a in rules.alerts if a.kind == "chase"]
        silence = silence_alert(thread, current["priority"].value, now, d)
        if silence:
            alerts.append(silence)
        after = self.model.usage
        return ThreadResult(
            thread_id=thread.thread_id,
            system=self.name,
            fields=current,
            field_changes=changes,
            notes=notes,
            chase_assessments=rules.chase_assessments,
            alerts=alerts,
            model_inputs=self.model.inputs[inputs_before:],
            usage=Usage(
                provider=after.provider,
                model=after.model,
                prompt_version=f"{EXTRACT_PROMPT}+{NOTE_PROMPT}",
                calls=after.calls - before.calls,
                prompt_tokens=after.prompt_tokens - before.prompt_tokens,
                completion_tokens=after.completion_tokens - before.completion_tokens,
                cost_usd=after.cost_usd - before.cost_usd,
            ),
        )
