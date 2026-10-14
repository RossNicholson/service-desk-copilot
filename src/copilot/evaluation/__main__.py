"""Score systems against the labelled dataset.

    uv run python -m copilot.evaluation                  # every system on the dev split
    uv run python -m copilot.evaluation --split test     # the held-out test split
    uv run python -m copilot.evaluation --system baseline --limit 20

The copilot runs only when a model is configured (LLM_API_KEY, or LLM_API_BASE
for a local model): it costs money, within the SPEND_CAP_USD setting. The
redaction check runs every time, offline.

Prints a Markdown comparison and writes reports/eval-<split>.json and .md, plus
every system's raw results for inspection.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from copilot.evaluation.dataset import load_split
from copilot.evaluation.harness import NEEDS_MODEL, SYSTEMS, available_systems, evaluate
from copilot.evaluation.metrics import redaction_metrics
from copilot.evaluation.report import markdown, write_reports
from copilot.llm import model_configured

ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m copilot.evaluation",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument(
        "--system",
        action="append",
        choices=sorted(SYSTEMS),
        help="System to score (repeatable). Defaults to every one that can run.",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Also grade notes with the model (one extra call per thread per system).",
    )
    parser.add_argument(
        "--no-redaction", action="store_true", help="Skip the offline redaction check."
    )
    parser.add_argument("--limit", type=int, help="Only the first N threads.")
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)

    ready = available_systems()
    systems = args.system or ready
    missing = [s for s in systems if s not in ready]
    if args.judge and not model_configured():
        missing.append("--judge")
    if missing:
        print(
            f"{', '.join(missing)} needs a model. Set LLM_API_KEY (and LLM_MODEL) in .env, "
            "or LLM_API_BASE for a local model. See .env.example.",
            file=sys.stderr,
        )
        return 2
    if not any(s in NEEDS_MODEL for s in systems) and not args.system:
        print("No model configured, so only the baseline runs.", file=sys.stderr)
    judge = None
    if args.judge:
        from copilot.evaluation.judge import NoteJudge

        judge = NoteJudge
    runs = evaluate(args.split, systems, args.limit, judge)
    count = len(runs[0].results) if runs else 0
    redaction = None
    if not args.no_redaction:
        threads = load_split(args.split)
        redaction = redaction_metrics(threads[: args.limit] if args.limit else threads)
    text = markdown(args.split, runs, count, redaction)
    print(text)
    path = write_reports(args.out, args.split, runs, count, redaction)
    print(f"Reports written to {path.parent}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if any(r.errors for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
