"""The held-out test set must only be read by the evaluation harness."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The evaluation harness (Phase 2) will be the one allowed reader.
ALLOWED = {ROOT / "src" / "copilot" / "evaluation"}
# "data/test", or path joins such as DATA / "test" and Path("data", "test").
PATTERN = re.compile(r"""data/test|data["']?\s*[/,]\s*["']test\b""", re.IGNORECASE)


def test_application_code_does_not_reference_the_test_set():
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if any(parent in ALLOWED for parent in path.parents):
            continue
        text = path.read_text(encoding="utf-8")
        if PATTERN.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"these files reference the held-out test set: {offenders}"


def test_pattern_catches_common_spellings():
    for text in ['open("data/test/labels.jsonl")', 'DATA / "test"', 'Path("data", "test")']:
        assert PATTERN.search(text), text
