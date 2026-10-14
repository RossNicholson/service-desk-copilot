"""Score systems against the labelled dataset.

    uv run python -m copilot.evaluation                  # every system on the dev split
    uv run python -m copilot.evaluation --split test     # the held-out test split
    uv run python -m copilot.evaluation --system baseline --limit 20

Prints a Markdown comparison and writes reports/eval-<split>.json and .md, plus
every system's raw results for inspection.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from copilot.evaluation.harness import SYSTEMS, evaluate
from copilot.evaluation.report import markdown, write_reports

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
        help="System to score (repeatable). Defaults to all, with the baseline first.",
    )
    parser.add_argument("--limit", type=int, help="Only the first N threads.")
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)

    systems = args.system or ["baseline", *sorted(s for s in SYSTEMS if s != "baseline")]
    runs = evaluate(args.split, systems, args.limit)
    count = len(runs[0].results) if runs else 0
    text = markdown(args.split, runs, count)
    print(text)
    path = write_reports(args.out, args.split, runs, count)
    print(f"Reports written to {path.parent}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if any(r.errors for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
