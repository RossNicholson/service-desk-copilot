"""The copilot end to end: everything it does with one email thread."""

from __future__ import annotations

from datetime import datetime

from copilot.baseline import silence_alert
from copilot.chasing import PROMPT_NAME as CHASE_PROMPT
from copilot.chasing import ChaseDetector, chase_alerts, escalate_silence
from copilot.directory import Directory, load_directory
from copilot.extraction import PROMPT_NAME as EXTRACT_PROMPT
from copilot.extraction import FieldExtractor
from copilot.llm import ModelClient
from copilot.notes import PROMPT_NAME as NOTE_PROMPT
from copilot.notes import NoteWriter
from copilot.redaction import Redactor
from copilot.schemas import (
    ChaseAssessment,
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
    note. Each follow-up from the client is also checked for chasing. Alerts go
    to the assigned engineer and escalate to the team lead by the rules in
    `copilot.chasing`; the silence alert uses the model's priority.
    """

    name = "copilot"

    def __init__(self, client: ModelClient | None = None, directory: Directory | None = None):
        self.directory = directory or load_directory()
        self.model = client or ModelClient()
        self.extractor = FieldExtractor(self.model, self.directory)
        self.notes = NoteWriter(self.model, self.directory)
        self.chases = ChaseDetector(self.model, self.directory)

    def process(self, thread: Thread, now: datetime) -> ThreadResult:
        d = self.directory
        redactor = Redactor(directory=d)
        before = self.model.usage.model_copy()
        inputs_before = len(self.model.inputs)
        current: dict[str, FieldPrediction] = {}
        changes: list[FieldChange] = []
        notes: list[StructuredNote] = []
        assessed: list[tuple[ChaseAssessment, int]] = []
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
            if index > 0:
                assessed.append(self.chases.assess(thread, index, redactor))

        alerts = chase_alerts(thread, assessed, d.support_address)
        priority = current["priority"].value
        silence = silence_alert(thread, priority, now, d)
        if silence:
            target = d.response_targets_minutes[str(priority)]
            alerts.append(escalate_silence(silence, now, target))
        after = self.model.usage
        return ThreadResult(
            thread_id=thread.thread_id,
            system=self.name,
            fields=current,
            field_changes=changes,
            notes=notes,
            chase_assessments=[a for a, _ in assessed],
            alerts=alerts,
            model_inputs=self.model.inputs[inputs_before:],
            usage=Usage(
                provider=after.provider,
                model=after.model,
                prompt_version=f"{EXTRACT_PROMPT}+{NOTE_PROMPT}+{CHASE_PROMPT}",
                calls=after.calls - before.calls,
                prompt_tokens=after.prompt_tokens - before.prompt_tokens,
                completion_tokens=after.completion_tokens - before.completion_tokens,
                cost_usd=after.cost_usd - before.cost_usd,
            ),
        )
