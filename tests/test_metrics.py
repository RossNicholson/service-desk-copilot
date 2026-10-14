"""The metrics give full marks to a perfect system and none to an empty one."""

import pytest

from copilot.evaluation.dataset import load_split
from copilot.evaluation.metrics import all_metrics
from copilot.schemas import (
    FIELD_NAMES,
    Alert,
    ChaseAssessment,
    FieldChange,
    FieldPrediction,
    StructuredNote,
    ThreadResult,
)

SUPPORT = "support@northgate-ms.example"


def oracle(lt, model_inputs=()):
    labels = lt.labels
    fields = {
        name: FieldPrediction(
            value=getattr(labels.fields, name), confidence=1, reason="label", needs_review=False
        )
        for name in FIELD_NAMES
    }
    changes = [
        FieldChange(message_index=m.index, field=k, value=v, reason="label")
        for m in labels.messages
        for k, v in m.field_updates.items()
    ]
    note = labels.note
    notes = [
        StructuredNote(
            message_index=len(labels.messages) - 1,
            reported=note.reported,
            affected=note.affected,
            tried=note.tried,
            asks=[*note.asks, *note.details],
            deadline_or_impact=note.deadline_or_impact,
            open_questions=note.open_questions,
        )
    ]
    chases = [
        ChaseAssessment(message_index=m.index, is_chase=m.is_chase, confidence=1, reason="label")
        for m in labels.messages
        if m.direction == "inbound" and m.index > 0
    ]
    alerts = [
        Alert(kind=a.kind, message_index=a.message_index, at=a.at, reason="label")
        for a in labels.expected_alerts
    ]
    return ThreadResult(
        thread_id=lt.thread.thread_id,
        system="oracle",
        fields=fields,
        field_changes=changes,
        notes=notes,
        chase_assessments=chases,
        alerts=alerts,
        model_inputs=list(model_inputs),
    )


@pytest.fixture(scope="module")
def dev():
    return load_split("dev")


def test_perfect_system_scores_full_marks(dev):
    metrics = all_metrics([(lt, oracle(lt)) for lt in dev], SUPPORT)
    for key in (
        "fields.overall",
        "fields.unflagged_accuracy",
        "notes.completeness",
        "notes.open_questions_recall",
        "checkins.update_recall",
        "checkins.update_precision",
        "chasing.precision",
        "chasing.recall",
        "alerts.precision",
        "alerts.recall",
    ):
        assert metrics[key] == pytest.approx(1.0), key
    assert metrics["chasing.false_alarm_rate"] == 0
    assert metrics["fields.flagged_for_review"] == 0


def test_empty_system_scores_nothing(dev):
    empty = [(lt, ThreadResult(thread_id=lt.thread.thread_id, system="empty", fields={}))
             for lt in dev]  # fmt: skip
    metrics = all_metrics(empty, SUPPORT)
    assert metrics["fields.overall"] == 0
    assert metrics["notes.completeness"] == 0
    assert metrics["alerts.recall"] == 0
    assert metrics["checkins.update_recall"] == 0


def test_pii_leakage_counts_raw_text_sent_to_a_model(dev):
    raw = [(lt, oracle(lt, [e.body for e in lt.thread.messages])) for lt in dev]
    assert all_metrics(raw, SUPPORT)["pii.leakage_rate"] > 0.9
    redacted = [(lt, oracle(lt, ["[PERSON] reports the printer is offline."])) for lt in dev]
    assert all_metrics(redacted, SUPPORT)["pii.leakage_rate"] == 0


def test_invented_note_content_lowers_faithfulness(dev):
    pairs = []
    for lt in dev[:30]:
        result = oracle(lt)
        result.notes[0].tried.append("Replaced the motherboard and reinstalled Windows.")
        pairs.append((lt, result))
    assert all_metrics(pairs, SUPPORT)["notes.faithfulness"] < 1
