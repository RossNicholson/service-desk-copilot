"""Turns evaluation runs into a Markdown table and a JSON report."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from copilot.evaluation.harness import SystemRun

# Metrics where lower is better, so the comparison column reads the right way.
LOWER_IS_BETTER = (
    "fields.flagged_for_review",
    "notes.unexpected_questions_per_thread",
    "chasing.false_alarm_rate",
    "pii.leakage",
    "cost.",
    "run.errors",
    "suggestions.unsafe_drafts",
    "suggestions.dropped_rate",
)

SECTIONS = [
    ("Ticket fields", "fields."),
    ("Prompt injection threads", "adversarial."),
    ("Notes", "notes."),
    ("Check-ins", "checkins."),
    ("Chase detection", "chasing."),
    ("Alerts", "alerts."),
    ("Suggestion emails", "suggestions."),
    ("PII leakage", "pii."),
    ("Cost", "cost."),
    ("Run", "run."),
]


def _fmt(name: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    if name.startswith("cost.usd"):
        return f"${value:.4f}"
    if name.endswith(("expected_updates", "threads_sent_to_model", "errors", "unsafe_drafts")) or (
        name.startswith("cost.") and not name.startswith("cost.usd")
    ):
        return f"{value:g}"
    if name.endswith("per_thread"):
        return f"{value:.2f}"
    return f"{value:.1%}"


def markdown(split: str, runs: list[SystemRun], count: int, redaction: dict | None = None) -> str:
    names = [r.system for r in runs]
    lines = [f"## Evaluation: {split} split, {count} threads", ""]
    header = "| Metric | " + " | ".join(names) + " |"
    if len(runs) > 1:
        header += " vs baseline |"
    lines.append(header)
    lines.append("|---" * (len(names) + 1 + (len(runs) > 1)) + "|")
    baseline = next((r for r in runs if r.system == "baseline"), runs[0])
    keys = list(runs[0].metrics)
    for title, prefix in SECTIONS:
        section = [k for k in keys if k.startswith(prefix)]
        if not section:
            continue
        lines.append(f"| **{title}** |" + " |" * (len(names) + (len(runs) > 1)))
        for key in section:
            cells = [_fmt(key, r.metrics.get(key)) for r in runs]
            row = f"| {key.split('.', 1)[1].replace('_', ' ')} | " + " | ".join(cells) + " |"
            if len(runs) > 1:
                row += f" {_delta(key, runs[-1].metrics.get(key), baseline.metrics.get(key))} |"
            lines.append(row)
    lines.append("")
    if redaction:
        lines += [
            "### Redaction",
            "",
            "The redactor on its own, over every email in the split. Every model call "
            "goes through it.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for key, value in redaction.items():
            label = key.split(".", 1)[1].replace("_", " ")
            shown = f"{value:.3f}s" if key.endswith("seconds_per_thread") and value else None
            lines.append(f"| {label} | {shown or _fmt(key, value)} |")
        lines.append("")
    lines.append(
        "Note scores are lexical proxies (word overlap with the labels and the emails), "
        "except the judge rows, which a model grades when the harness runs with --judge."
    )
    for run in runs:
        if run.errors:
            lines.append(
                f"\n{run.system} failed on {len(run.errors)} threads, first: {run.errors[0]}"
            )
    return "\n".join(lines) + "\n"


def _delta(key: str, value: float | None, base: float | None) -> str:
    if value is None or base is None:
        return ""
    diff = value - base
    if abs(diff) < 1e-9:
        return "same"
    better = diff < 0 if key.startswith(LOWER_IS_BETTER) else diff > 0
    shown = f"{diff:+.4f}" if key.startswith("cost.usd") else f"{diff * 100:+.1f} pts"
    if not key.startswith(
        (
            "cost.",
            "fields.",
            "notes.",
            "checkins.",
            "chasing.",
            "alerts.",
            "pii.leakage",
            "adversarial.",
            "suggestions.",
        )
    ) or key.endswith("unsafe_drafts"):
        shown = f"{diff:+g}"
    return f"{shown} {'better' if better else 'worse'}"


def write_reports(
    out_dir: Path, split: str, runs: list[SystemRun], count: int, redaction: dict | None = None
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "split": split,
        "threads": count,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "systems": {
            r.system: {
                "metrics": r.metrics,
                "model": next((x.usage.model for x in r.results if x.usage.model), None),
                "prompt_version": next(
                    (x.usage.prompt_version for x in r.results if x.usage.prompt_version), None
                ),
                "judged": r.judged,
                "seconds": round(r.seconds, 2),
                "errors": r.errors,
            }
            for r in runs
        },
        "redaction": redaction,
    }
    json_path = out_dir / f"eval-{split}.json"
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    (out_dir / f"eval-{split}.md").write_text(markdown(split, runs, count, redaction))
    results_path = out_dir / f"results-{split}.jsonl"
    with results_path.open("w", encoding="utf-8") as handle:
        for run in runs:
            for result in run.results:
                handle.write(result.model_dump_json() + "\n")
    return json_path
