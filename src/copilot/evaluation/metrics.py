"""Metrics for scoring a system's results against the labels.

Each function takes the labelled threads and the system's results (in the same
order) and returns a flat dict of named numbers, so reports can line systems up
side by side.

Note scoring is lexical for now: a fact counts as covered when most of its
content words appear in the note, and a note item counts as supported when most
of its content words appear in the emails. That catches missing facts and
invented details cheaply; `judge.py` adds a model-graded check alongside it.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from statistics import mean

from copilot.evaluation.dataset import LabelledThread
from copilot.schemas import FIELD_NAMES, ThreadResult
from copilot.text import core_text, f1, forwarded_text, recall, tokens

SHORT_DESCRIPTION_MATCH = 0.5  # token F1 that counts as the same meaning
FACT_COVERED = 0.6  # share of a fact's words the note must contain
ITEM_SUPPORTED = 0.8  # share of a note item's words the emails must contain
QUESTION_MATCH = 0.5  # share of an expected question's words a predicted one must share
SILENCE_TOLERANCE_MINUTES = 15

Pairs = Sequence[tuple[LabelledThread, ThreadResult]]


def _rate(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _norm(value) -> str:
    return str(value).strip().lower() if value is not None else ""


def field_correct(name: str, expected, predicted) -> bool:
    if name == "short_description":
        return f1(str(expected), str(predicted or "")) >= SHORT_DESCRIPTION_MATCH
    return _norm(expected) == _norm(predicted)


# ---------------------------------------------------------------------------
# Field accuracy
# ---------------------------------------------------------------------------


def field_metrics(pairs: Pairs) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    correct_all, flagged, unflagged_correct, unflagged = 0, 0, 0, 0
    for name in FIELD_NAMES:
        hits = 0
        for lt, result in pairs:
            prediction = result.fields.get(name)
            expected = getattr(lt.labels.fields, name)
            ok = prediction is not None and field_correct(name, expected, prediction.value)
            hits += ok
            if prediction is not None and prediction.needs_review:
                flagged += 1
            else:
                unflagged += 1
                unflagged_correct += ok
        out[f"fields.{name}"] = _rate(hits, len(pairs))
        correct_all += hits
    total = len(pairs) * len(FIELD_NAMES)
    out["fields.overall"] = _rate(correct_all, total)
    out["fields.flagged_for_review"] = _rate(flagged, total)
    # How often a value the system did not flag is right: what an engineer can trust.
    out["fields.unflagged_accuracy"] = _rate(unflagged_correct, unflagged)
    scores = [
        f1(lt.labels.fields.short_description, str(r.fields["short_description"].value or ""))
        for lt, r in pairs
        if "short_description" in r.fields
    ]
    out["fields.short_description_f1"] = mean(scores) if scores else 0.0
    return out


def adversarial_metrics(pairs: Pairs) -> dict[str, float | None]:
    """Field accuracy on threads carrying a prompt injection, for comparison with overall."""
    subset = [(lt, r) for lt, r in pairs if lt.labels.traits.adversarial]
    if not subset:
        return {"adversarial.fields_overall": None, "adversarial.priority": None}
    scored = field_metrics(subset)
    return {
        "adversarial.fields_overall": scored["fields.overall"],
        "adversarial.priority": scored["fields.priority"],
        "adversarial.assignment_group": scored["fields.assignment_group"],
    }


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def _note_items(note) -> list[str]:
    items = [note.reported, note.affected, *note.tried, *note.asks]
    if note.deadline_or_impact:
        items.append(note.deadline_or_impact)
    return [i for i in items if i and i.strip()]


def _thread_source(lt: LabelledThread) -> str:
    return "\n".join(f"{e.subject}\n{e.body}" for e in lt.thread.messages)


def _client_text(lt: LabelledThread, support: str) -> str:
    return "\n".join(
        f"{core_text(e.body)}\n{forwarded_text(e.body)}"
        for e in lt.thread.messages
        if e.from_address != support
    )


def note_metrics(pairs: Pairs, support_address: str) -> dict[str, float | None]:
    covered, facts, supported, items, q_hit, q_total, q_extra = 0, 0, 0, 0, 0, 0, 0
    for lt, result in pairs:
        if not result.notes:
            expected = _expected_facts(lt)
            facts += len(expected)
            q_total += len(lt.labels.note.open_questions)
            continue
        note = result.notes[-1]
        note_text = "\n".join(_note_items(note))
        for fact in _expected_facts(lt):
            facts += 1
            covered += recall(fact, note_text) >= FACT_COVERED
        source = _client_text(lt, support_address) or _thread_source(lt)
        for item in _note_items(note):
            items += 1
            supported += recall(item, source) >= ITEM_SUPPORTED
        expected_q = lt.labels.note.open_questions
        q_total += len(expected_q)
        for question in expected_q:
            q_hit += any(recall(question, p) >= QUESTION_MATCH for p in note.open_questions)
        q_extra += sum(
            not any(recall(e, p) >= QUESTION_MATCH for e in expected_q) for p in note.open_questions
        )
    return {
        "notes.completeness": _rate(covered, facts),
        "notes.faithfulness": _rate(supported, items),
        "notes.open_questions_recall": _rate(q_hit, q_total),
        "notes.unexpected_questions_per_thread": _rate(q_extra, len(pairs)),
    }


def _expected_facts(lt: LabelledThread) -> list[str]:
    note = lt.labels.note
    facts = [note.reported, note.affected, *note.tried, *note.asks, *note.details]
    if note.deadline_or_impact:
        facts.append(note.deadline_or_impact)
    # "X only" is the label's way of saying one person; it needs no words in a note.
    return [f for f in facts if f and tokens(f) and not f.endswith(" only")]


# ---------------------------------------------------------------------------
# Check-ins: field updates as new emails arrive
# ---------------------------------------------------------------------------


def checkin_metrics(pairs: Pairs) -> dict[str, float | None]:
    expected_total, hits, predicted_total, predicted_correct = 0, 0, 0, 0
    for lt, result in pairs:
        expected = {
            (m.index, name, _norm(value))
            for m in lt.labels.messages
            for name, value in m.field_updates.items()
        }
        later = [c for c in result.field_changes if c.message_index > 0]
        predicted = {(c.message_index, c.field, _norm(c.value)) for c in later}
        expected_total += len(expected)
        hits += len(expected & predicted)
        predicted_total += len(predicted)
        predicted_correct += len(predicted & expected)
    return {
        "checkins.update_recall": _rate(hits, expected_total),
        # Any change after the first email that the labels do not expect counts as wrong.
        "checkins.update_precision": _rate(predicted_correct, predicted_total),
        "checkins.expected_updates": float(expected_total),
    }


# ---------------------------------------------------------------------------
# Chasing and alerts
# ---------------------------------------------------------------------------


def chase_metrics(pairs: Pairs) -> dict[str, float | None]:
    tp = fp = fn = false_alarm_flagged = false_alarms = 0
    for lt, result in pairs:
        said = {a.message_index: a.is_chase for a in result.chase_assessments}
        for m in lt.labels.messages:
            if m.direction != "inbound" or m.index == 0:
                continue
            flagged = said.get(m.index, False)
            tp += flagged and m.is_chase
            fp += flagged and not m.is_chase
            fn += (not flagged) and m.is_chase
            if m.false_alarm:
                false_alarms += 1
                false_alarm_flagged += flagged
    return {
        "chasing.precision": _rate(tp, tp + fp),
        "chasing.recall": _rate(tp, tp + fn),
        "chasing.false_alarm_rate": _rate(false_alarm_flagged, false_alarms),
    }


def alert_metrics(pairs: Pairs) -> dict[str, float | None]:
    tp = fp = fn = 0
    by_kind = {"chase": [0, 0, 0], "silence": [0, 0, 0]}
    for lt, result in pairs:
        expected = list(lt.labels.expected_alerts)
        unmatched = list(expected)
        for alert in result.alerts:
            match = next((e for e in unmatched if _same_alert(e, alert)), None)
            if match:
                unmatched.remove(match)
                tp += 1
                by_kind[alert.kind][0] += 1
            else:
                fp += 1
                by_kind[alert.kind][1] += 1
        fn += len(unmatched)
        for e in unmatched:
            by_kind[e.kind][2] += 1
    out = {
        "alerts.precision": _rate(tp, tp + fp),
        "alerts.recall": _rate(tp, tp + fn),
    }
    for kind, (k_tp, k_fp, k_fn) in by_kind.items():
        out[f"alerts.{kind}_precision"] = _rate(k_tp, k_tp + k_fp)
        out[f"alerts.{kind}_recall"] = _rate(k_tp, k_tp + k_fn)
    return out


def _same_alert(expected, alert) -> bool:
    if expected.kind != alert.kind:
        return False
    if expected.kind == "chase":
        return expected.message_index == alert.message_index
    gap = abs((expected.at - alert.at).total_seconds()) / 60
    return gap <= SILENCE_TOLERANCE_MINUTES


# ---------------------------------------------------------------------------
# PII leakage and cost
# ---------------------------------------------------------------------------


def pii_metrics(pairs: Pairs) -> dict[str, float | None]:
    """Share of known personal values that reached a model, among threads sent to one."""
    leaked = total = threads_sent = 0
    by_type: dict[str, list[int]] = {}
    for lt, result in pairs:
        if not result.model_inputs:
            continue
        threads_sent += 1
        sent = "\n".join(result.model_inputs).lower()
        for item in lt.labels.pii:
            total += 1
            hit = item.value.lower() in sent
            leaked += hit
            counts = by_type.setdefault(item.type, [0, 0])
            counts[0] += hit
            counts[1] += 1
    out = {
        "pii.threads_sent_to_model": float(threads_sent),
        "pii.leakage_rate": _rate(leaked, total),
    }
    for kind, (hit, n) in sorted(by_type.items()):
        out[f"pii.leakage_{kind}"] = _rate(hit, n)
    return out


def cost_metrics(pairs: Pairs) -> dict[str, float | None]:
    if not pairs:
        return {}
    return {
        "cost.usd_per_ticket": mean(r.usage.cost_usd for _, r in pairs),
        "cost.calls_per_ticket": mean(r.usage.calls for _, r in pairs),
        "cost.tokens_per_ticket": mean(
            r.usage.prompt_tokens + r.usage.completion_tokens for _, r in pairs
        ),
    }


def redaction_metrics(threads: Sequence[LabelledThread]) -> dict[str, float | None]:
    """How well the redactor alone does, on every email of every thread.

    Leakage is the share of labelled personal values still present after
    redaction. Details kept is the share of the labelled facts' words (other than
    personal values) that survive, so over-redaction shows up too.
    """
    from copilot.directory import load_directory
    from copilot.redaction import Redactor

    directory = load_directory()
    leaked = total = kept = words = 0
    by_type: dict[str, list[int]] = {}
    started = time.monotonic()
    for lt in threads:
        redactor = Redactor(directory=directory)
        text = "\n".join(redactor.redact(f"{e.subject}\n{e.body}") for e in lt.thread.messages)
        lowered = text.lower()
        for item in lt.labels.pii:
            hit = item.value.lower() in lowered
            leaked += hit
            total += 1
            counts = by_type.setdefault(item.type, [0, 0])
            counts[0] += hit
            counts[1] += 1
        personal = {t for item in lt.labels.pii for t in tokens(item.value)}
        present = set(tokens(text))
        for fact in _expected_facts(lt):
            for token in set(tokens(fact)) - personal:
                words += 1
                kept += token in present
    seconds = time.monotonic() - started
    out = {
        "redaction.leakage_rate": _rate(leaked, total),
        "redaction.details_kept": _rate(kept, words),
        "redaction.seconds_per_thread": _rate(seconds, len(threads)),
    }
    for kind, (hit, n) in sorted(by_type.items()):
        out[f"redaction.leakage_{kind}"] = _rate(hit, n)
    return out


def all_metrics(pairs: Pairs, support_address: str) -> dict[str, float | None]:
    return {
        **field_metrics(pairs),
        **adversarial_metrics(pairs),
        **note_metrics(pairs, support_address),
        **checkin_metrics(pairs),
        **chase_metrics(pairs),
        **alert_metrics(pairs),
        **pii_metrics(pairs),
        **cost_metrics(pairs),
    }
